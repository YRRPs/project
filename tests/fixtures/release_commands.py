from __future__ import annotations

from pathlib import Path

UNZIP = """#!/usr/bin/env python3
import sys, zipfile
args = sys.argv[1:]
if args[0] == '-tq': sys.exit(0)
if args[0] == '-Z1':
    print('\\n'.join(zipfile.ZipFile(args[1]).namelist()))
elif args[0] == '-p':
    sys.stdout.buffer.write(zipfile.ZipFile(args[1]).read(args[2]))
else: sys.exit(2)
"""
OPENSSL = "#!/bin/sh\nprintf synthetic-der\n"


def write_executable(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o755)
    return path


def write_release_command_stubs(bin_dir: Path) -> None:
    write_executable(bin_dir / "unzip", UNZIP)
    write_executable(bin_dir / "openssl", OPENSSL)
