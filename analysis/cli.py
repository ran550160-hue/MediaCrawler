from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from analysis.loaders import AnalysisDataError, load_dataframe, resolve_input_files
from analysis.reports import AnalysisReportError, generate_analysis_report


app = typer.Typer(add_completion=False, help="Analyze MediaCrawler output files.")
PLATFORM_OUTPUT_DIRS = {
    "dy": "douyin",
}


def _load_optional(path: Optional[Path]):
    if path is None:
        return None
    return load_dataframe(path)


@app.callback(invoke_without_command=True)
def main(
    platform: str = typer.Option(..., "--platform", help="Platform to analyze: xhs or dy"),
    crawler_type: str = typer.Option("search", "--crawler-type", help="Crawler type used in output file names"),
    contents: Optional[Path] = typer.Option(None, "--contents", help="Explicit contents data file"),
    comments: Optional[Path] = typer.Option(None, "--comments", help="Explicit comments data file"),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir", help="Directory for analysis outputs"),
    top_n: int = typer.Option(20, "--top-n", help="Number of top records to include"),
) -> None:
    platform = platform.strip().lower()
    if platform not in {"xhs", "dy"}:
        raise typer.BadParameter("platform must be 'xhs' or 'dy'")

    resolved_contents, resolved_comments = resolve_input_files(
        platform=platform,
        crawler_type=crawler_type,
        contents=contents,
        comments=comments,
    )
    if resolved_contents is None and resolved_comments is None:
        typer.echo(
            f"Analysis failed: no contents or comments file found for platform={platform}, crawler_type={crawler_type}",
            err=True,
        )
        raise typer.Exit(code=1)

    output_platform_dir = PLATFORM_OUTPUT_DIRS.get(platform, platform)
    output = output_dir or Path("data") / output_platform_dir / "analysis"

    try:
        contents_df = _load_optional(resolved_contents)
        comments_df = _load_optional(resolved_comments)
        summary = generate_analysis_report(
            platform=platform,
            contents_df=contents_df,
            comments_df=comments_df,
            output_dir=output,
            top_n=top_n,
        )
    except (AnalysisDataError, AnalysisReportError) as exc:
        typer.echo(f"Analysis failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("Analysis complete")
    typer.echo(f"Contents: {resolved_contents or 'not found'}")
    typer.echo(f"Comments: {resolved_comments or 'not found'}")
    typer.echo(f"Report: {summary['outputs']['html']}")
    typer.echo(f"Summary: {summary['outputs']['summary']}")


if __name__ == "__main__":
    app()
