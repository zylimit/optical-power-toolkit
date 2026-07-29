"""Reuse a saved onebox session (onebox_auth.json) to produce HTTP headers, plus
the interactive login that seeds it. onebox uses Huawei SSO; csrftoken is just the
WAPCSRFTOKEN cookie value echoed back in a header."""

import asyncio
import os
from typing import Dict

from playwright.async_api import async_playwright as async_playwright_api
from playwright.sync_api import sync_playwright

from . import config
from .exceptions import AuthExpiredError
from .logging_setup import get_logger

log = get_logger()

LOGIN_DURATION_SECONDS = 240
LOGIN_SAVE_INTERVAL_SECONDS = 5


def interactive_login(
    duration_seconds: int = LOGIN_DURATION_SECONDS,
    save_interval_seconds: int = LOGIN_SAVE_INTERVAL_SECONDS,
    auth_file: str = config.AUTH_FILE,
) -> None:
    """Open a visible browser against onebox; Huawei SSO usually logs in
    automatically. storage_state auto-saves every few seconds -- just wait until
    the file list shows, then close the window."""

    async def _run():
        async with async_playwright_api() as p:
            # VPN/内网下 playwright 自带 chromium 经常下不了；优先用本机 Chrome/Edge。
            launch_kwargs = {"headless": False}
            for channel in ("chrome", "msedge"):
                try:
                    browser = await p.chromium.launch(channel=channel, **launch_kwargs)
                    log.info(f"[login] 使用本机浏览器 channel={channel}")
                    break
                except Exception as e:
                    log.warning(f"[login] channel={channel} 启动失败: {e}")
            else:
                browser = await p.chromium.launch(**launch_kwargs)
                log.info("[login] 回退 playwright 自带 chromium")
            ctx = await browser.new_context()
            closed = asyncio.Event()

            def _on_page(pg):
                pg.on("close", lambda: closed.set() if len(ctx.pages) == 0 else None)

            ctx.on("page", _on_page)
            browser.on("disconnected", lambda: closed.set())

            page = await ctx.new_page()
            await page.goto(config.TARGET_URL)
            log.info(f"[login] 若未自动登录，请在浏览器里完成 SSO；每 {save_interval_seconds}s 自动保存到 {auth_file}，看到文件列表后关闭窗口即可")

            elapsed = 0
            saved = False
            while elapsed < duration_seconds and not closed.is_set():
                try:
                    await asyncio.wait_for(closed.wait(), timeout=save_interval_seconds)
                except asyncio.TimeoutError:
                    pass
                elapsed += save_interval_seconds
                if closed.is_set():
                    break
                try:
                    await ctx.storage_state(path=auth_file)
                    saved = True
                except Exception:
                    closed.set()
            try:
                await ctx.storage_state(path=auth_file)
                saved = True
            except Exception:
                pass
            log.info(f"[login] {auth_file} 已保存" if saved else f"[login] 未能保存 {auth_file}，请重试")
            try:
                await browser.close()
            except Exception:
                pass

    asyncio.run(_run())


def get_session_headers(auth_file: str = config.AUTH_FILE) -> Dict[str, str]:
    """Load the saved session headless, refresh cookies by hitting onebox, and
    return HTTP headers for `requests`. Raises AuthExpiredError if the session is
    gone (caller should prompt --login)."""
    if not os.path.exists(auth_file):
        raise AuthExpiredError(f"找不到 {auth_file}，请先运行 `python onebox_download.py --login` 完成登录")

    log.info("正在通过 Playwright 加载 onebox 以刷新会话 ...")
    # xgate（内网 SSO 代理）注入登录态要 15-30s，networkidle 会早于它完成 -> 轮询等
    # WAPCSRFTOKEN + hwsso_login 两个 cookie 出现，最多等 wait_seconds，其间周期性 reload。
    wait_seconds = 60
    poll = 3
    cookie_map = {}
    with sync_playwright() as p:
        # 无头模式：复用 --login 刚存的新鲜 auth.json（xgate 只在 --login 的可见窗口注入登录态）。
        # VPN/内网下 playwright 自带 chromium 常下载失败，优先本机 Chrome/Edge。
        browser = None
        last_err = None
        for channel in ("chrome", "msedge"):
            try:
                browser = p.chromium.launch(channel=channel, headless=True)
                log.info(f"[browser] 使用本机浏览器 channel={channel} headless=True")
                break
            except Exception as e:
                last_err = e
                log.warning(f"[browser] channel={channel} 启动失败: {e}")
        if browser is None:
            try:
                browser = p.chromium.launch(headless=True)
                log.info("[browser] 回退 playwright 自带 chromium headless=True")
            except Exception as e:
                raise RuntimeError(
                    f"无法启动浏览器（chrome/msedge/playwright均失败）。最后错误: {last_err or e}"
                ) from e
        try:
            ctx = browser.new_context(storage_state=auth_file)
            page = ctx.new_page()
            # xgate 代理下整页 "load" 事件可能等不到，只等 DOM 就绪 + 放宽超时
            page.goto(config.TARGET_URL, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            waited = 0
            while waited < wait_seconds:
                cookie_map = {c["name"]: c["value"] for c in ctx.cookies()}
                if cookie_map.get("WAPCSRFTOKEN") and "hwsso_login" in cookie_map:
                    break
                page.wait_for_timeout(poll * 1000)
                waited += poll
                if waited % 12 == 0:  # 每 12s reload 一次触发 xgate 重新注入
                    try:
                        page.reload()
                    except Exception:
                        pass
        finally:
            browser.close()

    csrf = cookie_map.get("WAPCSRFTOKEN")
    if not csrf or "hwsso_login" not in cookie_map:
        raise AuthExpiredError(f"会话已失效（等了 {wait_seconds}s 仍缺 WAPCSRFTOKEN/hwsso_login，xgate 未登录成功），请重新运行 `python onebox_download.py --login`")

    cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_map.items())
    log.info("onebox 会话有效")
    return {
        "Cookie": cookie_str,
        "csrftoken": csrf,
        "oneboxajax": "true",
        "x-requested-with": "XMLHttpRequest",
        "x-obx-lang": "zh",
        "Accept": "*/*",
        "Referer": config.BASE_URL + "/",
        "Origin": config.BASE_URL,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    }
