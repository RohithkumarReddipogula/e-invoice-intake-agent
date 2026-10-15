"""The e-rechnung-inbox commit is pinned twice: in pyproject.toml and in the kosit CI job."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_ci_builds_the_validator_from_the_pinned_commit():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    dep = next(d for d in pyproject["project"]["dependencies"] if d.startswith("e-rechnung-inbox"))
    dep_sha = dep.rsplit("@", 1)[1]
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    ci_sha = re.search(r"ERECHNUNG_INBOX_SHA:\s*([0-9a-f]{40})", ci)
    assert re.fullmatch(r"[0-9a-f]{40}", dep_sha), dep
    assert ci_sha is not None
    assert ci_sha.group(1) == dep_sha
