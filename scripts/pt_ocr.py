"""Local Gemini OCR for optical-power-test photos. Reads a pt_extract rows.json,
sends each anchored photo straight to Gemini 2.5 Flash (direct REST, no proxy),
and writes one result JSON per row (checkpoint: skip rows already done, so a
crash/rerun only fills the gaps). Concurrent via a thread pool.

    python pt_ocr.py --rows pt_run1/rows.json --out pt_run1/ocr --workers 12
"""

import argparse
import base64
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

PROMPT = (
    "这是 FTTH 光功率测试现场照片。只输出一个 JSON（不要 markdown 代码块），字段：\n"
    '{"box_name": 盒子白色标牌上印的编号(原样字母数字,别加下划线), '
    '"power_dbm": 橙色光功率计 LCD 的主读数(较大的数,通常为负,单位dBm,数字;看不清则 null), '
    '"lat": GPS叠加的纬度(十进制度,N正S负;无则null), '
    '"lon": GPS叠加的经度(十进制度,E正W负;无则null), '
    '"address": 叠加里的街道地址行(无则null), '
    '"addr_area": 地址里的 LGA/行政区名(如 Alimosho;无则null), '
    '"addr_estate": 地址里的封闭小区/estate 名(无则null), '
    '"addr_street": 门牌号+街名(如 13 Ogundimu St;无则null), '
    '"near_street": 布尔——叠加地址里没有本街名、street 取的是最近可见街名时为 true,否则 false, '
    '"timestamp": 叠加里的日期时间(无则null), '
    '"legible": 盒子标牌和功率计读数是否都清晰可读(true/false)}\n'
    "不要猜看不清的字符：看不清就 legible=false、power_dbm=null。"
)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)


def _extract_json(text):
    t = _FENCE.sub("", text.strip())
    m = re.search(r"\{.*\}", t, re.S)
    return json.loads(m.group()) if m else json.loads(t)


def _load_image(img_path, downscale):
    """Return (bytes, mime). If downscale>0, thumbnail to that max dim and re-encode JPEG."""
    if downscale:
        import io
        from PIL import Image
        im = Image.open(img_path)
        im.thumbnail((downscale, downscale))
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=85)
        return buf.getvalue(), "image/jpeg"
    with open(img_path, "rb") as f:
        raw = f.read()
    return raw, ("image/png" if img_path.lower().endswith(".png") else "image/jpeg")


def ocr_one(item, api_key, model, retries=5, downscale=0):
    img_path = item["image"]
    raw, mime = _load_image(img_path, downscale)
    body = {
        "contents": [{"parts": [
            {"text": PROMPT},
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(raw).decode()}},
        ]}],
        # thinking off: simple OCR doesn't need it -> cheaper + faster
        "generationConfig": {"temperature": 0, "thinkingConfig": {"thinkingBudget": 0}},
    }
    url = API.format(model=model)
    last = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(url, params={"key": api_key}, json=body, timeout=120)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.RequestException(f"HTTP {r.status_code} (可重试)")
            if r.status_code == 200:
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                d = _extract_json(text)
                return {
                    "source_file": item.get("source_file"), "row": item["row"], "sheet": item.get("sheet"),
                    "box_name_image": d.get("box_name"), "power_dbm": d.get("power_dbm"),
                    "lat": d.get("lat"), "lon": d.get("lon"), "address": d.get("address"),
                    "addr_area": d.get("addr_area"), "addr_estate": d.get("addr_estate"),
                    "addr_street": d.get("addr_street"), "near_street": d.get("near_street"),
                    "timestamp": d.get("timestamp"), "legible": d.get("legible"),
                    "notes": "",
                }
            last = f"HTTP {r.status_code}: {r.text[:120]}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(min(20, 2 * attempt))  # 退避，网络抖动时逐步拉长
    return {"source_file": item.get("source_file"), "row": item["row"], "sheet": item.get("sheet"),
            "box_name_image": None, "power_dbm": None, "lat": None, "lon": None, "address": None,
            "addr_area": None, "addr_estate": None, "addr_street": None, "near_street": False,
            "timestamp": None, "legible": False, "notes": f"OCR_FAILED: {last}"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True)
    ap.add_argument("--out", required=True, help="结果目录（一行一个 json，断点续传）")
    ap.add_argument("--model", default="gemini-3.5-flash")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--downscale", type=int, default=0, help="缩到该最大边(px)再发；0=原图")
    args = ap.parse_args(argv)

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("缺 GEMINI_API_KEY"); return 2
    os.makedirs(args.out, exist_ok=True)

    rows = json.load(open(args.rows, encoding="utf-8"))
    todo = [r for r in rows if r.get("has_photo") and r.get("image")]
    if args.limit:
        todo = todo[:args.limit]

    def result_path(r):
        # 带源文件 + sheet，避免不同文件相同 sheet 名/行号撞车
        key = re.sub(r"[^0-9A-Za-z]+", "_", f"{r.get('source_file')}__{r.get('sheet')}")
        return os.path.join(args.out, f"{key}_{r['row']}.json")

    pending = [r for r in todo if not os.path.exists(result_path(r))]
    print(f"待识别 {len(todo)} 张(已完成 {len(todo)-len(pending)}，本次跑 {len(pending)})，{args.workers} 并发，模型 {args.model}")
    started = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(ocr_one, r, key, args.model, 5, args.downscale): r for r in pending}
        for fut in as_completed(futs):
            r = futs[fut]
            res = fut.result()
            with open(result_path(r), "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False)
            done += 1
            if done % 25 == 0 or done == len(pending):
                rate = done / max(1e-9, time.time() - started)
                print(f"  {done}/{len(pending)}  ({rate:.1f}/s)")
    print(f"完成 {done} 张，耗时 {time.time()-started:.1f}s -> {args.out}")


if __name__ == "__main__":
    raise SystemExit(main())
