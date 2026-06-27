#!/usr/bin/env python3
"""Download the Loghub BGL dataset for the honest (real-data) evaluation.

The synthetic generator is the default/offline path; this fetches the real
~708 MB BGL log so you can run the same pipeline on 214.7 days of production
logs. Use the **Loghub** version (not CFDR — they carry different labels).

    python scripts/download_bgl.py            # -> data/BGL.log
    logsig survey --bgl data/BGL.log --bin-widths 30 60 120
    logsig run    --bgl data/BGL.log --rank
"""

import os
import sys
import tarfile
import urllib.request

URL = "https://zenodo.org/record/3227177/files/BGL.tar.gz"
DEST_DIR = "data"
TARBALL = os.path.join(DEST_DIR, "BGL.tar.gz")
LOG = os.path.join(DEST_DIR, "BGL.log")


def main() -> int:
    os.makedirs(DEST_DIR, exist_ok=True)
    if os.path.exists(LOG):
        print(f"{LOG} already present ({os.path.getsize(LOG):,} bytes).")
        return 0
    print(f"Downloading {URL} (~708 MB) ...")
    try:
        urllib.request.urlretrieve(URL, TARBALL)
    except Exception as e:  # noqa: BLE001
        print(f"download failed: {e}\n"
              f"If your network blocks Zenodo, fetch {URL} manually and untar "
              f"into {DEST_DIR}/ as BGL.log.", file=sys.stderr)
        return 1
    print("Extracting ...")
    with tarfile.open(TARBALL) as tf:
        tf.extractall(DEST_DIR)
    # Loghub ships the file as 'BGL.log'; normalize if needed.
    if not os.path.exists(LOG):
        for name in os.listdir(DEST_DIR):
            if name.upper().startswith("BGL") and name.endswith(".log"):
                os.rename(os.path.join(DEST_DIR, name), LOG)
                break
    print(f"Done -> {LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
