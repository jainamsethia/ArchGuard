import typer
from pathlib import Path
from typing import Annotated
from enum import Enum


class SyncDirection(str, Enum):
    push = "push"
    pull = "pull"
    both = "both"


def sync_cache(
    direction: Annotated[
        SyncDirection, typer.Argument(help="push | pull | both")
    ] = SyncDirection.both,
    bucket: str = typer.Option(
        ..., envvar="ARCHGUARD_S3_BUCKET", help="S3 bucket name"
    ),
    prefix: str = typer.Option("archguard-cache/", help="S3 key prefix"),
    cache_dir: Path = typer.Option(
        Path(".archguard-cache"), help="Local cache directory"
    ),
    profile: str = typer.Option(None, envvar="AWS_PROFILE", help="AWS profile"),
) -> None:
    """Sync the embedding cache with S3 for persistent CI caching."""
    try:
        import boto3
    except ImportError:
        typer.echo(
            "Error: boto3 is required for S3 sync. Install with: pip install boto3",
            err=True,
        )
        raise typer.Exit(1)

    from rich.console import Console

    console = Console()
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    s3 = session.client("s3")
    db_file = cache_dir / "embeddings.db"
    s3_key = f"{prefix}embeddings.db"

    if direction in (SyncDirection.pull, SyncDirection.both):
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, s3_key, str(db_file))
            console.print(f"[green]✓ Pulled cache from s3://{bucket}/{s3_key}[/green]")
        except s3.exceptions.ClientError as e:
            if "404" in str(e):
                console.print(
                    "[yellow]⚠ No cache found in S3 (first run). Starting fresh.[/yellow]"
                )
            else:
                raise

    if direction in (SyncDirection.push, SyncDirection.both):
        if db_file.exists():
            s3.upload_file(str(db_file), bucket, s3_key)
            console.print(f"[green]✓ Pushed cache to s3://{bucket}/{s3_key}[/green]")
        else:
            console.print("[yellow]⚠ No local cache file to push.[/yellow]")
