"""python -m intake [--port 8000]: run the intake app.

Binds to 127.0.0.1 (v1 has no login, SPEC section 8.2). Only in demo mode
(INTAKE_DEMO_MODE=1) it binds to 0.0.0.0 and takes the port from $PORT (e.g. on Railway).
"""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn
from pydantic import ValidationError

from .config import Settings


def bind_address(settings: Settings, port: int | None) -> tuple[str, int]:
    if settings.demo_mode:
        return "0.0.0.0", int(os.environ.get("PORT") or port or 8000)
    return "127.0.0.1", port or 8000


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    try:
        settings = Settings()
    except ValidationError as exc:
        sys.exit(f"Invalid settings: {exc.errors()[0]['msg']}")
    host, port = bind_address(settings, args.port)
    uvicorn.run("intake.app:create_app", factory=True, host=host, port=port)


if __name__ == "__main__":
    main()
