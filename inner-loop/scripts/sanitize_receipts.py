#!/usr/bin/env python3
"""Replace exact secret values in text/JSON receipts without printing them."""
from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--secret-file", required=True, type=Path)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    secret = args.secret_file.read_text().strip()
    if not secret:
        raise SystemExit("secret file is empty")
    for path in args.paths:
        text = path.read_text(errors="surrogateescape")
        count = text.count(secret)
        path.write_text(text.replace(secret, "<redacted>"), errors="surrogateescape")
        print(f"SANITIZE_RECEIPT path={path} replacements={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
