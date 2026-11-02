import typer

app = typer.Typer(help="Learn rules for your coding agent from its past mistakes.")


@app.callback()
def main() -> None:
    """rulesmith command line."""


@app.command()
def scan(commits: int = typer.Option(500, help="How many recent commits to read.")) -> None:
    """Find repeated agent mistakes and propose rules."""
    typer.echo(f"scan is not implemented yet (commits={commits})")
