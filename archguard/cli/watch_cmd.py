import typing
import time
import threading
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from rich.console import Console

from archguard.cli.analyze_cmd import AnalyzeOptions, _analyze_command_impl


class AnalysisEventHandler(FileSystemEventHandler):
    def __init__(self, opts: AnalyzeOptions, console: Console):
        self.opts = opts
        self.console = console
        self._last_score: float | None = None
        self._debounce_timer: threading.Timer | None = None

    def on_modified(self, event: typing.Any) -> None:
        if event.src_path.endswith(".py") and not event.is_directory:
            self._schedule_analysis()

    def _schedule_analysis(self) -> None:
        if self._debounce_timer:
            self._debounce_timer.cancel()
        # Debounce: wait 500ms after last change before re-running
        self._debounce_timer = threading.Timer(0.5, self._run_analysis)
        self._debounce_timer.start()

    def _run_analysis(self) -> None:
        """Run analysis on file changes and report the ArchDebt delta."""
        self.console.rule("[bold blue]File changed — re-analyzing...[/bold blue]")
        exit_code, result = _analyze_command_impl(self.opts)
        if result is not None:
            new_score = result.archdebt.composite_score
            if self._last_score is not None:
                delta = new_score - self._last_score
                sign = "+" if delta >= 0 else ""
                self.console.print(f"ArchDebt delta: {sign}{delta:.3f}")
            self._last_score = new_score


def run_watch_mode(opts: AnalyzeOptions, repo_path: Path) -> None:
    console = Console()
    handler = AnalysisEventHandler(opts, console)
    observer = Observer()
    observer.schedule(handler, str(repo_path), recursive=True)
    observer.start()

    # Run the initial analysis
    handler._run_analysis()

    console.print(
        f"[bold green]Watching {repo_path} for changes. Ctrl+C to stop.[/bold green]"
    )
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
