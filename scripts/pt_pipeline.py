"""光功率总表 一键流水线：xlsx -> 抽取(表+锚定照片) -> Gemini OCR -> 结构化总表 CSV。

    python pt_pipeline.py "onebox_power_test/IJU ...14082025.xlsx" --out pt_run
    python pt_pipeline.py onebox_power_test/*.xlsx --out pt_all --workers 16

三个阶段各是独立脚本(pt_extract / pt_ocr / pt_merge)，本脚本按顺序调用并汇总。
Gemini 直连需外网(关 VPN)；OCR/总表可反复重跑，断点续传只补缺的。
"""

import argparse
import os
import subprocess
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def run(cmd, title):
    print(f"\n{'='*60}\n▶ {title}\n{'='*60}")
    t = time.time()
    r = subprocess.run([PY] + cmd, cwd=HERE)
    if r.returncode != 0:
        print(f"✗ 阶段失败(exit {r.returncode}): {title}")
        sys.exit(r.returncode)
    print(f"✓ {title}  ({time.time()-t:.1f}s)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx", nargs="+", help="一个或多个 PT xlsx 文件")
    ap.add_argument("--out", default="pt_run", help="工作/输出目录")
    ap.add_argument("--workers", type=int, default=16, help="OCR 并发(Gemini Ultra 量足可调高)")
    ap.add_argument("--downscale", type=int, default=1024, help="OCR 前缩图最大边(px)，0=原图")
    ap.add_argument("--model", default="gemini-3.5-flash")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    rows = os.path.join(args.out, "rows.json")
    ocr = os.path.join(args.out, "ocr")
    master = os.path.join(args.out, "master.csv")

    # ① 抽取
    run(["pt_extract.py", *args.xlsx, "--out", args.out], "① 抽取 表格+锚定照片")
    # ② Gemini OCR（断点续传：已识别的跳过）
    run(["pt_ocr.py", "--rows", rows, "--out", ocr,
         "--workers", str(args.workers), "--downscale", str(args.downscale),
         "--model", args.model], "② Gemini OCR 识别照片")
    # ③ 合成总表
    run(["pt_merge.py", "--rows", rows, "--ocr", ocr, "--out", master], "③ 合成结构化总表")

    print(f"\n{'='*60}\n✅ 完成 -> {master}\n{'='*60}")


if __name__ == "__main__":
    main()
