"""Export the generator-ready FastAPI contract without starting the service."""
from __future__ import annotations

import json
from pathlib import Path

from app.main import app


def main() -> None:
    target = Path("docs/openapi.json")
    target.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
