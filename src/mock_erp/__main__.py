"""python -m mock_erp [--port 8001] [--fail-every N] [--db data/mock_erp.db] [--demo]"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from .app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock ERP for e-invoice-intake-agent.")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--db", type=Path, default=Path("data/mock_erp.db"))
    parser.add_argument(
        "--fail-every", type=int, default=0, help="answer every N-th POST with 503 (0 = never)"
    )
    parser.add_argument("--demo", action="store_true", help="add POST /api/reset (demo mode)")
    args = parser.parse_args()
    app = create_app(args.db, args.fail_every, demo=args.demo)
    uvicorn.run(app, host="127.0.0.1", port=args.port)  # never public, also in the demo


if __name__ == "__main__":
    main()
