"""Entry point: `python onebox_download.py --login` then `python onebox_download.py`"""

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from onebox_downloader.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
