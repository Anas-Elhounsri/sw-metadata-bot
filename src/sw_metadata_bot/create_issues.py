"""Create issues command - main logic."""

import json
import logging
from pathlib import Path

import click

from . import github_api, gitlab_api, pitfalls

logger = logging.getLogger(__name__)


def detect_platform(url: str) -> str:
    """Detect platform (GitHub, GitLab, etc.) from repository URL."""
    url = url.lower()
    if "github.com" in url:
        return "github"
    elif "gitlab.com" in url:
        return "gitlab.com"
    elif "gitlab" in url:
        return "gitlab"
    else:
        raise ValueError(f"Unsupported repository platform in URL: {url}")


def _normalize_repo_url(url: str) -> str:
    """Normalize repository URL for matching between datasets."""
    return url.strip().rstrip("/")


def load_config(config_path: Path | None) -> dict:
    """Load issue configuration from JSON file."""
    if config_path is None:
        return {"custom_message": None}
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


def _load_repository_list(file_path: Path) -> set[str]:
    """Load repository URLs from a JSON file with a 'repositories' key."""
    with open(file_path, encoding="utf-8") as f:
        data = json.load(f)

    repositories = data.get("repositories", [])
    if not isinstance(repositories, list):
        raise click.ClickException(
            f"Invalid format in {file_path}: 'repositories' must be a list"
        )

    return {_normalize_repo_url(url) for url in repositories if isinstance(url, str)}


def _extract_check_ids(checks: list[dict]) -> tuple[list[str], list[str]]:
    """Extract unique pitfall and warning codes from checks."""
    pitfall_ids: list[str] = []
    warning_ids: list[str] = []

    for check in checks:
        pitfall_url = str(check.get("pitfall", ""))
        code = pitfall_url.split("#")[-1] if "#" in pitfall_url else pitfall_url
        if not code:
            continue

        if code.startswith("P") and code not in pitfall_ids:
            pitfall_ids.append(code)
        elif code.startswith("W") and code not in warning_ids:
            warning_ids.append(code)

    return pitfall_ids, warning_ids


def _safe_get_metacheck_version(data: dict) -> str:
    """Get metacheck version without failing issue reporting."""
    try:
        return pitfalls.get_metacheck_version(data)
    except Exception:
        return "unknown"


def _get_analysis_date(data: dict) -> str:
    """Get analysis date from pitfalls payload."""
    return str(data.get("dateCreated", "unknown"))


def _build_report_entry(
    *,
    repo_url: str | None,
    platform: str | None,
    pitfalls_count: int | None,
    warnings_count: int | None,
    issue_url: str | None,
    analysis_date: str,
    bot_version: str,
    metacheck_version: str,
    pitfalls_ids: list[str] | None,
    warnings_ids: list[str] | None,
    file_path: Path | None = None,
    error: str | None = None,
) -> dict[str, object]:
    """Build a report entry with common metadata and optional fields."""
    entry: dict[str, object] = {
        "repo_url": repo_url,
        "platform": platform,
        "pitfalls_count": pitfalls_count,
        "warnings_count": warnings_count,
        "analysis_date": analysis_date,
        "sw_metadata_bot_version": bot_version,
        "rsmetacheck_version": metacheck_version,
        "pitfalls_ids": pitfalls_ids or [],
        "warnings_ids": warnings_ids or [],
    }

    if issue_url is not None:
        entry["issue_url"] = issue_url
    if file_path is not None:
        entry["file"] = str(file_path)
    if error is not None:
        entry["error"] = error

    return entry


