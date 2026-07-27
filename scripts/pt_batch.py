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


def process_one(xlsx, conn, args):
    name = os.path.basename(xlsx)
    if pt_db.file_done(conn, name):
        if args.mode == "skip":
            print(f"[跳过] {name}（已入库）"); return "skip"
        if args.mode == "ask":
            if input(f"{name} 已入库，覆盖? [y/N] ").strip().lower() != "y":
                print("  跳过"); return "skip"

    # 写库的 model 元数据按后端定，防止 claude/codex 路线被误标成 gemini
    model_label = {"claude": "claude-sonnet-4-6", "codex": "codex-gpt-5.6-terra"}.get(
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
            pt_db.upsert_file(conn, {"file_name": name, "file_size": os.path.getsize(xlsx),
                                     "sheets": 0, "rows": 0, "photos": 0, "record_count": 0,
                                     "model": model_label, "processed_at": time.strftime("%Y-%m-%d %H:%M:%S")})
            conn.commit()
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
        # ③ 入库（覆盖该文件旧记录），并记 file_size
        pt_db.store(conn, rows_json, ocr_dir, model_label, mode="refresh")
        conn.execute("UPDATE files SET file_size=? WHERE file_name=?", (os.path.getsize(xlsx), name))
        conn.commit()
        return "done"
    finally:
        # ④ 删临时（成功才删；失败上面已 return，保留现场）
        if os.path.isdir(tmp):
            shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="*", help="目录或 xlsx 文件")
    ap.add_argument("--db", default="pt_data.sqlite")
    ap.add_argument("--mode", default="skip", choices=["skip", "refresh", "ask"])
    ap.add_argument("--model", default="gemini-cli", help="写库的 model 元数据标签（gemini 后端时生效）")
    ap.add_argument("--backend", choices=["gemini", "claude", "codex"], default="gemini")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--downscale", type=int, default=1024)
    ap.add_argument("--tmp", default="pt_tmp", help="本地临时目录（用完即删）")
    ap.add_argument("--limit", type=int, default=None, help="本次最多处理几个(未入库的)文件，如 10")
    ap.add_argument("--export", default=None, help="处理完顺带导出 CSV 到此路径")
    ap.add_argument("--export-only", default=None, help="不处理，仅导出 CSV")
    ap.add_argument("--where", default=None, help="导出 SQL 条件，如 \"conf='高'\"")
    args = ap.parse_args()

    conn = pt_db.connect(args.db)

    if args.export_only:
        pt_db.export(conn, args.export_only, args.where)
        conn.close(); return

    files = collect_xlsx(args.inputs)
    if args.limit and args.mode == "skip":
        # 增量：只挑还没入库的，取前 N 个（配合“10个10个吃”）
        files = [f for f in files if not pt_db.file_done(conn, os.path.basename(f))][:args.limit]
    elif args.limit:
        files = files[:args.limit]
    print(f"待处理 {len(files)} 个 xlsx，模式={args.mode}，后端={args.backend}\n" + "=" * 60)
    stats = {"done": 0, "skip": 0, "fail": 0, "empty": 0}
    started = time.time()
    for i, xlsx in enumerate(files, 1):
        try:
            stats[process_one(xlsx, conn, args)] += 1
        except Exception as e:
            print(f"  ✗ 异常 {os.path.basename(xlsx)}: {e}")
            stats["fail"] += 1
        if i % 5 == 0 or i == len(files):
            print(f"--- 进度 {i}/{len(files)}  入库{stats['done']} 跳过{stats['skip']} 失败{stats['fail']} ---")

    print("=" * 60)
    total = conn.execute("SELECT count(*) FROM records").fetchone()[0]
    print(f"完成: 入库{stats['done']} 跳过{stats['skip']} 失败{stats['fail']}, 库内共 {total} 条记录, 耗时 {time.time()-started:.0f}s")
    if args.export:
        pt_db.export(conn, args.export, args.where)
    conn.close()


if __name__ == "__main__":
    main()
