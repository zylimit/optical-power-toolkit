"""Command-line entry point for the onebox (WeLink 云空间) downloader."""

import argparse

from . import config
from .api_client import OneboxApiClient
from .auth import get_session_headers, interactive_login
from .crawler import OneboxCrawler
from .downloader import OneboxDownloader
from .exceptions import AuthExpiredError, OneboxDownloaderError
from .logging_setup import setup_logging


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="onebox_downloader",
        description="下载 WeLink 云空间群空间（默认 Power Test 光功率）的文件，默认只下 xlsx。",
    )
    p.add_argument("--login", action="store_true", help="打开浏览器完成 SSO 登录，刷新 onebox_auth.json 后退出")
    p.add_argument("--team-id", default=config.DEFAULT_TEAM_ID, help=f"群空间 teamId (默认 {config.DEFAULT_TEAM_ID})")
    p.add_argument("--output-dir", default=config.DEFAULT_OUTPUT_DIR, help=f"保存目录 (默认 {config.DEFAULT_OUTPUT_DIR})")
    p.add_argument("--workers", type=int, default=config.DEFAULT_WORKERS, help=f"并发数 (默认 {config.DEFAULT_WORKERS})")
    p.add_argument("--retry", type=int, default=config.DEFAULT_DOWNLOAD_RETRY, help="单文件失败重试次数")
    p.add_argument("--overwrite", action="store_true", help="强制重新下载已存在文件")
    p.add_argument("--all-types", action="store_true", help="下载所有类型（默认只下 xlsx）")
    p.add_argument("--limit", type=int, default=None, help="只下载前 N 个（试水用）")
    p.add_argument("--log-level", default=config.DEFAULT_LOG_LEVEL, choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    log = setup_logging(args.log_level)

    if args.login:
        log.info("交互式登录 onebox ...")
        interactive_login()
        return 0

    log.info("=" * 60)
    log.info("WeLink 云空间下载器")
    log.info("=" * 60)
    try:
        headers = get_session_headers()
        api_client = OneboxApiClient(headers, team_id=args.team_id)
        downloader = OneboxDownloader(api_client, retry=args.retry, overwrite=args.overwrite)
        extensions = None if args.all_types else config.DEFAULT_EXTENSIONS
        crawler = OneboxCrawler(api_client, downloader, args.output_dir,
                                workers=args.workers, extensions=extensions, limit=args.limit)
        manifest = crawler.run()
        return 1 if manifest["fail"] else 0
    except AuthExpiredError as e:
        log.error(f"认证失败: {e}")
        log.error("请运行 `python onebox_download.py --login` 完成登录后再试。")
        return 2
    except OneboxDownloaderError as e:
        log.error(f"任务终止: {e}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
