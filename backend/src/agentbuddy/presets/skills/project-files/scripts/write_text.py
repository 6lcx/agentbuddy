"""Trusted preset script; writes one text file inside the current workspace."""

import sys
from pathlib import Path

if len(sys.argv) != 3:
    raise SystemExit("Usage: write_text.py relative_path text")
root = Path.cwd().resolve()
target = (root / sys.argv[1]).resolve()
if not target.is_relative_to(root) or target == root:
    raise SystemExit("Path must be inside the project workspace")
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(sys.argv[2], encoding="utf-8")
print(target.relative_to(root).as_posix())