@click.command()
@click.option(
    "--pitfalls-output-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    required=True,
    help="Directory containing pitfalls JSON-LD files from metacheck analysis.",
)
@click.option(
    "--issues-dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=Path.cwd(),
    help="Directory to save issue bodies and reports.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Simulate issue creation without actually posting to repositories.",
)
@click.option(
    "--log-level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    default="INFO",
    help="Logging level.",
)
@click.option(
    "--opt-outs-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="JSON file containing repositories to exclude from issue creation.",
)
@click.option(
    "--issue-config-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="JSON file containing issue configuration.",
)
def create_issues_command(
    pitfalls_output_dir: Path,
    issues_dir: Path,
    dry_run: bool,
    log_level: str,
    opt_outs_file: Path | None,
    issue_config_file: Path | None,
):
    """
    Create issues in repositories based on metadata analysis results.

    This command processes pitfalls files generated by the metacheck tool
    and creates corresponding issues in the analyzed repositories.
    """
    # Setup logging
    logging.basicConfig(
        level=log_level.upper(),
        format="%(levelname)s: %(message)s",
    )

    # Create output directory
    issues_dir.mkdir(parents=True, exist_ok=True)

    # Initialize API clients
    github, gitlab = None, None

    mode = "DRY RUN" if dry_run else "PRODUCTION"
    click.echo(f"\n{'=' * 60}")
    click.echo(f"Creating issues [{mode}]")
    click.echo(f"{'=' * 60}\n")

    issue_config = load_config(issue_config_file)

    opt_out_repos: set[str] = set()
    if opt_outs_file is not None:
        opt_out_repos = _load_repository_list(opt_outs_file)
        click.echo(
            f"Loaded {len(opt_out_repos)} opt-out repositories from: {opt_outs_file}\n"
        )

    # Find pitfalls files
    pitfalls_files = sorted(pitfalls_output_dir.glob("*.jsonld"))
    if not pitfalls_files:
        click.echo(f"No pitfalls files found in {pitfalls_output_dir}", err=True)
        return

    click.echo(f"Found {len(pitfalls_files)} pitfalls files to process\n")

    # Process each file
    created = []
    failed = []
    skipped = []
    bot_version = pitfalls.__version__

    for i, file_path in enumerate(pitfalls_files, 1):
        click.echo(f"[{i}/{len(pitfalls_files)}] Processing: {file_path.name}")

        repo_url: str | None = None
        platform: str | None = None
        pitfalls_count: int | None = None
        warnings_count: int | None = None
        analysis_date: str = "unknown"
        metacheck_version: str = "unknown"
        pitfalls_ids: list[str] | None = None
        warnings_ids: list[str] | None = None

        try:
            # Load pitfalls
            data = pitfalls.load_pitfalls(file_path)
            repo_url = pitfalls.get_repository_url(data)
            pitfalls_list = pitfalls.get_pitfalls_list(data)
            warnings_list = pitfalls.get_warnings_list(data)
            pitfalls_count = len(pitfalls_list)
            warnings_count = len(warnings_list)
            analysis_date = _get_analysis_date(data)
            metacheck_version = _safe_get_metacheck_version(data)
            pitfalls_ids, warnings_ids = _extract_check_ids(data.get("checks", []))
            click.echo(f"  Repository: {repo_url}")

            if _normalize_repo_url(repo_url) in opt_out_repos:
                click.echo("  ↷ Skipped: repository is in opt-outs list")
                skipped.append({"repo_url": repo_url, "file": str(file_path)})
                click.echo()
                continue

            # Generate issue content
            report = pitfalls.format_report(repo_url, data)
            body = pitfalls.create_issue_body(
                report, issue_config.get("custom_message")
            )

            # Save issue body
            body_file = issues_dir / f"issue_body_{file_path.stem}.md"
            with open(body_file, "w", encoding="utf-8") as f:
                f.write(body)
            click.echo(f"  Issue body saved to: {body_file}")

            # Create issue
            platform = detect_platform(repo_url)
            click.echo(f"  Detected platform: {platform}")
            title = "Automated Metadata Quality Report from CodeMetaSoft"

            if platform == "github":
                if not github:
                    github = github_api.GitHubAPI(dry_run=dry_run)
                issue_url = github.create_issue(repo_url, title, body)
            elif platform == "gitlab.com":
                if not gitlab:
                    gitlab = gitlab_api.GitLabAPI(dry_run=dry_run)
                issue_url = gitlab.create_issue(repo_url, title, body)
            else:
                raise ValueError(f"Unsupported platform: {platform}")

            click.echo(f"  ✓ Issue created: {issue_url}")

            created.append(
                _build_report_entry(
                    repo_url=repo_url,
                    platform=platform,
                    pitfalls_count=pitfalls_count,
                    warnings_count=warnings_count,
                    issue_url=issue_url,
                    analysis_date=analysis_date,
                    bot_version=bot_version,
                    metacheck_version=metacheck_version,
                    pitfalls_ids=pitfalls_ids,
                    warnings_ids=warnings_ids,
                )
            )

        except Exception as e:
            click.echo(f"  ✗ Error: {e}", err=True)
            failed.append(
                _build_report_entry(
                    repo_url=repo_url,
                    platform=platform,
                    pitfalls_count=pitfalls_count,
                    warnings_count=warnings_count,
                    issue_url=None,
                    analysis_date=analysis_date,
                    bot_version=bot_version,
                    metacheck_version=metacheck_version,
                    pitfalls_ids=pitfalls_ids,
                    warnings_ids=warnings_ids,
                    file_path=file_path,
                    error=str(e),
                )
            )

        click.echo()

    # Save reports
    with open(issues_dir / "created_issues_report.json", "w") as f:
        json.dump(created, f, indent=2)
    click.echo(f"Created issues report: {issues_dir / 'created_issues_report.json'}")

    if failed:
        with open(issues_dir / "failed_issues_report.json", "w") as f:
            json.dump(failed, f, indent=2)
        click.echo(f"Failed issues report: {issues_dir / 'failed_issues_report.json'}")

    if skipped:
        with open(issues_dir / "skipped_issues_report.json", "w") as f:
            json.dump(skipped, f, indent=2)
        click.echo(
            f"Skipped issues report: {issues_dir / 'skipped_issues_report.json'}"
        )

    # Display summary
    click.echo(f"\n{'=' * 60}")
    click.echo(
        f"Summary: Created {len(created)} | Skipped {len(skipped)} | Failed {len(failed)}"
    )
    click.echo(f"{'=' * 60}\n")

    if failed:
        click.echo(f"⚠️  {len(failed)} issues failed to create.", err=True)
        return 1

    return 0
