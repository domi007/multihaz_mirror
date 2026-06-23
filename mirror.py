import os
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import urlparse

# =============================================================================
# CONFIGURATION
# =============================================================================

BASE_URL = "https://VALAMI.multihaz.hu"
domain = urlparse(BASE_URL).hostname

LIST_URL = f"{BASE_URL}/api/document/list"
DOWNLOAD_URL = f"{BASE_URL}/api/document/download"

USERNAME = ""
PASSWORD = ""

USER_ID = None
HOUSE_IDS = []
HOUSE_ID = 2138
HOUSE_YEAR = 2026


# Where to store the mirror
LOCAL_ROOT = Path("./mirror")

# Delay between requests (seconds)
REQUEST_DELAY = 0.25

# =============================================================================
# SESSION SETUP
# =============================================================================

session = requests.Session()

#session.cookies.set(
#    ".AspNetCore.Session",
#    ASP_NET_CORE_SESSION,
#    domain=domain
#)
# Paste your session value here
#ASP_NET_CORE_SESSION = ""

session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:150.0) "
        "Gecko/20100101 Firefox/150.0"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": (
        f"{BASE_URL}/{HOUSE_ID}/{HOUSE_YEAR}/document/index"
    ),
    "Origin": BASE_URL,
    "Content-Type": "application/json",

    # Browser fetch metadata
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",

    "Connection": "keep-alive",
})
# Retry configuration
retry_strategy = Retry(
    total=5,
    connect=5,
    read=5,
    status=5,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET", "POST"],
)

adapter = HTTPAdapter(max_retries=retry_strategy)

session.mount("http://", adapter)
session.mount("https://", adapter)

# =============================================================================
# API FUNCTIONS
# =============================================================================
def timestamp_ms():
    return int(time.time() * 1000)

def initialize_session():
    r = session.get(
        f"{BASE_URL}/api/status/whoami",
        params={"t": timestamp_ms()},
        timeout=30,
    )
    r.raise_for_status()

def login():
    payload = {
        "username": USERNAME,
        "password": PASSWORD,
    }

    r = session.post(
        f"{BASE_URL}/api/account/logon",
        params={"t": timestamp_ms()},
        json=payload,
        timeout=30,
    )

    if r.status_code != 204:
        raise RuntimeError(
            f"Login failed: HTTP {r.status_code}"
        )

def get_identity():
    global USER_ID, HOUSE_IDS

    r = session.get(
        f"{BASE_URL}/api/status/whoami",
        params={"t": timestamp_ms()},
        timeout=30,
    )

    r.raise_for_status()

    data = r.json()

    if "whoami" not in data:
        raise RuntimeError("Authentication failed")

    whoami = data["whoami"]

    USER_ID = whoami["id"]

    # Extract house IDs from houseprivs keys
    HOUSE_IDS = [
        int(k)
        for k in whoami.get("houseprivs", {}).keys()
        if k != "0"
    ]

    print(f"User ID: {USER_ID}")
    print(f"House IDs: {HOUSE_IDS}")

    return whoami

def select_house_id(house_ids):
    """
    Let the user choose a HOUSE_ID from a list.
    Returns the selected HOUSE_ID.
    """

    if not house_ids:
        raise RuntimeError("No house IDs available")

    if len(house_ids) == 1:
        print(f"Only one house available: {house_ids[0]}")
        return house_ids[0]

    print("\nAvailable houses:")
    for i, house_id in enumerate(house_ids, start=1):
        print(f"  {i}. {house_id}")

    while True:
        try:
            choice = int(
                input("\nSelect house number: ").strip()
            )

            if 1 <= choice <= len(house_ids):
                return house_ids[choice - 1]

            print(
                f"Please enter a number between "
                f"1 and {len(house_ids)}."
            )

        except ValueError:
            print("Please enter a valid number.")

def list_directory(remote_dir: str):

    payload = {
        "dir": remote_dir,
        "view": "list",
        "houseid": HOUSE_ID,
        "houseyear": HOUSE_YEAR,
    }

    timestamp_ms = int(time.time() * 1000)

    response = session.post(
        LIST_URL,
        params={"t": timestamp_ms},
        json=payload,
        timeout=60,
    )

    response.raise_for_status()

    content_type = response.headers.get(
        "Content-Type", ""
    ).lower()

    if "html" in content_type:
        raise RuntimeError(
            f"Session expired or redirected while listing "
            f"{remote_dir}"
        )

    time.sleep(REQUEST_DELAY)

    return response.json()

def download_file(remote_path: str,
                  local_path: Path,
                  expected_size: int | None,
                  last_modified: int | None):
    """
    Download a single file.
    """

    if (
        expected_size is not None
        and local_path.exists()
        and local_path.stat().st_size == expected_size
    ):
        print(f"[SKIP] {remote_path}")
        return

    print(f"[FILE] {remote_path}")

    params = {
        "houseyear": HOUSE_YEAR,
        "houseid": HOUSE_ID,
        "file": remote_path,
    }

    response = session.get(
        DOWNLOAD_URL,
        params=params,
        stream=True,
    )

    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "").lower()

    if "text/html" in content_type:
        raise RuntimeError(
            f"Expected file download but received HTML: {remote_path}"
        )

    local_path.parent.mkdir(parents=True, exist_ok=True)

    with open(local_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)

    if last_modified:
        try:
            os.utime(local_path, (last_modified, last_modified))
        except Exception:
            pass

    time.sleep(REQUEST_DELAY)


# =============================================================================
# RECURSIVE MIRROR
# =============================================================================

def mirror_directory(remote_dir: str, local_dir: Path):
    """
    Recursively mirror a remote directory.
    """

    print(f"[DIR ] {remote_dir}")

    local_dir.mkdir(parents=True, exist_ok=True)

    data = list_directory(remote_dir)

    items = data.get("files", [])

    for item in items:

        try:
            name = item["name"]
            is_dir = item.get("isdir", False)

            if remote_dir in ("", "/"):
                child_remote = name
            else:
                child_remote = f"{remote_dir.rstrip('/')}/{name}"

            child_local = local_dir / name

            if is_dir:

                mirror_directory(
                    child_remote,
                    child_local
                )

            else:

                download_file(
                    remote_path=child_remote,
                    local_path=child_local,
                    expected_size=item.get("size"),
                    last_modified=item.get("lastmodified"),
                )

        except Exception as exc:
            print(
                f"[ERROR] Failed processing "
                f"{item.get('name', '<unknown>')}: {exc}"
            )


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    initialize_session()
    login()
    identity = get_identity()
    HOUSE_ID = select_house_id(HOUSE_IDS)
    print("Starting mirror...")

    try:
        # If this doesn't work, try ""
        ROOT_REMOTE_DIR = "/"

        mirror_directory(
            ROOT_REMOTE_DIR,
            LOCAL_ROOT
        )

        print("\nMirror completed successfully.")

    except KeyboardInterrupt:
        print("\nInterrupted by user.")

    except Exception as exc:
        print(f"\nFatal error: {exc}")
