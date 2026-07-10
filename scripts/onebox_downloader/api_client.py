"""HTTP client for the onebox teamspace APIs: list files (paged) + resolve a
file's temporary signed download URL."""

import time
from typing import Dict, List

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import config
from .exceptions import ApiRequestError
from .logging_setup import get_logger

log = get_logger()


class OneboxApiClient:
    def __init__(self, headers: Dict[str, str], team_id: str = config.DEFAULT_TEAM_ID,
                 connect_retry: int = config.DEFAULT_CONNECT_RETRY):
        self.team_id = team_id
        self.session = requests.Session()
        self.session.headers.update(headers)
        retry = Retry(total=connect_retry, backoff_factor=1,
                      status_forcelist=[429, 500, 502, 503, 504],
                      allowed_methods=["GET", "POST"])
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def list_all_files(self, parent_id: str = "0") -> List[Dict]:
        """Page through /file/list until fewer than a full page returns.
        Returns raw file dicts (id/name/size/type/versions/...)."""
        items: List[Dict] = []
        page = 1
        while True:
            body = {
                "teamId": self.team_id, "parentId": parent_id,
                "pageNumber": page, "pageSize": config.PAGE_SIZE,
                "orderField": "modifiedAt", "desc": "false", "token": "", "mode": "new",
            }
            try:
                r = self.session.post(config.LIST_API, data=body, timeout=60)
                r.raise_for_status()
                content = (r.json().get("data") or {}).get("content") or []
            except (requests.RequestException, ValueError) as e:
                raise ApiRequestError(f"列表查询失败 page={page}: {e}")
            items.extend(content)
            log.info(f"列表第 {page} 页: +{len(content)}，累计 {len(items)}")
            if len(content) < config.PAGE_SIZE:
                break
            page += 1
        return items

    def get_download_url(self, file_id) -> str:
        ts = int(time.time() * 1000)
        url = f"{config.DOWNLOAD_URL_API}/{self.team_id}/{file_id}?{ts}&_={ts}"
        try:
            r = self.session.get(url, timeout=60)
            r.raise_for_status()
            download_url = (r.json().get("data") or {}).get("downloadUrl")
        except (requests.RequestException, ValueError) as e:
            raise ApiRequestError(f"取下载直链失败 file_id={file_id}: {e}")
        if not download_url:
            raise ApiRequestError(f"下载直链为空 file_id={file_id}")
        return download_url
