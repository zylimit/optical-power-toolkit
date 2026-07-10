"""Extract optical-power-test rows from a (messy) WeLink PT xlsx.

These files are inconsistent: many sheets aren't power tables at all (OLT design,
stubs), and the power sheets themselves have different column layouts and header
wording. So we detect by DATA, not by fixed columns / header text:

  power sheet  := a sheet with >=3 box-IDs (H##L#S#) AND (power/picture keyword OR photos)
  box column   := the column with the most H##L#S# cells
  power column := the column with the most numeric cells in [0.5, 60] (dBm magnitude)
  pass column  := the column with the most Pass/Fail cells
  cluster/date := by header keyword, else best-effort

Each data row (a row whose box column holds an H##L#S#) is paired with the photo
anchored to that row (drawing anchor). Pure stdlib (zipfile + regex).

    python pt_extract.py "<file.xlsx>" --out pt_run1        # all power sheets
    python pt_extract.py "<file.xlsx>" --scan               # just classify sheets
"""

import argparse
import html
import json
import os
import re
import sys
import zipfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BOX_RE = re.compile(r"H\d+L\d+S\d+", re.I)
PASS_RE = re.compile(r"^(pass|fail)$", re.I)
POWER_KW = ("power", "ddm", "testing", "testimg", "test picture", "meter")


def _shared_strings(z):
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    sx = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
    return [html.unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S)))
            for si in re.findall(r"<si>(.*?)</si>", sx, re.S)]


def _resolve(base_dir, target):
    if target.startswith("/"):
        return target[1:]
    parts = []
    for p in (base_dir + "/" + target).split("/"):
        if p == "..":
            parts.pop()
        elif p not in (".", ""):
            parts.append(p)
    return "/".join(parts)


def _sheet_map(z):
    wb = z.read("xl/workbook.xml").decode("utf-8", "replace")
    rels = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="([^"]+)"',
                           z.read("xl/_rels/workbook.xml.rels").decode("utf-8", "replace")))
    return [(name, _resolve("xl", rels[rid]))
            for name, rid in re.findall(r'<sheet [^>]*name="([^"]+)"[^>]*r:id="(rId\d+)"', wb)]


def _cells(z, sheet_path, ss):
    """{row_int: {col_letter: value}}."""
    sx = z.read(sheet_path).decode("utf-8", "replace")
    out = {}
    for rm in re.finditer(r'<row r="(\d+)"[^>]*>(.*?)</row>', sx, re.S):
        rn = int(rm.group(1))
        cells = {}
        for cm in re.finditer(r'<c r="([A-Z]+)\d+"([^>]*)>(.*?)</c>', rm.group(2), re.S):
            col, attrs, inner = cm.group(1), cm.group(2), cm.group(3)
            t = (re.search(r't="(\w+)"', attrs) or [None, None])[1]
            if t == "inlineStr":
                v = "".join(re.findall(r"<t[^>]*>(.*?)</t>", inner, re.S))
                if v:
                    cells[col] = html.unescape(v)
            else:
                vm = re.search(r"<v>(.*?)</v>", inner, re.S)
                if vm:
                    cells[col] = ss[int(vm.group(1))] if t == "s" else html.unescape(vm.group(1))
        if cells:
            out[rn] = cells
    return out


def _row_to_image(z, sheet_path):
    sx = z.read(sheet_path).decode("utf-8", "replace")
    m = re.search(r'<drawing r:id="(rId\d+)"', sx)
    if not m:
        return {}
    base = sheet_path.split("/")[-1]
    rels_path = "xl/worksheets/_rels/" + base + ".rels"
    if rels_path not in z.namelist():
        return {}
    rels = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="([^"]+)"',
                           z.read(rels_path).decode("utf-8", "replace")))
    draw = _resolve("xl/worksheets", rels[m.group(1)])
    drels = "xl/drawings/_rels/" + draw.split("/")[-1] + ".rels"
    names = z.namelist()
    if draw not in names or drels not in names:
        return {}  # 该 drawing 无图关系文件（空 drawing 等）-> 无锚定图片
    emb = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="([^"]+)"',
                          z.read(drels).decode("utf-8", "replace")))
    dxml = z.read(draw).decode("utf-8", "replace")
    row2img = {}
    for a in re.finditer(r"<xdr:from>(.*?)</xdr:from>.*?<a:blip[^>]*r:embed=\"(rId\d+)\"", dxml, re.S):
        rr = re.search(r"<xdr:row>(\d+)</xdr:row>", a.group(1))
        if rr and a.group(2) in emb:
            row2img[int(rr.group(1)) + 1] = _resolve("xl/drawings", emb[a.group(2)])
    return row2img


