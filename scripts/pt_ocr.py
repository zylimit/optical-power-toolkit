"""Local Gemini OCR for optical-power-test photos, via the Gemini CLI
(OAuth personal quota, no API-key billing). Reads a pt_extract rows.json,
batches up to 10 anchored photos per `gemini` call (plan mode, tools off,
one JSON array back), and writes one result JSON per row (checkpoint: skip
rows already done, so a crash/rerun only fills the gaps). Concurrent CLI
subprocesses via a thread pool.

    python pt_ocr.py --rows pt_run1/rows.json --out pt_run1/ocr --workers 4

可选 --backend claude：走 mango-litellm 网关的 Claude 订阅路线（Anthropic
Messages API 格式，同样不计费），图片 base64 内联、强制单线程长退避。

    python pt_ocr.py --rows pt_run1/rows.json --out pt_run1/ocr --backend claude

可选 --backend codex：本机 Codex CLI（ChatGPT 订阅额度，`codex login` 登录，
非 apikey 模式，不计费），图片走 `-i` 文件引用、`-o` 落最终消息到文件。

    python pt_ocr.py --rows pt_run1/rows.json --out pt_run1/ocr --backend codex
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BATCH_SIZE = 10   # 每次 CLI 调用塞几张图：CLI 启动开销 ~55s 是固定成本，摊薄；再大单次失败重试代价过高
CLI_TIMEOUT = 480  # 秒；10 张/批常态 1-2 分钟，但实测偶发慢到 300s+（限流/抖动），给足余量再交给重试

# Claude 订阅路线（--backend claude，mango-litellm 网关）。
# 模型名绝对不能带 -api 后缀：网关配了两条路由，claude-sonnet-4-6 走订阅
# OAuth token（不计费），claude-sonnet-4-6-api 走按量计费 key——用错就违反
# 项目"禁止使用付费 API"的硬性规则。
CLAUDE_MODEL = "claude-sonnet-4-6"
CLAUDE_HTTP_TIMEOUT = 300        # 秒；10 张 base64 图一个请求，给足传输+推理余量
CLAUDE_429_RETRIES = 10          # 订阅账号限流紧、恢复时间未知，"硬跑到通"而非快速失败
CLAUDE_429_BACKOFF_START = 15    # 429 退避起步秒数，每次翻倍
CLAUDE_429_BACKOFF_CAP = 120     # 429 退避封顶秒数

# Codex 订阅路线（--backend codex，本机 Codex CLI，`codex login` 的 ChatGPT
# 订阅态，非 apikey）。gpt-5.6-terra 是"均衡"档（介于 sol 旗舰与 luna 轻量之间），
# 并发限流表现未实测，先不强制单线程，跑起来后照样看 DB 失败率判断要不要收并发。
CODEX_MODEL = "gpt-5.6-terra"
CODEX_REASONING_EFFORT = "medium"
CODEX_TIMEOUT = 480


class FatalOCRError(RuntimeError):
    """确定性环境错误（如目录不受信任）：重试无意义，直接终止整个运行，
    不往结果目录写失败文件污染断点续传。"""


def _gemini_cmd():
    """Resolve the gemini CLI invocation prefix.

    Windows 下 `gemini` 是 npm 的 .cmd shim，subprocess 列表参数会经 cmd.exe
    转义、多行中文 prompt 会被弄坏——改为 node 直调 shim 指向的 gemini.js。
    """
    exe = shutil.which("gemini")
    if not exe:
        return None
    if exe.lower().endswith(".cmd"):
        js = os.path.join(os.path.dirname(exe),
                          "node_modules", "@google", "gemini-cli", "bundle", "gemini.js")
        if os.path.exists(js):
            return ["node", js]
    return [exe]


def _codex_cmd():
    """Resolve the codex CLI invocation prefix. Windows 下 `codex` 也是 npm
    的 .cmd shim，同 _gemini_cmd() 的理由改为 node 直调 codex.js。"""
    exe = shutil.which("codex")
    if not exe:
        return None
    if exe.lower().endswith(".cmd"):
        js = os.path.join(os.path.dirname(exe),
                          "node_modules", "@openai", "codex", "bin", "codex.js")
        if os.path.exists(js):
            return ["node", js]
    return [exe]


# 单图 11 字段的定义与识别规则（pt_merge/pt_db 依赖这套字段含义，别改）；
# 批量版新增 idx 序号字段用于把数组元素映射回输入图片。
FIELDS = (
    '{"idx": 图片序号(整数,从1开始,对应列表里的"图N"), '
    '"box_name": 盒子白色标牌上印的编号(原样字母数字,别加下划线), '
    '"power_dbm": 橙色光功率计 LCD 的主读数(较大的数,通常为负,单位dBm,数字;看不清则 null), '
    '"lat": GPS叠加的纬度(十进制度,N正S负;无则null), '
    '"lon": GPS叠加的经度(十进制度,E正W负;无则null), '
    '"address": 叠加里的街道地址行(无则null), '
    '"addr_area": 地址里的 LGA/行政区名(如 Alimosho;无则null), '
    '"addr_estate": 地址里的封闭小区/estate 名(无则null), '
    '"addr_street": 门牌号+街名(如 13 Ogundimu St;无则null), '
    '"near_street": 布尔——叠加地址里没有本街名、street 取的是最近可见街名时为 true,否则 false, '
    '"timestamp": 叠加里的日期时间(无则null), '
    '"legible": 盒子标牌和功率计读数是否都清晰可读(true/false)}'
)

RULES = (
    "不要猜看不清的字符：看不清就 legible=false、power_dbm=null。\n"
    "功率计 LCD 和盒子标牌经常不在同一焦平面（一个清晰一个模糊很常见）：两者各自独立判断清晰度、"
    "分别就近取清晰的读数，互不牵连——不要因为其中一个模糊就把另一个也判 null 或不可读。\n"
    "标牌拍摄角度倾斜时要逐字符辨认：任何一个字符不确定，整体就 legible=false。"
    "禁止按常见的 FAT 命名格式(如 AREA+C#+Z#+H#+L#+S#)脑补缺失或模糊的字符。"
)


def batch_prompt(paths):
    n = len(paths)
    refs = "\n".join(f"图{i}: @{p.replace(os.sep, '/')}" for i, p in enumerate(paths, 1))
    return (
        f"以下 {n} 张图是 FTTH 光功率测试现场照片：\n{refs}\n\n"
        "不要调用任何工具，不要读取其他文件，不要联网搜索，直接根据图片内容回答。\n"
        f"对每张图独立识别，只输出一个 JSON 数组（不要 markdown 代码块），共 {n} 个对象，"
        "按输入顺序原样返回，不要重排、不要遗漏。每个对象的字段：\n"
        f"{FIELDS}\n{RULES}"
    )


def claude_batch_prompt(n):
    """Claude 后端的批量 prompt：图片作为 base64 content block 直接内联在
    请求体里（没有 gemini 的 @文件引用），所以用"第N张对应idx=N"描述顺序。
    字段定义与规则复用 FIELDS/RULES，保证两个后端抽取结果一致。"""
    return (
        f"输入的 {n} 张图片是 FTTH 光功率测试现场照片，按输入顺序第 N 张图对应 idx=N。\n"
        f"对每张图独立识别，只输出一个 JSON 数组（不要 markdown 代码块），共 {n} 个对象，"
        "按输入顺序原样返回，不要重排、不要遗漏。每个对象的字段：\n"
        f"{FIELDS}\n{RULES}"
    )


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)


def _extract_json_array(text):
    t = _FENCE.sub("", text.strip())
    m = re.search(r"\[.*\]", t, re.S)
    return json.loads(m.group()) if m else json.loads(t)


def _prepare_image(img_path, seq, downscale, tmp_dir):
    """把一张图物化到 tmp_dir 下，返回 CLI @ 引用用的绝对路径。

    一律进临时文件、用零填充序号命名（img01.png ...），两个原因（都踩过）：
    - gemini CLI 只能 @ 引用 workspace(cwd) 内的文件，越界不报错、静默不附图，
      模型会凭空编造结果——tmp_dir 建在 cwd 下保证在 workspace 内；
    - CLI 附图顺序是路径字典序而非 prompt 里 @ 出现的顺序，序号命名让
      字典序 == 输入顺序，idx 才能可靠映射回原始图片。
    缩图（downscale>0）落盘为 JPEG；CLI 的 @ 语法只认磁盘文件，不接受内联数据。
    """
    src = os.path.abspath(img_path)
    if not downscale:
        dst = os.path.join(tmp_dir, f"img{seq:02d}{os.path.splitext(src)[1].lower()}")
        shutil.copy(src, dst)
        return dst
    from PIL import Image
    im = Image.open(src)
    im.thumbnail((downscale, downscale))
    dst = os.path.join(tmp_dir, f"img{seq:02d}.jpg")
    im.convert("RGB").save(dst, "JPEG", quality=85)
    return dst


# Anthropic Messages API 只认这四种图片 mime；其他格式统一转 JPEG
_CLAUDE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}


def _image_to_base64(img_path, downscale):
    """读一张图返回 (base64串, mime)，供 Claude 后端内联进请求体。

    与 _prepare_image 目的不同：Messages API 走 base64 content block，
    不需要落临时文件、也没有 gemini CLI 的 workspace/字典序限制。
    缩图逻辑与 _prepare_image 一致（thumbnail 到最大边 + JPEG q85）。
    """
    src = os.path.abspath(img_path)
    ext = os.path.splitext(src)[1].lower()
    if not downscale and ext in _CLAUDE_MIME:
        with open(src, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii"), _CLAUDE_MIME[ext]
    # 需要缩图，或格式 API 不认：统一转 JPEG（内存里转，不落盘）
    import io
    from PIL import Image
    im = Image.open(src)
    if downscale:
        im.thumbnail((downscale, downscale))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"


def result_path(out_dir, r):
    # 带源文件 + sheet，避免不同文件相同 sheet 名/行号撞车
    key = re.sub(r"[^0-9A-Za-z]+", "_", f"{r.get('source_file')}__{r.get('sheet')}")
    return os.path.join(out_dir, f"{key}_{r['row']}.json")


def _record(item, d, notes=""):
    d = d or {}
    return {
        "source_file": item.get("source_file"), "row": item["row"], "sheet": item.get("sheet"),
        "box_name_image": d.get("box_name"), "power_dbm": d.get("power_dbm"),
        "lat": d.get("lat"), "lon": d.get("lon"), "address": d.get("address"),
        "addr_area": d.get("addr_area"), "addr_estate": d.get("addr_estate"),
        "addr_street": d.get("addr_street"), "near_street": d.get("near_street") or False,
        "timestamp": d.get("timestamp"), "legible": d.get("legible") or False,
        "notes": notes,
    }


def _fail(item, reason):
    return _record(item, None, notes=f"OCR_FAILED: {reason}")


def ocr_batch(items, gemini, retries=5, downscale=0):
    """OCR 一批（最多 BATCH_SIZE 张）图片，返回与 items 一一对应的结果列表。

    整批调用失败（CLI 非 0 退出、超时、JSON 解析失败）→ 整批重试 retries 次；
    单张图缺失/读取失败，或数组里缺某张图的条目 → 仅该条标 OCR_FAILED，其余照常返回。
    """
    # 临时图片目录必须建在 cwd 下（见 _prepare_image），用完即删
    tmp_dir = tempfile.mkdtemp(prefix="pt_ocr_tmp_", dir=os.getcwd())
    try:
        results = {}   # items 下标 -> 结果（准备失败的图先落定，不进 CLI 调用）
        sending = []   # (items 下标, 图片路径)
        for i, it in enumerate(items):
            try:
                sending.append((i, _prepare_image(it["image"], len(sending) + 1, downscale, tmp_dir)))
            except Exception as e:  # 单图坏了不连累整批，也不值得重试
                results[i] = _fail(it, f"图片读取失败 {type(e).__name__}: {e}")
        last = "本批没有可发送的图片"
        for attempt in range(1, retries + 1) if sending else ():
            try:
                prompt = batch_prompt([p for _, p in sending])
                r = subprocess.run(
                    gemini + ["-p", prompt, "--output-format", "json",
                              "--approval-mode", "plan", "--allowed-mcp-server-names", "none"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=CLI_TIMEOUT)
                if r.returncode != 0:
                    if "not trusted" in (r.stderr or ""):  # 目录不受信任是确定性环境错误
                        raise FatalOCRError(
                            f"gemini CLI 不信任当前目录 {os.getcwd()}：设 GEMINI_CLI_TRUST_WORKSPACE=true "
                            "或在交互模式里信任该目录后重跑")
                    raise RuntimeError(f"gemini CLI exit {r.returncode}: {(r.stderr or '')[:160]}")
                resp = json.loads(r.stdout)["response"]
                arr = _extract_json_array(resp)
                if not isinstance(arr, list) or not arr:
                    raise ValueError("响应不是非空 JSON 数组")
                by_idx = {d["idx"]: d for d in arr
                          if isinstance(d, dict) and isinstance(d.get("idx"), int)}
                for n, (i, _) in enumerate(sending, 1):
                    results[i] = (_record(items[i], by_idx[n]) if n in by_idx
                                  else _fail(items[i], "批量响应缺少该图条目"))
                break
            except FatalOCRError:
                raise
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
                time.sleep(min(20, 2 * attempt))  # 退避，网络抖动/限流时逐步拉长
        for i, _ in sending:
            results.setdefault(i, _fail(items[i], last))
        return [results[i] for i in range(len(items))]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def ocr_batch_claude(items, retries=5, downscale=0):
    """OCR 一批图片——Claude 订阅路线（mango-litellm 网关，Messages API 格式）。

    与 ocr_batch 签名平行、返回结构一致（与 items 一一对应的 _record/_fail
    列表），main() 统一调度。两类错误分开退避：
    - 429：订阅账号限流很紧且响应头没有 Retry-After，长退避硬跑——
      15s 起步每次翻倍、封顶 120s、最多 CLAUDE_429_RETRIES 次；
    - 其他错误（网络、5xx、JSON 解析失败）：沿用 gemini 路径的短退避节奏，
      重试 retries 次。
    """
    import requests
    url = os.environ["ANTHROPIC_BASE_URL"].rstrip("/") + "/v1/messages"
    headers = {"x-api-key": os.environ["ANTHROPIC_API_KEY"],
               "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    results = {}   # items 下标 -> 结果（准备失败的图先落定，不进请求）
    sending = []   # (items 下标, base64, mime)
    for i, it in enumerate(items):
        try:
            b64, mime = _image_to_base64(it["image"], downscale)
            sending.append((i, b64, mime))
        except Exception as e:  # 单图坏了不连累整批，也不值得重试
            results[i] = _fail(it, f"图片读取失败 {type(e).__name__}: {e}")
    last = "本批没有可发送的图片"
    if sending:
        content = [{"type": "image",
                    "source": {"type": "base64", "media_type": m, "data": b}}
                   for _, b, m in sending]
        content.append({"type": "text", "text": claude_batch_prompt(len(sending))})
        body = {"model": CLAUDE_MODEL, "max_tokens": 4096,
                "messages": [{"role": "user", "content": content}]}
        err_left, rl_left = retries, CLAUDE_429_RETRIES
        rl_wait, attempt = CLAUDE_429_BACKOFF_START, 0
        while err_left > 0 and rl_left > 0:
            try:
                r = requests.post(url, headers=headers, json=body,
                                  timeout=CLAUDE_HTTP_TIMEOUT)
                if r.status_code == 429:
                    rl_left -= 1
                    last = "HTTP 429 rate_limit_error（订阅账号限流）"
                    if rl_left:
                        time.sleep(rl_wait)
                        rl_wait = min(CLAUDE_429_BACKOFF_CAP, rl_wait * 2)
                    continue
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}")
                text = r.json()["content"][0]["text"]
                arr = _extract_json_array(text)
                if not isinstance(arr, list) or not arr:
                    raise ValueError("响应不是非空 JSON 数组")
                by_idx = {d["idx"]: d for d in arr
                          if isinstance(d, dict) and isinstance(d.get("idx"), int)}
                for n, (i, _b, _m) in enumerate(sending, 1):
                    results[i] = (_record(items[i], by_idx[n]) if n in by_idx
                                  else _fail(items[i], "批量响应缺少该图条目"))
                break
            except Exception as e:
                err_left -= 1
                attempt += 1
                last = f"{type(e).__name__}: {e}"
                if err_left:
                    time.sleep(min(20, 2 * attempt))  # 非 429 的短退避，同 gemini 节奏
    for i, _b, _m in sending:
        results.setdefault(i, _fail(items[i], last))
    return [results[i] for i in range(len(items))]


def ocr_batch_codex(items, codex, retries=5, downscale=0):
    """OCR 一批图片——Codex 订阅路线（本机 CLI，`-i` 传图 + `-o` 落最终消息到
    文件，避免解析 `--json` 事件流）。与 ocr_batch/ocr_batch_claude 签名和
    返回结构一致，整批失败走通用短退避重试（同 ocr_batch 节奏，非 claude
    那种按 429 区分的长退避——codex CLI 不透出 HTTP 状态码）。"""
    tmp_dir = tempfile.mkdtemp(prefix="pt_ocr_tmp_", dir=os.getcwd())
    try:
        results = {}
        sending = []
        for i, it in enumerate(items):
            try:
                sending.append((i, _prepare_image(it["image"], len(sending) + 1, downscale, tmp_dir)))
            except Exception as e:
                results[i] = _fail(it, f"图片读取失败 {type(e).__name__}: {e}")
        last = "本批没有可发送的图片"
        for attempt in range(1, retries + 1) if sending else ():
            out_file = os.path.join(tmp_dir, f"out_{attempt}.txt")
            try:
                prompt = claude_batch_prompt(len(sending))
                img_args = []
                for _, p in sending:
                    img_args += ["-i", p]
                cmd = codex + ["exec", "-m", CODEX_MODEL,
                                "-c", f'model_reasoning_effort="{CODEX_REASONING_EFFORT}"',
                                "--sandbox", "read-only", "--skip-git-repo-check",
                                *img_args, "-o", out_file, prompt]
                r = subprocess.run(cmd, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=CODEX_TIMEOUT)
                if r.returncode != 0:
                    raise RuntimeError(f"codex CLI exit {r.returncode}: {(r.stderr or '')[:160]}")
                if not os.path.exists(out_file):
                    raise RuntimeError("codex CLI 未写出 -o 输出文件")
                text = open(out_file, encoding="utf-8").read()
                arr = _extract_json_array(text)
                if not isinstance(arr, list) or not arr:
                    raise ValueError("响应不是非空 JSON 数组")
                by_idx = {d["idx"]: d for d in arr
                          if isinstance(d, dict) and isinstance(d.get("idx"), int)}
                for n, (i, _) in enumerate(sending, 1):
                    results[i] = (_record(items[i], by_idx[n]) if n in by_idx
                                  else _fail(items[i], "批量响应缺少该图条目"))
                break
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
                time.sleep(min(20, 2 * attempt))
        for i, _ in sending:
            results.setdefault(i, _fail(items[i], last))
        return [results[i] for i in range(len(items))]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True)
    ap.add_argument("--out", required=True, help="结果目录（一行一个 json，断点续传）")
    ap.add_argument("--workers", type=int, default=4,
                    help="并发批次数（同时跑几个 gemini CLI 子进程；OAuth 个人额度上限未实测，保守默认 4）")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--downscale", type=int, default=0, help="缩到该最大边(px)再发；0=原图")
    ap.add_argument("--backend", choices=["gemini", "claude", "codex"], default="gemini",
                    help="OCR 后端：gemini=本机 gemini CLI(OAuth 订阅额度，默认)；"
                         "claude=mango-litellm 网关 Claude 订阅路线（同样不计费）；"
                         "codex=本机 codex CLI(ChatGPT 订阅额度，codex login 登录，同样不计费)")
    args = ap.parse_args(argv)

    gemini = None
    codex = None
    if args.backend == "claude":
        missing = [k for k in ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY")
                   if not os.environ.get(k)]
        if missing:
            print(f"缺环境变量 {' / '.join(missing)}"
                  "（mango-litellm 网关的地址与 key，--backend claude 必需）"); return 2
        if args.workers != 1:
            # 订阅账号限流很紧（实测单线程 20-35s 间隔也连续 429），
            # 并发只会互相 429 得不偿失——强制单线程
            print("claude 后端强制单线程（订阅账号限流紧，并发只会互相 429），--workers 已覆盖为 1")
            args.workers = 1
    elif args.backend == "codex":
        codex = _codex_cmd()
        if not codex:
            print("PATH 上找不到 codex CLI（npm i -g @openai/codex 并完成 codex login 登录）"); return 2
    else:
        gemini = _gemini_cmd()
        if not gemini:
            print("PATH 上找不到 gemini CLI（npm i -g @google/gemini-cli 并完成 OAuth 登录）"); return 2
    os.makedirs(args.out, exist_ok=True)

    rows = json.load(open(args.rows, encoding="utf-8"))
    todo = [r for r in rows if r.get("has_photo") and r.get("image")]
    if args.limit:
        todo = todo[:args.limit]

    pending = [r for r in todo if not os.path.exists(result_path(args.out, r))]
    chunks = [pending[i:i + BATCH_SIZE] for i in range(0, len(pending), BATCH_SIZE)]
    backend_desc = {"claude": "Claude 订阅(mango-litellm)",
                     "codex": f"Codex CLI 订阅({CODEX_MODEL})"}.get(args.backend, "Gemini CLI(OAuth)")
    print(f"待识别 {len(todo)} 张(已完成 {len(todo)-len(pending)}，本次跑 {len(pending)})，"
          f"{len(chunks)} 批 x {BATCH_SIZE} 张，{args.workers} 并发批次，{backend_desc}")
    started = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        if args.backend == "claude":
            futs = {ex.submit(ocr_batch_claude, c, 5, args.downscale): c for c in chunks}
        elif args.backend == "codex":
            futs = {ex.submit(ocr_batch_codex, c, codex, 5, args.downscale): c for c in chunks}
        else:
            futs = {ex.submit(ocr_batch, c, gemini, 5, args.downscale): c for c in chunks}
        for fut in as_completed(futs):
            chunk = futs[fut]
            try:
                batch = fut.result()
            except FatalOCRError as e:
                ex.shutdown(cancel_futures=True)
                print(f"终止: {e}")
                return 2
            for it, res in zip(chunk, batch):
                with open(result_path(args.out, it), "w", encoding="utf-8") as f:
                    json.dump(res, f, ensure_ascii=False)
            done += len(chunk)
            rate = done / max(1e-9, time.time() - started)
            print(f"  {done}/{len(pending)}  ({rate:.2f}/s)")
    print(f"完成 {done} 张，耗时 {time.time()-started:.1f}s -> {args.out}")


if __name__ == "__main__":
    raise SystemExit(main())
