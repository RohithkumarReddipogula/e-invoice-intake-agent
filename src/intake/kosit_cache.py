"""KoSIT results cached on disk and committed (like the LLM cache).

Filled once with Docker (`python -m eval.run --fill-kosit`); after that the eval, CI and the
demo run without Docker. Keyed by the SHA-256 of the file, so a changed file is never matched.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from erechnung_inbox import CheckResult, Finding, Profile, Severity, Status, Syntax, check_files
from erechnung_inbox.models import Container

DEFAULT_DIR = Path("eval/kosit_cache")


class KositCacheMiss(Exception):
    pass


def _key(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def to_json(result: CheckResult) -> dict:
    data = result.to_dict()
    data.pop("exit_code", None)
    data["file"] = Path(data["file"]).name  # no local paths in the committed cache
    return data


def from_json(data: dict) -> CheckResult:
    data = dict(data)
    findings = [
        Finding(f["rule_id"], Severity(f["severity"]), f["location"], f["message"])
        for f in data.pop("findings")
    ]
    enums = {"status": Status, "container": Container, "syntax": Syntax, "profile": Profile}
    for key, enum in enums.items():
        if data.get(key) is not None:
            data[key] = enum(data[key])
    return CheckResult(findings=findings, **data)


class KositCache:
    def __init__(self, directory: Path = DEFAULT_DIR):
        self.directory = directory

    def path_for(self, file: Path) -> Path:
        return self.directory / f"{_key(file)}.json"

    def __call__(self, path: Path) -> CheckResult:
        """A StructuredValidator for the pipeline: cached result or a loud failure."""
        cached = self.path_for(path)
        if not cached.exists():
            raise KositCacheMiss(
                f"No cached KoSIT result for {path.name}; run `python -m eval.run --fill-kosit` "
                "with Docker running."
            )
        return from_json(json.loads(cached.read_text(encoding="utf-8")))

    def fill(self, files: list[Path]) -> tuple[int, int]:
        """Validate the files that are not cached yet, in one validator run.
        Returns (newly cached, already cached)."""
        missing = [f for f in files if not self.path_for(f).exists()]
        if missing:
            results = check_files(missing)
            errors = [r for r in results if r.status is Status.ERROR]
            if errors:
                raise RuntimeError(f"KoSIT could not run: {errors[0].reason}")
            self.directory.mkdir(parents=True, exist_ok=True)
            for file, result in zip(missing, results, strict=True):
                self.path_for(file).write_text(
                    json.dumps(to_json(result), indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
        return len(missing), len(files) - len(missing)
