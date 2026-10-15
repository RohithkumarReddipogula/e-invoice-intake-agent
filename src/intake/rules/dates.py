"""R5 dates."""

from __future__ import annotations

from datetime import date

from ..models import InvoiceData
from .base import RuleResult, failed, passed

R5, R5_NAME = "R5", "dates"
MAX_AGE_YEARS = 2


def years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February in a year that has none
        return day.replace(year=day.year - years, day=28)


def check_dates(data: InvoiceData, today: date) -> RuleResult:
    issue = data.issue_date
    if issue is None:
        return failed(R5, R5_NAME, "Issue date is missing or not a valid date.")

    problems = []
    if issue > today:
        problems.append(f"issue date {issue} is in the future (today is {today})")
    oldest = years_before(today, MAX_AGE_YEARS)
    if issue < oldest:
        problems.append(f"issue date {issue} is more than {MAX_AGE_YEARS} years old")
    if data.due_date is not None and data.due_date < issue:
        problems.append(f"due date {data.due_date} is before the issue date {issue}")

    if problems:
        text = "; ".join(problems)
        return failed(R5, R5_NAME, text[0].upper() + text[1:] + ".")
    due = f", due {data.due_date}" if data.due_date else ""
    return passed(R5, R5_NAME, f"Dates are plausible (issued {issue}{due}).")
