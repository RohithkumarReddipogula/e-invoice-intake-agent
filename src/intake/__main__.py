"""python -m intake [--port 8000]: run the intake app on 127.0.0.1."""

from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    # Local only: v1 has no login (SPEC section 8.2).
    uvicorn.run("intake.app:create_app", factory=True, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
