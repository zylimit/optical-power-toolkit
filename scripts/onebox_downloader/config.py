"""URLs, IDs, and defaults for the WeLink onebox (云空间) downloader."""

BASE_URL = "https://onebox.huawei.com"

# The "Power Test" group space (群空间). teamId == ownedBy in the API.
DEFAULT_TEAM_ID = "17033289"
TARGET_URL = f"{BASE_URL}/#eSpaceGroupFile/1/0/{DEFAULT_TEAM_ID}"

LIST_API = f"{BASE_URL}/perfect/teamspace/file/list"
# getForcedDownloadUrl/{teamId}/{fileId} -> {"data":{"downloadUrl": "..."}}
DOWNLOAD_URL_API = f"{BASE_URL}/perfect/files/getForcedDownloadUrl"

AUTH_FILE = "onebox_auth.json"
DEFAULT_OUTPUT_DIR = "onebox_power_test"

PAGE_SIZE = 100
DEFAULT_WORKERS = 3            # files run up to ~340MB; stay gentle on the server
DEFAULT_DOWNLOAD_RETRY = 3
DEFAULT_CONNECT_RETRY = 2
DEFAULT_LOG_LEVEL = "INFO"
LOG_DIR = "logs"
MANIFEST_FILENAME = "manifest.json"

# only keep these extensions when --ext-filter is on (default xlsx-only)
DEFAULT_EXTENSIONS = (".xlsx",)
