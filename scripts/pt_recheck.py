"""硬样本 pro 复核流水线：从库里筛硬样本行（有图模糊 / OCR失败 / 标牌糊未核对），
按源文件重抽照片，只对硬样本行用 pro 模型重跑 OCR，再重建该文件全部记录入库。

关键设计：pt_db.store(mode="refresh") 会 DELETE 该文件旧记录后按 OCR 目录整体重建，
所以临时 OCR 目录必须补齐该文件**全部行**的 json——非硬样本行从库里现有字段反查
重建近似 OCR json（reconcile 对这些字段幂等：address 已 clean、street 已标准化、
near_street 置 False 不再重复加前缀、timestamp 回填 test_date 原值，重算结果与
库内一致），硬样本行才是新跑的。两层防线：store 之前先做完整性校验——应有 json 的行
必须都能被 load_ocr 真实扫到，缺行立即中止、该文件跳过、库内保持原样（预防层）；
json 落地但内容本身劣化的残余风险，由覆盖全部行的 worse 变差检测捕获打印，
供人工裁定（检测层兜底）。

    python pt_recheck.py --db pt_data.sqlite --dir onebox_power_test --limit 5
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from pt_extract import extract_file
from pt_merge import load_ocr
from pt_ocr import result_path, BATCH_SIZE, CODEX_MODEL, ocr_batch, ocr_batch_codex, _gemini_cmd, _codex_cmd
import pt_db

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HARD_WHERE = ("(photo_status IN ('有图模糊','OCR失败') OR box_check='标牌糊未核对') "
              "AND (model IS NULL OR model NOT LIKE 'codex%')")


def fetch_hard(conn, limit=None):
    """硬样本行按 source_file 分组：{文件名: {(sheet, row), ...}}，--limit 截文件数。"""
    rows = conn.execute(
        f"SELECT source_file, sheet, row FROM records WHERE {HARD_WHERE} "
        "ORDER BY source_file, sheet, row").fetchall()
    by_file = {}
    for src, sheet, row in rows:
        by_file.setdefault(src, set()).add((sheet, row))
    files = sorted(by_file)
    if limit:
        files = files[:limit]
    return {f: by_file[f] for f in files}


def fetch_file_rows(conn, fname):
    cols = ("sheet", "row", "photo_status", "box_check", "box_image", "power_image_dbm",
            "lat", "lon", "address", "addr_area", "addr_estate", "addr_street",
            "test_date", "model", "ocr_at")
    cur = conn.execute(f"SELECT {','.join(cols)} FROM records WHERE source_file=?", (fname,))
    return [dict(zip(cols, r)) for r in cur]


def rebuild_json(fname, d):
    """非硬样本行：从库内字段反查重建近似 OCR json（喂给 reconcile 重算结果不变）。"""
    return {"source_file": fname, "row": d["row"], "sheet": d["sheet"],
            "box_name_image": d["box_image"], "power_dbm": d["power_image_dbm"],
            "lat": d["lat"], "lon": d["lon"], "address": d["address"],
            "addr_area": d["addr_area"], "addr_estate": d["addr_estate"],
            "addr_street": d["addr_street"], "near_street": False,
            "timestamp": d["test_date"],
            "legible": d["photo_status"] == "有图清晰",
            "notes": "OCR_FAILED: rebuilt-from-db" if d["photo_status"] == "OCR失败" else ""}


def ocr_targets(extracted, db_keys, hard_keys):
    """要真跑 OCR 的行：硬样本行 + 库里没有的新行（xlsx 中途变更的兜底），且必须有图。"""
    return [r for r in extracted
            if r.get("has_photo") and r.get("image")
            and ((r.get("sheet"), r["row"]) in hard_keys
                 or (r.get("sheet"), r["row"]) not in db_keys)]


def process_file(conn, fname, hard_keys, args, backend_handle, ocr_fn):
    """单文件复核。返回 before 快照 {(sheet,row): (photo_status, box_check)}，
    覆盖该文件复核前库内**全部行**（非仅硬样本，供 diff_stats 全量检测劣化/消失）；
    跳过返回 None。store 之前先做 OCR json 完整性校验，缺行即抛 RuntimeError 中止
    （由 main 的循环兜底：回滚、打印、跳过该文件），保证 store 绝不在已知有缺口时执行。"""
    xlsx = next((p for p in (os.path.join(d, fname) for d in args.dir) if os.path.isfile(p)), None)
    if xlsx is None:
        print(f"[缺文件] {fname}: 在 {args.dir} 下都找不到，跳过")
        return None
    xlsx_size = os.path.getsize(xlsx)  # 提前取：store 提交之后再抛 IO 异常就撤不掉了
    db_rows = fetch_file_rows(conn, fname)
    db_keys = {(d["sheet"], d["row"]) for d in db_rows}
    tmp = tempfile.mkdtemp(prefix="pt_recheck_")
    try:
        # ① 重抽照片到临时目录
        extracted = extract_file(xlsx, tmp, log=lambda *a: None)
        if not extracted:
            print(f"[空] {fname}: 重抽无光功率行，跳过")
            return None
        rows_json = os.path.join(tmp, "rows.json")
        with open(rows_json, "w", encoding="utf-8") as f:
            json.dump(extracted, f, ensure_ascii=False)
        ocr_dir = os.path.join(tmp, "ocr")
        os.makedirs(ocr_dir, exist_ok=True)

        # ② 只对硬样本行跑复核 OCR（result_path 命名规则直接复用 pt_ocr 的），
        #    按 BATCH_SIZE 分批走 pt_ocr 的批量后端函数（ocr_batch/ocr_batch_codex
        #    签名一致：(items, backend_handle, retries, downscale)），批次间用线程池并发。
        targets = ocr_targets(extracted, db_keys, hard_keys)
        print(f"[复核] {fname}: 硬样本 {len(hard_keys)} 行，OCR {len(targets)} 张，后端 {args.backend}")
        chunks = [targets[i:i + BATCH_SIZE] for i in range(0, len(targets), BATCH_SIZE)]
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(ocr_fn, c, backend_handle, 5, args.downscale): c for c in chunks}
            for fut in as_completed(futs):
                chunk = futs[fut]
                batch = fut.result()
                for it, res in zip(chunk, batch):
                    with open(result_path(ocr_dir, it), "w", encoding="utf-8") as f:
                        json.dump(res, f, ensure_ascii=False)

        # ③ 补齐非硬样本行的 OCR json——store(refresh) 按 OCR 目录整体重建，缺谁丢谁。
        #    photo_status=无图 的行原本就没有 OCR json，跳过（reconcile 走无图分支）。
        ocred = {(t.get("sheet"), t["row"]) for t in targets}
        kept = []
        for d in db_rows:
            if (d["sheet"], d["row"]) in ocred:
                continue
            kept.append(d)
            if d["photo_status"] == "无图":
                continue
            ref = {"source_file": fname, "sheet": d["sheet"], "row": d["row"]}
            with open(result_path(ocr_dir, ref), "w", encoding="utf-8") as f:
                json.dump(rebuild_json(fname, d), f, ensure_ascii=False)

        # ③.5 完整性校验（预防层）：store(refresh) 缺谁丢谁——应有 json 的行（库内非
        #    "无图"行）必须都能被 load_ocr 真实扫到（直接调 load_ocr 同源判定，不另写
        #    一套 key 解析），缺任何一行就在 store 之前中止，库内保持原样
        expected = {(d["sheet"], d["row"]) for d in db_rows if d["photo_status"] != "无图"}
        landed = {(s, r) for (src, s, r) in load_ocr(ocr_dir) if src == fname}
        missing = sorted(expected - landed)
        if missing:
            raise RuntimeError(f"{fname}: OCR json 补齐不完整，缺 {missing}，"
                               f"已在入库前中止（该文件本次复核跳过，库内保持原样）")

        before = {(d["sheet"], d["row"]): (d["photo_status"], d["box_check"]) for d in db_rows}

        # ④ 整文件重建入库；未复核行还原原 model/ocr_at（不冒充 pro 复核过）
        #    store() 内部已自行 commit，重建数据到这里已落库；后面的溯源还原单独兜异常，
        #    失败也不回头把复核当成没发生——数据没错，只是溯源字段没还原全，告警让人工核对。
        pt_db.store(conn, rows_json, ocr_dir, args.model, mode="refresh")
        try:
            conn.executemany(
                "UPDATE records SET model=?, ocr_at=? WHERE source_file=? AND sheet=? AND row=?",
                [(d["model"], d["ocr_at"], fname, d["sheet"], d["row"]) for d in kept])
            conn.execute("UPDATE files SET file_size=? WHERE file_name=?", (xlsx_size, fname))
            conn.commit()
        except Exception as e:
            # executemany 中途失败时，已执行的还原还挂在未提交事务里——每条还原各自独立正确，
            # 提交保住已还原的部分（少一条要手工核对的）；连提交都失败才撤干净，别带半截事务走。
            try:
                conn.commit()
            except Exception:
                conn.rollback()
            print(f"  ⚠ {fname}: 已重建入库，但 model/ocr_at 溯源还原失败（{type(e).__name__}: {e}），"
                  f"请手工核对该文件未复核行的 model/ocr_at 字段")
        return before
    finally:
        # ⑤ 删临时目录：图片和临时 OCR json 绝不残留、绝不入库
        shutil.rmtree(tmp, ignore_errors=True)


def diff_stats(conn, fname, before, hard_keys):
    """before/after 对比。before 覆盖该文件全部行：n/leg/box（复核行数、legible 假转真数、
    box_check 非通过转通过数）只统计 hard_keys 命中的真复核行；worse 变差明细扫描全量
    （清晰转糊 / 通过转非通过 / 行消失，供人工裁定——复核只应更准），
    非硬样本行被空 OCR 重建降级的场景也在此捕获。"""
    cur = conn.execute(
        "SELECT sheet, row, photo_status, box_check FROM records WHERE source_file=?", (fname,))
    after = {(s, r): (p, b) for s, r, p, b in cur}
    n = leg = box = 0
    worse = []
    for k, (p0, b0) in before.items():
        p1, b1 = after.get(k, (None, None))
        if k in hard_keys:
            n += 1
            if p0 in ("有图模糊", "OCR失败") and p1 == "有图清晰":
                leg += 1
            if b0 != "通过" and b1 == "通过":
                box += 1
        if (p1 is None or (p0 == "有图清晰" and p1 != "有图清晰")
                or (b0 == "通过" and b1 != "通过")):
            worse.append((k, p0, b0, p1, b1))
    return n, leg, box, worse


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="pt_data.sqlite")
    ap.add_argument("--dir", nargs="+", required=True,
                     help="xlsx 下载目录（可传多个，覆盖范围与 pt_batch.py 一致；"
                          "按 source_file 依次在各目录下定位原文件重抽照片）")
    ap.add_argument("--backend", choices=["gemini", "codex"], default="codex",
                     help="复核用的 OCR 后端（默认 codex/GPT-5.6-terra 订阅路线；"
                          "gemini 为旧路线，本轮 1028 条硬样本失败全部出自该路线，不建议再用）")
    ap.add_argument("--model", default=None, help="写库的 model 标签，默认按 --backend 自动取")
    ap.add_argument("--limit", type=int, default=None, help="最多处理几个命中文件")
    ap.add_argument("--workers", type=int, default=8, help="并发批次数（codex/gemini CLI 子进程并发数）")
    ap.add_argument("--downscale", type=int, default=1024,
                     help="OCR 前图片长边压缩像素，0=不压缩（对齐 pt_batch 默认 1024）")
    args = ap.parse_args(argv)

    if args.backend == "codex":
        backend_handle = _codex_cmd()
        if not backend_handle:
            print("PATH 上找不到 codex CLI（npm i -g @openai/codex 并完成 codex login 登录）")
            return 2
        ocr_fn = ocr_batch_codex
        args.model = args.model or f"codex-{CODEX_MODEL}"
    else:
        backend_handle = _gemini_cmd()
        if not backend_handle:
            print("PATH 上找不到 gemini CLI（npm i -g @google/gemini-cli 并完成 OAuth 登录）")
            return 2
        ocr_fn = ocr_batch
        args.model = args.model or "gemini-cli-recheck"

    conn = pt_db.connect(args.db)
    by_file = fetch_hard(conn, args.limit)
    total_hard = sum(len(v) for v in by_file.values())
    print(f"硬样本 {total_hard} 行，分布在 {len(by_file)} 个文件"
          + (f"（--limit {args.limit}）" if args.limit else ""))
    started = time.time()
    tot = {"files": 0, "rows": 0, "leg": 0, "box": 0}
    for fname, hard_keys in by_file.items():
        try:
            res = process_file(conn, fname, hard_keys, args, backend_handle, ocr_fn)
        except Exception as e:
            # 能抛到这里的只剩 store() 提交之前的异常（重抽/OCR/store 中途）——store 之后的
            # 溯源还原异常已在 process_file 内兜住告警不再上抛，所以"保持原样"是真的
            conn.rollback()  # store 中途失败别把半截事务带进下一个文件
            print(f"  ✗ 异常 {fname}: {type(e).__name__}: {e}（已回滚，库内保持原样）")
            continue
        if res is None:
            continue
        before = res
        n, leg, box, worse = diff_stats(conn, fname, before, hard_keys)
        tot["files"] += 1; tot["rows"] += n; tot["leg"] += leg; tot["box"] += box
        print(f"  {fname}: 复核 {n} 行，legible 转清晰 +{leg}，盒子核对转通过 +{box}")
        for (sheet, row), p0, b0, p1, b1 in worse:
            print(f"    ⚠ 变差待人工裁定 [{sheet}] 行{row}: "
                  f"照片状态 {p0}->{p1 or '行消失'}，盒子核对 {b0}->{b1 or '-'}")
    conn.close()
    print("=" * 60)
    print(f"复核完成: {tot['files']} 个文件 / {tot['rows']} 行，"
          f"legible false->true {tot['leg']}，box_check 非通过->通过 {tot['box']}，"
          f"耗时 {time.time()-started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
