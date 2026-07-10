"""Single-file download: resolve the signed URL, stream to disk, skip if already
present at the right size, retry on failure."""

import html
import os
import re
from dataclasses import dataclass
from typing import Dict, Optional

import requests

from . import config
from .api_client import OneboxApiClient
from .exceptions import ApiRequestError
from .logging_setup import get_logger

log = get_logger()

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    """HTML-unescape (&amp; &nbsp; ...) then strip characters illegal on Windows."""
    name = html.unescape(name or "").replace("\xa0", " ")
    name = _ILLEGAL.sub("_", name).strip().rstrip(".")
    return name or "unnamed"


@dataclass
class DownloadResult:
    success: bool
    skipped: bool = False
    path: Optional[str] = None
    error: Optional[str] = None


class OneboxDownloader:
    def __init__(self, api_client: OneboxApiClient, retry: int = config.DEFAULT_DOWNLOAD_RETRY,
                 overwrite: bool = False):
        self.api_client = api_client
        self.retry = retry
        self.overwrite = overwrite

    def download_file(self, file: Dict, out_dir: str) -> DownloadResult:
        name = sanitize_filename(file.get("name"))
        size = file.get("size")
        dest = os.path.join(out_dir, name)

        if not self.overwrite and os.path.exists(dest) and os.path.getsize(dest) > 0:
            # skip if size matches (or size unknown but file non-empty)
            if size is None or os.path.getsize(dest) == size:
                return DownloadResult(True, skipped=True, path=dest)

        os.makedirs(out_dir, exist_ok=True)
        last_err = None
        for attempt in range(1, self.retry + 1):
            try:
                url = self.api_client.get_download_url(file.get("id"))
                tmp = dest + ".part"
                with self.api_client.session.get(url, stream=True, timeout=120) as r:
                    r.raise_for_status()
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_content(chunk_size=1024 * 256):
                            if chunk:
                                f.write(chunk)
                if size is not None and os.path.getsize(tmp) != size:
                    raise ApiRequestError(f"大小不符: 期望 {size}, 实到 {os.path.getsize(tmp)}")
                os.replace(tmp, dest)
                return DownloadResult(True, path=dest)
            except (requests.RequestException, ApiRequestError, OSError) as e:
                last_err = str(e)
                log.warning(f"下载失败({attempt}/{self.retry}) {name}: {e}")
        return DownloadResult(False, path=dest, error=last_err)
