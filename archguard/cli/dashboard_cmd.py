import typer

dashboard_app = typer.Typer(
    name="dashboard",
    help="Start the ArchGuard live dashboard.",
    no_args_is_help=False,
)


@dashboard_app.callback(invoke_without_command=True)
def dashboard_cmd(
    port: int = typer.Option(8080, "--port", "-p", help="Port to run the dashboard on"),
    host: str = typer.Option(
        "127.0.0.1", "--host", help="Host to bind the dashboard to"
    ),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on changes"),
) -> None:
    """Start the ArchGuard live dashboard."""
    try:
        import uvicorn
    except ImportError:
        typer.echo("Dashboard requires: pip install archguard[dashboard]", err=True)
        raise typer.Exit(2)

    typer.echo(f"Starting ArchGuard dashboard at http://{host}:{port}")
    uvicorn.run("archguard.dashboard.app:app", host=host, port=port, reload=reload)
