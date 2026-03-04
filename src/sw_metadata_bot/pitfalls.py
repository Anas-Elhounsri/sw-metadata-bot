"""Pitfalls data loading and parsing."""

import json
import re
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

from . import __version__


def load_pitfalls(file_path: Path) -> dict:
    """Load pitfalls from JSON-LD file."""
    with open(file_path, encoding="utf-8") as f:
        return json.load(f)


def get_repository_url(data: dict) -> str:
    """Extract repository URL from pitfalls data."""
    return data.get("assessedSoftware", {}).get("url", "")


def _get_check_code(check: dict) -> str:
    """Extract check code (e.g. P001/W004) from a check entry."""
    check_id = str(check.get("checkId", ""))
    if re.match(r"^[PW]\d+", check_id):
        return check_id

    evidence = str(check.get("evidence", ""))
    match = re.search(r"\b([PW]\d{3,})\b", evidence)
    if match:
        return match.group(1)

    return ""


def get_pitfalls_list(data: dict) -> list[dict]:
    """Get list of pitfall checks from data."""
    return [
        check
        for check in data.get("checks", [])
        if _get_check_code(check).startswith("P")
    ]


def get_warnings_list(data: dict) -> list[dict]:
    """Get list of warning checks from data."""
    return [
        check
        for check in data.get("checks", [])
        if _get_check_code(check).startswith("W")
    ]


def get_metacheck_version(data: dict) -> str:
    """Get the version of RSMetacheck used for analysis."""
    return version("metacheck")


def format_report(repo_url: str, data: dict) -> str:
    """Format pitfalls data into a readable report."""
    pitfalls = get_pitfalls_list(data)
    warnings = get_warnings_list(data)

    report = "# Metadata Quality Report\n\n"
    report += f"**Repository:** {repo_url}\n"
    report += f"**Analysis Date:** {datetime.now().strftime('%Y-%m-%d')}\n"
    report += f"**sw-metadata-bot version:** {__version__}\n"
    report += f"**RSMetacheck version:** {get_metacheck_version(data)}\n\n"

    if pitfalls:
        report += f"## 🔴 Pitfalls ({len(pitfalls)})\n\n"
        for p in pitfalls:
            report += f"### {p['checkId']}\n"
            report += f"{p.get('process', 'No description')}\n"
            report += f"{p.get('evidence', 'No details')}\n\n"
            if p.get("suggestion"):
                report += f"**Suggestion:** {p['suggestion']}\n\n"

    if warnings:
        report += f"## ⚠️ Warnings ({len(warnings)})\n\n"
        for w in warnings:
            report += f"### {w['checkId']}\n"
            report += f"{w.get('evidence', 'No details')}\n\n"
            if w.get("suggestion"):
                report += f"**Suggestion:** {w['suggestion']}\n\n"

    return report


DEFAULT_GREETINGS = """\
    Hi maintainers,
Your repository is part of our metadata quality improvement initiative. We've automatically analyzed your repository's metadata and discovered some issues that could be fixed.
"""

ISSUE_TEMPLATE = """\
{greetings}

This automated issue includes:
- Detected metadata pitfalls and warnings
- Suggestions for fixing each issue

## Context
This analysis is performed by the [CodeMetaSoft](https://w3id.org/codemetasoft) project to help improve research software quality.

{report}
---

This report was generated automatically by [sw-metadata-bot](https://github.com/SoftwareUnderstanding/sw-metadata-bot).

If you're not interested in participating, please comment "unsubscribe" and we will remove your repository from our list.
"""


def create_issue_body(report: str, custom_message: str | None) -> str:
    """Wrap report in issue template using optional custom message or default greetings."""
    if not custom_message:
        custom_message = DEFAULT_GREETINGS

    body = ISSUE_TEMPLATE.format(report=report, greetings=custom_message)

    return body
