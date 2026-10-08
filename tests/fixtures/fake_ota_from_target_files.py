#!/usr/bin/env python3
"""Stand-in for AOSP ota_from_target_files that writes a synthetic incremental OTA."""
import json
import os
import sys
import zipfile
from pathlib import Path

args = sys.argv[1:]
Path(os.environ["FAKE_OTA_ARGS"]).write_text(json.dumps(args))
password_file = os.environ.get("ANDROID_PW_FILE", "")
Path(os.environ["FAKE_OTA_PASSWORDS"]).write_text(Path(password_file).read_text() if password_file else "")
for line in filter(None, os.environ.get("FAKE_OTA_WARNINGS", "").split("|")):
    print(line)
metadata = "\n".join(
    (
        "ota-type=AB",
        f"post-build={os.environ['FAKE_POST_BUILD']}",
        f"post-timestamp={os.environ['FAKE_POST_TIMESTAMP']}",
        f"pre-build-incremental={os.environ['FAKE_PRE_INCREMENTAL']}",
        "ota-property-files=payload.bin:5:6,metadata:7:8",
    )
)
with zipfile.ZipFile(args[-1], "w") as archive:
    archive.writestr("META-INF/com/android/metadata", metadata + "\n")
    archive.writestr("META-INF/com/android/otacert", b"synthetic-certificate")
    archive.writestr("payload.bin", b"synthetic incremental payload")
