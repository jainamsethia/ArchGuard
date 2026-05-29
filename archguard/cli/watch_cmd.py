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

    def on_modified(self, event):
        if event.src_path.endswith(".py") and not event.is_directory:
            self._schedule_analysis()

    def _schedule_analysis(self):
        if self._debounce_timer:
            self._debounce_timer.cancel()
        # Debounce: wait 500ms after last change before re-running
        self._debounce_timer = threading.Timer(0.5, self._run_analysis)
        self._debounce_timer.start()

    def _run_analysis(self):
        self.console.rule("[bold blue]File changed — re-analyzing...[/bold blue]")
        
        # We need to capture the score from the command execution.
        # But _analyze_command_impl returns an exit code (0 or 1), not the score.
        # We'll run it, but we can't easily get the score without changing the return type.
        # However, the user prompt asked to get `new_score = _analyze_command_impl(self.opts)`
        # Let's adjust it slightly, or we can just run it. If _analyze_command_impl doesn't return the score,
        # we can't show the delta. Wait, does _analyze_command_impl return a score now?
        # Let's check analyze_cmd.py. No, it returns `int` (EXIT_SUCCESS, etc).
        # We can read the score from the audit log or just let _analyze_command_impl print it.
        # Let's just run it for now.
        _analyze_command_impl(self.opts)
        
        # If the user's snippet expected `new_score`, I should try to read it from the audit log 
        # or maybe the prompt just meant conceptually.
        # Let's read from audit log:
        try:
            from archguard.audit.logger import AuditLogger
            last_run = AuditLogger(self.opts.repo.resolve() / ".archguard-cache" / "audit.jsonl").read_last_run()
            if last_run and "score" in last_run:
                new_score = last_run["score"] / 100.0  # normalize back
                if self._last_score is not None:
                    delta = new_score - self._last_score
                    if delta > 0.001:
                        self.console.print(f"[red]ArchDebt increased: +{delta:.3f}[/red]")
                    elif delta < -0.001:
                        self.console.print(f"[green]ArchDebt improved: {delta:.3f}[/green]")
                self._last_score = new_score
        except Exception:
            pass


def run_watch_mode(opts: AnalyzeOptions, repo_path: Path):
    console = Console()
    handler = AnalysisEventHandler(opts, console)
    observer = Observer()
    observer.schedule(handler, str(repo_path), recursive=True)
    observer.start()
    
    # Run the initial analysis
    handler._run_analysis()
    
    console.print(f"[bold green]Watching {repo_path} for changes. Ctrl+C to stop.[/bold green]")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
