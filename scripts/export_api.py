"""Export/check the default-prefix API schema without loading runtime credentials."""

import argparse
import json
from pathlib import Path

from agentbuddy.config import Settings
from agentbuddy.main import create_app

parser = argparse.ArgumentParser()
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
schema = create_app(Settings(api_prefix="/api/v1")).openapi()
target = root / "frontend/openapi.json"
if args.check:
    if json.loads(target.read_text(encoding="utf-8")) != schema:
        raise SystemExit(
            "OpenAPI snapshot differs; export and regenerate frontend types"
        )
    print("OpenAPI snapshot matches backend")
else:
    target.write_text(
        json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
