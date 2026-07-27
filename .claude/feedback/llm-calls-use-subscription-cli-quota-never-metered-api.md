---
type: feedback
description: 大模型调用（OCR/生成/审查等）一律优先走用户已登录的订阅/OAuth 额度 CLI（如 gemini oauth-personal），禁止按量计费的直连 REST API 或 apikey 模式 CLI；接入前必须先确认计费路径，计费模式须先与用户对齐替代方案
created: 2026-07-11
updated: 2026-07-11
occurrences: 1
graduated: false
source_skill: N/A（pt_ocr.py OCR 接入方式，主 Agent 选型问题）
---

# 大模型调用优先用订阅/OAuth 额度的 CLI，禁止按量计费 API

**问题描述**：optical-power-toolkit 的 `pt_ocr.py` 用 `GEMINI_API_KEY` 直连 Gemini REST API 做光功率照片 OCR，每次调用按 token 计费。而用户机器上已装好 Gemini CLI（OAuth 个人账号登录，`GOOGLE_CLOUD_PROJECT=mangosv5`，走订阅/免费额度不按次计费），闲置额度完全没用上。用户在一个 session 内连续 5 次反馈且措辞逐步加重："你还是用API KEY跑的，能否切到Gemini CLI跑"→"API 太费钱了"→"我主要是要用额度，我发现我的额度一点都没有消耗，全部是API的钱"→"如果不行就切Codex CLI，一定不能用API了"→（得知 Codex CLI 当前是 apikey 模式后）"不要调用API"→"禁止使用API"。核心诉求：已付费/免费的订阅额度躺着不用、却在为按量计费的 API 掏钱，这个浪费不能接受——这是硬性成本红线，不是一次性临时要求。

**触发场景**：项目里接入大模型能力（本例是照片 OCR）时，默认选了最方便的直连 REST API/API Key 路径，没有先盘点用户已有的订阅额度渠道。注意坑点：同一个工具也分计费模式——Codex CLI 当前 `auth_mode: apikey` 仍是计费路径，同样要避开，直到用户自己切成订阅登录。

**教训/建议**：
1. 本项目（以及类似场景）凡是要接入/新增任何大模型调用（OCR、生成、审查等），派发实现任务前必须先确认一件事："这个调用走的是按量计费 API，还是用户已有的订阅/OAuth 额度"——是计费模式就先跟用户对齐能否用免费/订阅路径替代，不能默认用最方便的 REST API/API Key 方式。
2. 优先复用用户已登录、走订阅/OAuth 额度的 CLI 工具（如 `gemini` CLI 的 oauth-personal 模式）；按 token/次数计费的直连 REST API、以及 apikey 计费模式的 CLI（哪怕是同一个工具）一律禁用。
3. 已验证的可行路径：Gemini CLI headless 模式——`gemini -p ... --output-format json --approval-mode plan --allowed-mcp-server-names none`；须显式加禁工具指令防止它跑偏成智能体行为，批量塞多张图摊薄单次启动开销。
