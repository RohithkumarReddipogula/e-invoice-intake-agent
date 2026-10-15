"""Disk cache for LLM answers. One JSON file per answer, committed to git for the eval set,
so CI and anyone cloning the repo can run without an API key."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def schema_hash(schema: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()


def cache_key(*, model: str, prompt_version: str, schema_sha: str, text: str) -> str:
    payload = json.dumps(
        {"model": model, "prompt_version": prompt_version, "schema": schema_sha, "text": text},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class DiskCache:
    def __init__(self, directory: Path):
        self.directory = directory

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        try:
            return json.loads(self._path(key).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def put(self, key: str, entry: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        # Write to a temp file and rename, so a crash never leaves a half-written entry.
        fd, tmp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(entry, f, indent=2, sort_keys=True, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp, self._path(key))
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
