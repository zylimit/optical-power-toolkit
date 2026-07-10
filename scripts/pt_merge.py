"""Reconcile pt_extract table rows with pt_ocr photo results into a single
optical-power MASTER table (CSV, no images) for dfc import.

Every judgement is a FIXED-ENUM column (filter/pivot friendly) -- no free text:
  置信度      高 / 中 / 低 / 无效
  照片状态    有图清晰 / 有图模糊 / 无图 / OCR失败
  功率核对    一致 / 表缺已恢复 / 表图不符 / 图糊用表 / 缺值 / 无图
  盒子核对    通过 / 后缀不符 / 标牌糊未核对 / 无图
  有坐标      是 / 否
  有地址      是 / 否
  可修坐标    是 / 否
  主要问题    正常 / 无图判Pass / 功率不符 / 盒子不符 / 无图 / OCR失败 / 功率漏填 / 标牌不清 / 无坐标

Truth model: power/lat/lon/address -> photo is truth; box name -> table clean,
verified by H##L#S# suffix.

    python pt_merge.py --rows pt_run1/rows.json --ocr pt_run1/ocr --out pt_run1/master.csv
"""

import argparse
import csv
import glob
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

POWER_TOL = 1.0
_SUFFIX = re.compile(r"H\d+L\d+S\d+")
# a real street address has digits + letters + a comma/street word; reject cluster-name junk
_ADDR_OK = re.compile(r"[A-Za-z].*,|street|st\.|road|rd|ave|lagos|nigeria", re.I)


def norm(s):
    return re.sub(r"[^A-Za-z0-9]", "", str(s or "")).upper()


def suffix(s):
    m = _SUFFIX.search(norm(s))
    return m.group() if m else None


def to_float(x):
    try:
        return float(str(x).replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def clean_addr(a):
    a = (a or "").strip()
    return a if (len(a) >= 8 and _ADDR_OK.search(a)) else ""


def load_ocr(ocr_dir):
    o = {}
    for fp in glob.glob(os.path.join(ocr_dir, "*.json")):
        try:
            x = json.load(open(fp, encoding="utf-8"))
            o[(x.get("source_file"), x.get("sheet"), x["row"])] = x  # 按 (源文件,sheet,行) 索引
        except (ValueError, KeyError, OSError):
            continue
    return o


def reconcile(r, o):
    has_photo = bool(r.get("has_photo"))
    legible = o.get("legible")
    failed = str(o.get("notes", "")).startswith("OCR_FAILED")
    passfail = str(r.get("passfail_table") or "").strip()

    pt = to_float(r.get("power_table"))
    if pt is not None and not (0 < pt <= 60):
        pt = None  # 表格功率超出合理 dBm 幅度(如日期序列号 45883)-> 当无效，退用照片
    pi = to_float(o.get("power_dbm"))
    pi_abs = abs(pi) if pi is not None else None
    st, si = suffix(r.get("box_table")), suffix(o.get("box_name_image"))
    lat, lon = o.get("lat"), o.get("lon")
    addr = clean_addr(o.get("address"))

    # 照片状态
    if not has_photo:
        photo = "无图"
    elif failed:
        photo = "OCR失败"
    elif not legible:
        photo = "有图模糊"
    else:
        photo = "有图清晰"

    # 功率核对 + final power
    if not has_photo:
        power_final, pchk = pt, "无图"
    elif pt is None and pi_abs is not None:
        power_final, pchk = pi_abs, "表缺已恢复"
    elif pt is not None and pi_abs is not None:
        power_final, pchk = pi_abs, ("一致" if abs(pt - pi_abs) <= POWER_TOL else "表图不符")
    elif pt is not None:
        power_final, pchk = pt, "图糊用表"
    else:
        power_final, pchk = None, "缺值"

    # 盒子核对
    if not has_photo:
        bchk = "无图"
    elif st and si:
        bchk = "通过" if st == si else "后缀不符"
    elif st and not si:
        bchk = "标牌糊未核对"
    else:
        bchk = "标牌糊未核对"

    has_coord = "是" if (lat is not None and lon is not None) else "否"
    has_addr = "是" if addr else "否"

    # 置信度
    if not has_photo and passfail:
        conf = "无效"
    elif pchk == "表图不符" or bchk == "后缀不符":
        conf = "低"
    elif photo in ("无图", "OCR失败"):
        conf = "低"
    elif pchk == "表缺已恢复" or bchk == "标牌糊未核对" or photo == "有图模糊":
        conf = "中"
    else:
        conf = "高"

    可修坐标 = "是" if (has_coord == "是" and conf in ("高", "中")) else "否"

    # 主要问题（单一，按优先级）
    if not has_photo and passfail:
        issue = "无图判Pass"
    elif pchk == "表图不符":
        issue = "功率不符"
    elif bchk == "后缀不符":
        issue = "盒子不符"
    elif photo == "无图":
        issue = "无图"
    elif photo == "OCR失败":
        issue = "OCR失败"
    elif pchk == "表缺已恢复":
        issue = "功率漏填"
    elif bchk == "标牌糊未核对":
        issue = "标牌不清"
    elif has_coord == "否":
        issue = "无坐标"
    else:
        issue = "正常"

    return {
        "box": r.get("box_table"), "power": power_final, "lat": lat, "lon": lon, "addr": addr,
        "置信度": conf, "照片状态": photo, "功率核对": pchk, "盒子核对": bchk,
        "有坐标": has_coord, "有地址": has_addr, "可修坐标": 可修坐标, "主要问题": issue,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True)
    ap.add_argument("--ocr", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    rows = json.load(open(args.rows, encoding="utf-8"))
    # 只保留真正的盒子行（box_table 含 H##L#S# 后缀），过滤 "170" 之类杂质
    rows = [r for r in rows if suffix(r.get("box_table"))]
    ocr = load_ocr(args.ocr)

    cols = ["source_file", "sheet", "row", "cluster", "box_name", "power_dbm",
            "lat", "lon", "address", "test_date", "pass_fail",
            "置信度", "照片状态", "功率核对", "盒子核对", "有坐标", "有地址", "可修坐标", "主要问题"]
    stats = {k: {} for k in ["置信度", "主要问题", "功率核对", "盒子核对"]}
    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            o = ocr.get((r.get("source_file"), r.get("sheet"), r["row"]), {})
            rec = reconcile(r, o)
            for k in stats:
                stats[k][rec[k]] = stats[k].get(rec[k], 0) + 1
            w.writerow([
                r.get("source_file") or r.get("file"), r.get("sheet"), r.get("row"),
                r.get("cluster"), rec["box"], rec["power"], rec["lat"], rec["lon"],
                rec["addr"], o.get("timestamp") or r.get("date_table"), r.get("passfail_table"),
                rec["置信度"], rec["照片状态"], rec["功率核对"], rec["盒子核对"],
                rec["有坐标"], rec["有地址"], rec["可修坐标"], rec["主要问题"],
            ])

    print(f"总表导出 {len(rows)} 行 -> {args.out}")
    for k, d in stats.items():
        print(f"  {k}: " + " | ".join(f"{kk}={vv}" for kk, vv in sorted(d.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()
