#!/usr/bin/env python3
"""Redact command-line bearer values from a Docker inspect receipt in place."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def sanitize(path: Path) -> bool:
    data = json.loads(path.read_text())
    changed = False
    for item in data if isinstance(data, list) else [data]:
        cmd = item.get("Config", {}).get("Cmd")
        if not isinstance(cmd, list):
            continue
        for index, value in enumerate(cmd[:-1]):
            if value == "--api-key" and cmd[index + 1] != "<redacted>":
                cmd[index + 1] = "<redacted>"
                changed = True
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.paths:
        changed = sanitize(path)
        print(f"SANITIZE_INSPECT path={path} changed={str(changed).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
