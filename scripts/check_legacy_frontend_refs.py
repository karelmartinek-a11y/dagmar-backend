from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN = [
    "device" + "Fingerprint",
    "claim" + "-" + "token",
    "claim" + "Token",
    "register" + "Instance",
    "get" + "Instance" + "Status",
    "activation" + "State",
    '"/' + "pending" + '"',
    "Pending" + "Page",
    '"/' + "brand" + '/"',
]

SCAN_EXT = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sh"}
SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules"}

violations: list[str] = []
for path in ROOT.rglob("*"):
    if any(part in SKIP_DIRS for part in path.parts):
        continue
    if not path.is_file() or path.suffix.lower() not in SCAN_EXT:
        continue
    text = path.read_text(encoding="utf-8", errors="ignore")
    for needle in FORBIDDEN:
        if needle in text:
            violations.append(f"{path.relative_to(ROOT)} :: {needle}")

if violations:
    print("Legacy references found:")
    for v in violations:
        print(f" - {v}")
    sys.exit(1)

print("No legacy frontend references found.")
