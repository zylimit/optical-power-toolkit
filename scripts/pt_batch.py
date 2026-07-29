"""光功率批处理编排（DB 为唯一持久存储，OCR 中间件用完即删）。

逐文件流水：查DB(跳过已入库) -> 抽取到临时目录 -> Gemini OCR -> 入库 -> 删临时 -> 下一个。
支持增量：加了新文件重跑，默认只吃没入库过的。

    python pt_batch.py onebox_power_test --db pt_data.sqlite            # 增量(默认skip)
    python pt_batch.py onebox_power_test --mode refresh                 # 全部重跑覆盖
    python pt_batch.py "a.xlsx" "b.xlsx" --db pt_data.sqlite            # 指定文件
    python pt_batch.py --db pt_data.sqlite --export-only master.csv     # 只导出

模式：skip(默认,已入库跳过) / refresh(重跑覆盖) / ask(问一下)
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

import pt_db
import pt_upload

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PY = sys.executable
HERE = os.path.dirname(os.path.abspath(__file__))
FILE_BUDGET = 1800  # 单文件总预算(s)：抽取+OCR 共 30 分钟，超了隔离该文件继续下一个


def quarantine(xlsx, stage, elapsed):
    """超时处置：源 xlsx 改名 .skip 隔离。collect_xlsx 只收 *.xlsx，改名后永不再读。"""
    name = os.path.basename(xlsx)
    target = xlsx + ".skip"
    if os.path.exists(target):
        print(f"[隔离] {name}: 处理超时({stage}, {elapsed:.0f}s)，{name}.skip 已存在，不再改名")
        return
    try:
        os.rename(xlsx, target)
        print(f"[隔离] {name}: 处理超时({stage}, {elapsed:.0f}s)，已改名 .skip 永不再读")
    except OSError as e:
        print(f"[警告] {name}: 处理超时({stage}, {elapsed:.0f}s)，改名失败({e})，本次跳过")


def collect_xlsx(inputs):
    files = []
    for it in inputs:
        if os.path.isdir(it):
            files += sorted(glob.glob(os.path.join(it, "*.xlsx")))
        elif it.lower().endswith(".xlsx"):
            files.append(it)
    # 跳过 Excel 临时锁文件 ~$
    return [f for f in files if not os.path.basename(f).startswith("~$")]


def _already_done(ctx, name, xlsx, args):
    """增量判断：本地库按文件名查 files 表；上传模式按 content_md5 查服务端清单。"""
    if args.local_db:
        return pt_db.file_done(ctx["conn"], name)
    return pt_upload.content_md5(xlsx) in ctx["done_md5"]


def process_one(xlsx, ctx, args):
    name = os.path.basename(xlsx)
    if _already_done(ctx, name, xlsx, args):
        if args.mode == "skip":
            print(f"[跳过] {name}（已入库）"); return "skip"
        if args.mode == "ask":
            if input(f"{name} 已入库，覆盖? [y/N] ").strip().lower() != "y":
                print("  跳过"); return "skip"

    # 写库的 model 元数据按后端定，防止 claude/codex 路线被误标成 gemini
    model_label = {"claude": "claude-sonnet-4-6", "codex": "codex-gpt-5.6-terra",
                   "tokenplan": "qwen3.8-max-preview"}.get(
        args.backend, args.model)
    tmp = os.path.join(args.tmp, re.sub(r"[^0-9A-Za-z]+", "_", name)[:60])
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    try:
        # ① 抽取到临时目录（子进程执行：病态 sheet 会把纯正则解析拖死，主进程内无法强杀，
        #    子进程超时可 kill；pt_extract CLI 自带把 rows.json 落到 --out 目录）
        t0 = time.time()
        rows_json = os.path.join(tmp, "rows.json")
        try:
            r = subprocess.run([PY, os.path.join(HERE, "pt_extract.py"), xlsx, "--out", tmp],
                               cwd=HERE, stdout=subprocess.DEVNULL, timeout=FILE_BUDGET)
        except subprocess.TimeoutExpired:
            quarantine(xlsx, "抽取", time.time() - t0)
            return "fail"
        if r.returncode != 0:
            print(f"  ✗ 抽取失败(exit {r.returncode}) {name}，跳过")
            return "fail"
        if not os.path.exists(rows_json):
            print(f"  ✗ 抽取无输出(rows.json 缺失) {name}，跳过")
            return "fail"
        recs = json.load(open(rows_json, encoding="utf-8"))
        photos = sum(1 for rec in recs if rec.get("has_photo"))
        if not recs:
            # 没检测到光功率行：也登记该文件（0 记录），避免每批重试白占名额
            if args.local_db:
                pt_db.upsert_file(ctx["conn"], {"file_name": name, "file_size": os.path.getsize(xlsx),
                                         "sheets": 0, "rows": 0, "photos": 0, "record_count": 0,
                                         "model": model_label, "processed_at": time.strftime("%Y-%m-%d %H:%M:%S")})
                ctx["conn"].commit()
            else:
                # 上传模式：POST 空 records 让服务端登记该文件（去重清单据 content_md5）
                ctx["client"].upload(name, pt_upload.content_md5(xlsx),
                                     os.path.getsize(xlsx), [], {}, model=model_label)
            print(f"[空] {name}: 无光功率行，已登记跳过")
            return "empty"
        print(f"[处理] {name}: {len(recs)} 行, {photos} 图 -> OCR ...")
        # ② OCR 到临时目录（吃剩余预算，最少给 60s）
        ocr_dir = os.path.join(tmp, "ocr")
        remain = max(60, FILE_BUDGET - int(time.time() - t0))
        try:
            r = subprocess.run([PY, os.path.join(HERE, "pt_ocr.py"), "--rows", rows_json,
                                "--out", ocr_dir, "--backend", args.backend,
                                "--workers", str(args.workers), "--downscale", str(args.downscale)],
                               cwd=HERE, timeout=remain)
        except subprocess.TimeoutExpired:
            quarantine(xlsx, "OCR", time.time() - t0)
            return "fail"
        if r.returncode != 0:
            print(f"  ✗ OCR 失败(exit {r.returncode})，跳过入库，保留临时 {tmp}")
            return "fail"
        # ③ 落库：本地 SQLite（--local-db，V1 兼容）或上传服务端（默认）
        if args.local_db:
            pt_db.store(ctx["conn"], rows_json, ocr_dir, model_label, mode="refresh")
            ctx["conn"].execute("UPDATE files SET file_size=? WHERE file_name=?",
                                (os.path.getsize(xlsx), name))
            ctx["conn"].commit()
        else:
            up_recs = pt_upload.build_upload_records(rows_json, ocr_dir, model_label)
            images = pt_upload.collect_images(rows_json)
            md5 = pt_upload.content_md5(xlsx)
            res = ctx["client"].upload(name, md5, os.path.getsize(xlsx),
                                       up_recs, images, model=model_label)
            print(f"  [上传] {name}: {res['records_stored']} 条, {res['images_stored']} 图"
                  + (f", 内容重复于 {res['duplicate_of']}" if res.get("duplicate_of") else ""))
        return "done"
    finally:
        # ④ 删临时（成功才删；失败上面已 return，保留现场）
        if os.path.isdir(tmp):
            shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="*", help="目录或 xlsx 文件")
    ap.add_argument("--db", default="pt_data.sqlite")
    ap.add_argument("--server-url", default="http://localhost:8000",
                    help="服务端地址（上传模式，默认）")
    ap.add_argument("--local-db", action="store_true",
                    help="走 V1 本地 SQLite 落库（默认走上传服务端）")
    ap.add_argument("--mode", default="skip", choices=["skip", "refresh", "ask"])
    ap.add_argument("--model", default="gemini-cli", help="写库的 model 元数据标签（gemini 后端时生效）")
    ap.add_argument("--backend", choices=["gemini", "claude", "codex", "tokenplan"], default="gemini")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--downscale", type=int, default=0,
                    help="OCR送模前缩到该最大边(px)；0=原图不压缩(默认)")
    ap.add_argument("--tmp", default="pt_tmp", help="本地临时目录（用完即删）")
    ap.add_argument("--limit", type=int, default=None, help="本次最多处理几个(未入库的)文件，如 10")
    ap.add_argument("--export", default=None, help="处理完顺带导出 CSV 到此路径")
    ap.add_argument("--export-only", default=None, help="不处理，仅导出 CSV")
    ap.add_argument("--where", default=None, help="导出 SQL 条件，如 \"conf='高'\"")
    args = ap.parse_args()

    # 落库出口：本地 SQLite（--local-db，V1）或上传服务端（默认）。
    # ctx 统一携带两模式各自需要的句柄，process_one 据 args.local_db 分流。
    if args.local_db:
        conn = pt_db.connect(args.db)
        ctx = {"conn": conn}
    else:
        conn = None
        client = pt_upload.ServerClient(args.server_url)
        try:
            done_md5 = client.list_done_md5()
        except Exception as e:
            print(f"✗ 连不上服务端 {args.server_url}：{e}"); return
        ctx = {"client": client, "done_md5": done_md5}

    # --export / --export-only 是本地库能力，仅 --local-db 模式支持
    if args.export_only:
        if not args.local_db:
            print("✗ --export-only 需配合 --local-db（导出读本地 SQLite）"); return
        pt_db.export(conn, args.export_only, args.where)
        conn.close(); return

    files = collect_xlsx(args.inputs)
    if args.limit and args.mode == "skip":
        # 增量：只挑还没入库的，取前 N 个（配合“10个10个吃”）
        files = [f for f in files if not _already_done(ctx, os.path.basename(f), f, args)][:args.limit]
    elif args.limit:
        files = files[:args.limit]
    sink = "本地SQLite" if args.local_db else f"服务端 {args.server_url}"
    print(f"待处理 {len(files)} 个 xlsx，模式={args.mode}，后端={args.backend}，落库={sink}\n" + "=" * 60)
    stats = {"done": 0, "skip": 0, "fail": 0, "empty": 0}
    started = time.time()
    for i, xlsx in enumerate(files, 1):
        try:
            stats[process_one(xlsx, ctx, args)] += 1
        except Exception as e:
            print(f"  ✗ 异常 {os.path.basename(xlsx)}: {e}")
            stats["fail"] += 1
        if i % 5 == 0 or i == len(files):
            print(f"--- 进度 {i}/{len(files)}  入库{stats['done']} 跳过{stats['skip']} 失败{stats['fail']} ---")

    print("=" * 60)
    if args.local_db:
        total = conn.execute("SELECT count(*) FROM records").fetchone()[0]
        print(f"完成: 入库{stats['done']} 跳过{stats['skip']} 失败{stats['fail']}, 库内共 {total} 条记录, 耗时 {time.time()-started:.0f}s")
        if args.export:
            pt_db.export(conn, args.export, args.where)
        conn.close()
    else:
        print(f"完成: 上传{stats['done']} 跳过{stats['skip']} 失败{stats['fail']}, 耗时 {time.time()-started:.0f}s")


if __name__ == "__main__":
    main()
