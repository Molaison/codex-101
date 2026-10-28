#!/usr/bin/env python3
"""Generate or verify the release's component digests (Codex itself uses npm latest)."""

import argparse
import hashlib
import json
from pathlib import Path

import codex_provider


def manifest(root):
    filenames = ("install.sh", "install.ps1", "codex_provider.py", "model_catalog.json", "install_lifecycle.py", "magpie_support.py", "install-pi.sh")
    return {"schema": 1, "release": "v" + codex_provider.VERSION, "codex_channel": "stable",
            "files": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in filenames}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="validate manifest without writing")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    target = root / "release_manifest.json"
    expected = json.dumps(manifest(root), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not target.is_file() or target.read_text(encoding="utf-8") != expected:
            parser.error("release_manifest.json does not match current source; rebuild before release")
        print("release_manifest.json matches all release components")
    else:
        target.write_text(expected, encoding="utf-8")
        print("wrote release_manifest.json")


if __name__ == "__main__":
    main()
