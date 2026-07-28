# -*- coding: utf-8 -*-
"""scripts/pt_ocr.py ocr_batch_tokenplan —— red-locks 失败测试。

锁定缺陷3：ocr_batch_tokenplan 读 ANTHROPIC_BASE_URL 可能误走 mango 网关。

Spec 契约：--backend tokenplan 必须「直连」token-plan 网关
（https://token-plan.cn-beijing.maas.aliyuncs.com/...），不读
ANTHROPIC_BASE_URL（那是 mango/litellm 网关的基址，给 --backend claude 用的）。
现状：`url = os.environ.get("ANTHROPIC_BASE_URL", "").rstrip("/") or TOKENPLAN_URL`
→ 设了 ANTHROPIC_BASE_URL=mango 后跑 --backend tokenplan，请求发往 mango。

隔离：monkeypatch requests.post 记录实际请求 url；设环境变量
ANTHROPIC_BASE_URL=mango + ANTHROPIC_AUTH_TOKEN=fake-key（避开缺 key 报错）；
items 给一张存在的临时图。不真正发网络请求（fake post 返回 200 + 空 content）。

运行：python -m pytest tests/test_pt_ocr_tokenplan.py -v
"""

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import pt_ocr  # noqa: E402


def _fake_post_factory(captured):
    """造一个替代 requests.post 的 fake：记 url 到 captured['url']，
    返回一个有 .status_code / .json() 的假响应。"""
    class _Resp:
        status_code = 200

        def json(self):
            # ocr_batch_tokenplan 会 _anthropic_text_block 取首个 text 块，
            # 再 _extract_json_array 解析；给一个合法 JSON 数组让它走通主路径
            return {"content": [{"type": "text", "text": "[{\"idx\":1}]"}]}

        @property
        def text(self):
            return ""

    def _fake_post(url, headers=None, json=None, timeout=None, **kw):
        captured["url"] = url
        return _Resp()

    return _fake_post


class TestRedlockTokenplanIgnoresAnthropicBaseUrl:
    """缺陷3：tokenplan 读 ANTHROPIC_BASE_URL 可能误走 mango。"""

    def test_tokenplan_ignores_anthropic_base_url(self, tmp_path, monkeypatch):
        # 造一张临时图（_image_to_base64 需要真实文件）
        img = tmp_path / "pic.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
        items = [{"image": str(img), "sheet": "Sheet1", "row": 1}]

        # 设 ANTHROPIC_BASE_URL=mango 网关（--backend claude 才该用）
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://mango.example.com")
        # 给个 fake key，避开 _tokenplan_key 返回 None 抛 FatalOCRError
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "fake-key-for-test")

        # 抓实际请求 url：patch requests 模块的 post
        import requests as _req_mod
        captured = {}
        monkeypatch.setattr(_req_mod, "post", _fake_post_factory(captured))

        # 调一次单图 tokenplan
        results = pt_ocr.ocr_batch_tokenplan(items, retries=1)

        # 断言：请求 url 应直连 token-plan，不应含 mango
        assert "url" in captured, "前置失败：requests.post 未被调用"
        url = captured["url"]
        assert "token-plan.cn-beijing" in url, (
            f"tokenplan 应直连 token-plan 网关，实际 url={url}"
            "（缺陷3：误读 ANTHROPIC_BASE_URL 走 mango）"
        )
        assert "mango" not in url, (
            f"tokenplan 不应走 mango 网关，实际 url={url}"
            "（缺陷3：误读 ANTHROPIC_BASE_URL 走 mango）"
        )
