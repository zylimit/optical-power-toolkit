"""List the whole space, filter by extension, download concurrently, write a manifest."""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from . import config
from .api_client import OneboxApiClient
from .downloader import OneboxDownloader, sanitize_filename
from .logging_setup import get_logger

log = get_logger()


class OneboxCrawler:
    def __init__(self, api_client: OneboxApiClient, downloader: OneboxDownloader,
                 output_dir: str, workers: int = config.DEFAULT_WORKERS,
                 extensions: Optional[tuple] = None, limit: Optional[int] = None):
        self.api_client = api_client
        self.downloader = downloader
        self.output_dir = output_dir
        self.workers = workers
        # None -> download everything; tuple -> keep only these extensions
        self.extensions = extensions
        self.limit = limit

    def _keep(self, file: Dict) -> bool:
        if file.get("type") != 1:          # type 1 = file; skip folders/others
            return False
        if self.extensions is None:
            return True
        name = sanitize_filename(file.get("name")).lower()
        return name.endswith(self.extensions)

    def run(self) -> Dict:
        os.makedirs(self.output_dir, exist_ok=True)
        all_files = self.api_client.list_all_files()
        targets = [f for f in all_files if self._keep(f)]
        if self.limit:
            targets = targets[:self.limit]
        total = len(targets)
        log.info(f"共 {len(all_files)} 个条目，筛后待下载 {total} 个")

        success = skipped = fail = 0
        fail_list: List[Dict] = []
        started = time.time()

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futures = {ex.submit(self.downloader.download_file, f, self.output_dir): f for f in targets}
            done = 0
            for fut in as_completed(futures):
                f = futures[fut]
                done += 1
                res = fut.result()
                if res.success:
                    success += 1
                    if res.skipped:
                        skipped += 1
                    else:
                        log.info(f"[OK {done}/{total}] {os.path.basename(res.path)}")
                else:
                    fail += 1
                    fail_list.append({"id": f.get("id"), "name": f.get("name"), "error": res.error})
                    log.warning(f"[FAIL {done}/{total}] {f.get('name')}: {res.error}")

        elapsed = time.time() - started
        manifest = {
            "team_id": self.api_client.team_id,
            "output_dir": self.output_dir,
            "total_entries": len(all_files),
            "targeted": total,
            "success": success,
            "skipped": skipped,
            "fail": fail,
            "elapsed_sec": round(elapsed, 1),
            "fail_list": fail_list,
        }
        with open(os.path.join(self.output_dir, config.MANIFEST_FILENAME), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        log.info("=" * 60)
        log.info(f"完成: 成功 {success}(跳过 {skipped}) 失败 {fail}, 耗时 {elapsed:.1f}s")
        return manifest
