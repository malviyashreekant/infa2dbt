"""Command line interface.

Exit codes: 0 ok | 1 a gate failed (dropped component, bad SQL, parse error)
            | 2 manual-review items remain and --fail-on-review was given
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from .config import load_config
from .pipeline import collect_inputs, convert_paths
from .validate import validate_dir

app = typer.Typer(add_completion=False, help="Convert Informatica PowerCenter XML into a dbt project for Oracle.")


@app.command()
def convert(
    source: Path = typer.Argument(..., exists=True, help="One XML export, or a folder of them."),
    out: Path = typer.Option(Path("dbt_out"), "--out", "-o", help="Directory for the generated dbt project."),
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="YAML config (see README)."),
    fail_on_review: bool = typer.Option(False, "--fail-on-review", help="Exit 2 if any item needs manual review."),
) -> None:
    """Convert mappings, run the quality gates and write reports."""
    cfg = load_config(config)
    paths = collect_inputs(source)
    if not paths:
        typer.echo("no XML files found", err=True)
        raise typer.Exit(1)
    try:
        res = convert_paths(paths, out, cfg)
    except Exception as e:  # parse errors surface as a gate failure, never a traceback
        typer.echo(f"conversion failed: {type(e).__name__}: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"mappings: {len(res.plans)}  models: {res.models}  review items: {res.review_count}")
    typer.echo(f"output: {out}  (reports in {out / 'reports'})")
    if not res.findings.ok:
        typer.echo("\nGATE FAILURES:", err=True)
        for e in res.findings.errors:
            typer.echo(f"  - {e}", err=True)
        raise typer.Exit(1)
    if res.review_count and fail_on_review:
        typer.echo(f"{res.review_count} item(s) need manual review: see reports/manual_review.md", err=True)
        raise typer.Exit(2)
    typer.echo("all gates passed" + (f"; {res.review_count} item(s) still need manual review" if res.review_count else ""))


@app.command()
def check(project: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    """Re-run the static SQL checks on a generated project (for CI)."""
    f = validate_dir(project)
    if not f.ok:
        for e in f.errors:
            typer.echo(f"  - {e}", err=True)
        raise typer.Exit(1)
    typer.echo("static checks passed")


if __name__ == "__main__":
    app()
