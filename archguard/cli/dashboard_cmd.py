import typer

dashboard_app = typer.Typer(
    name="dashboard",
    help="Start the ArchGuard live dashboard. Authentication requires ARCHGUARD_DASHBOARD_TOKEN.",
    no_args_is_help=False,
)


@dashboard_app.callback(invoke_without_command=True)
def dashboard_cmd(
    port: int = typer.Option(8080, "--port", "-p", help="Port to run the dashboard on"),
    host: str = typer.Option(
        "127.0.0.1", "--host", help="Host to bind the dashboard to"
    ),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on changes"),
    allow_remote: bool = typer.Option(
        False, "--allow-remote", help="Allow remote access without authentication"
    ),
) -> None:
    """Start the ArchGuard live dashboard.

    To secure the API endpoints, set the ARCHGUARD_DASHBOARD_TOKEN environment
    variable. When set, all /api/* requests must include an Authorization: Bearer
    header with this token.
    """
    try:
        import uvicorn
    except ImportError:
        typer.echo("Dashboard requires: pip install archguard[dashboard]", err=True)
        raise typer.Exit(2)

    import os

    if allow_remote:
        os.environ["ARCHGUARD_DASHBOARD_ALLOW_REMOTE"] = "1"
        typer.echo(
            "[SECURITY WARNING] Remote access without authentication is enabled!",
            err=True,
        )

    typer.echo(f"Starting ArchGuard dashboard at http://{host}:{port}")
    typer.echo(
        "Dashboard running. Set ARCHGUARD_DASHBOARD_TOKEN to secure remote access."
    )
    uvicorn.run("archguard.dashboard.app:app", host=host, port=port, reload=reload)