def _num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def detect_columns(cells):
    """Data-driven column roles. Returns dict col letters + the header row number."""
    box_ct, num_ct, pass_ct = {}, {}, {}
    header_row, header_hits = None, 0
    for rn, row in cells.items():
        hits = 0
        for col, v in row.items():
            s = str(v)
            if BOX_RE.search(s):
                box_ct[col] = box_ct.get(col, 0) + 1
            n = _num(v)
            if n is not None and 0.5 <= n <= 60:
                num_ct[col] = num_ct.get(col, 0) + 1
            if PASS_RE.match(s.strip()):
                pass_ct[col] = pass_ct.get(col, 0) + 1
            low = s.lower()
            if any(k in low for k in ("cluster", "box", "power", "pass", "picture", "date", "ddm")):
                hits += 1
        if hits > header_hits:
            header_hits, header_row = hits, rn

    def top(d):
        return max(d, key=d.get) if d else None

    box_col = top(box_ct)
    pass_col = top(pass_ct)
    # power = most numeric-in-range, excluding box/pass columns
    num_ct = {c: n for c, n in num_ct.items() if c not in (box_col, pass_col)}
    power_col = top(num_ct)
    # cluster/date by header keyword
    cluster_col = date_col = None
    if header_row:
        for col, v in cells[header_row].items():
            low = str(v).lower()
            if "cluster" in low and not cluster_col:
                cluster_col = col
            if "date" in low and not date_col:
                date_col = col
    return {"header_row": header_row, "box": box_col, "power": power_col,
            "pass": pass_col, "cluster": cluster_col, "date": date_col,
            "box_count": box_ct.get(box_col, 0)}


def classify_sheet(z, sheet_path, ss):
    cells = _cells(z, sheet_path, ss)
    joined = " ".join(str(v) for row in cells.values() for v in row.values())
    box_ct = len(BOX_RE.findall(joined))
    has_kw = any(k in joined.lower() for k in POWER_KW)
    has_img = bool(_row_to_image(z, sheet_path))
    is_power = box_ct >= 3 and (has_kw or has_img)
    return is_power, box_ct, has_img, cells


def extract_sheet(z, sheet_name, sheet_path, ss, cells, out_dir, src_name):
    cols = detect_columns(cells)
    row2img = _row_to_image(z, sheet_path)
    img_dir = os.path.join(out_dir, "images")
    os.makedirs(img_dir, exist_ok=True)
    safe = re.sub(r"[^\w]+", "_", src_name + "__" + sheet_name)

    records = []
    for rn, row in cells.items():
        box = row.get(cols["box"]) if cols["box"] else None
        if not box or not BOX_RE.search(str(box)):
            continue  # only real box rows
        media = row2img.get(rn)
        img_out = None
        if media:
            ext = media.split(".")[-1]
            img_out = os.path.join(img_dir, f"{safe}_row{rn}.{ext}")
            with open(img_out, "wb") as f:
                f.write(z.read(media))
        records.append({
            "source_file": src_name, "sheet": sheet_name, "row": rn,
            "cluster": row.get(cols["cluster"]) if cols["cluster"] else None,
            "box_table": box,
            "power_table": row.get(cols["power"]) if cols["power"] else None,
            "date_table": row.get(cols["date"]) if cols["date"] else None,
            "passfail_table": row.get(cols["pass"]) if cols["pass"] else None,
            "image": (img_out.replace("\\", "/") if img_out else None),
            "has_photo": bool(media),
        })
    return records, cols


def extract_file(xlsx, out_dir, scan=False, log=print):
    """Extract all power-test rows (table + anchored photo) from one xlsx.
    Returns list of records. Skips non-power sheets; auto-detects columns."""
    z = zipfile.ZipFile(xlsx)
    ss = _shared_strings(z)
    src = os.path.basename(xlsx)
    log(f"文件: {src}")
    records = []
    for name, path in _sheet_map(z):
        is_power, box_ct, has_img, cells = classify_sheet(z, path, ss)
        if not is_power:
            log(f"  跳过 [{name}]  盒子ID={box_ct} 图={has_img}  -> 非光功率")
            continue
        if scan:
            c = detect_columns(cells)
            log(f"  光功率 [{name}]  盒子ID={box_ct} 图={has_img}  列: box={c['box']} power={c['power']} pass={c['pass']}")
            continue
        recs, cols = extract_sheet(z, name, path, ss, cells, out_dir, src)
        log(f"  光功率 [{name}]  {len(recs)} 行(有图{sum(r['has_photo'] for r in recs)})  列: box={cols['box']} power={cols['power']} pass={cols['pass']}")
        records.extend(recs)
    return records


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx", nargs="+")
    ap.add_argument("--out", default="pt_run1")
    ap.add_argument("--scan", action="store_true", help="只分类各 sheet，不抽取")
    args = ap.parse_args(argv)

    all_records = []
    for xlsx in args.xlsx:
        all_records.extend(extract_file(xlsx, args.out, scan=args.scan))

    if not args.scan:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "rows.json"), "w", encoding="utf-8") as f:
            json.dump(all_records, f, ensure_ascii=False, indent=1)
        print(f"共 {len(all_records)} 行 -> {os.path.join(args.out, 'rows.json')}")


if __name__ == "__main__":
    main()
