"""Sample e-rechnung-inbox results, rebuilt from its --json output."""

import json
from pathlib import Path

from erechnung_inbox import CheckResult, Finding, Profile, Severity, Status, Syntax
from erechnung_inbox.models import Container

RESULTS = Path(__file__).parent / "fixtures" / "check_results"


def load_check_result(name: str) -> CheckResult:
    """Rebuild a CheckResult from e-rechnung-inbox's --json output."""
    data = json.loads((RESULTS / f"{name}.json").read_text())
    findings = [
        Finding(
            rule_id=f["rule_id"],
            severity=Severity(f["severity"]),
            location=f["location"],
            message=f["message"],
        )
        for f in data.pop("findings")
    ]
    enums = {"status": Status, "container": Container, "syntax": Syntax, "profile": Profile}
    for key, enum in enums.items():
        if data[key] is not None:
            data[key] = enum(data[key])
    return CheckResult(findings=findings, **data)
