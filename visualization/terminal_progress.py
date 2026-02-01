"""
Tier 1: Terminal Progress Bars

Real-time progress visualization using rich library.
"""

from typing import Dict, Optional
from rich.progress import Progress, TaskID, BarColumn, TextColumn, TimeRemainingColumn, SpinnerColumn
from rich.live import Live
from rich.console import Console
from datetime import datetime

console = Console()


class PipelineProgress:
    """
    Real-time progress UI for pipeline execution.

    Usage:
        progress_ui = PipelineProgress()
        with Live(progress_ui.progress):
            task_id = progress_ui.start_collector("github")
            # ... collector runs ...
            progress_ui.complete_collector("github", success=True, signals=15)
    """

    def __init__(self):
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console,
            transient=False,
        )
        self.tasks: Dict[str, TaskID] = {}
        self.started_at: Dict[str, datetime] = {}

    def start_collector(self, collector_name: str, total: int = 10) -> TaskID:
        """
        Start progress bar for a collector.

        Args:
            collector_name: Name of the collector
            total: Total steps (default: 10 for indeterminate progress)

        Returns:
            TaskID for updating progress
        """
        description = f"⏳ {collector_name:<20}"
        task_id = self.progress.add_task(description, total=total)
        self.tasks[collector_name] = task_id
        self.started_at[collector_name] = datetime.now()
        return task_id

    def update_collector(self, collector_name: str, completed: int):
        """
        Update progress for a collector.

        Args:
            collector_name: Name of the collector
            completed: Number of completed steps
        """
        if collector_name in self.tasks:
            task_id = self.tasks[collector_name]
            self.progress.update(task_id, completed=completed)

    def complete_collector(
        self,
        collector_name: str,
        success: bool = True,
        signals: Optional[int] = None,
        error: Optional[str] = None
    ):
        """
        Mark collector as complete.

        Args:
            collector_name: Name of the collector
            success: Whether collector succeeded
            signals: Number of signals found (optional)
            error: Error message if failed (optional)
        """
        if collector_name not in self.tasks:
            return

        task_id = self.tasks[collector_name]
        elapsed = (datetime.now() - self.started_at[collector_name]).total_seconds()

        if success:
            icon = "✓"
            color = "green"
            suffix = f"→ {signals} signals" if signals is not None else ""
        else:
            icon = "✗"
            color = "red"
            suffix = f"→ {error}" if error else "→ failed"

        description = f"{icon} [{color}]{collector_name:<20}[/{color}] ({elapsed:.1f}s) {suffix}"

        self.progress.update(
            task_id,
            description=description,
            completed=10,  # Mark as 100%
        )

    def add_summary(self, message: str):
        """Add a summary message below progress bars."""
        console.print(f"\n{message}")


# Example usage
if __name__ == "__main__":
    import asyncio
    import time

    async def simulate_collector(name: str, duration: float, signals: int):
        """Simulate a collector run."""
        steps = 10
        for i in range(steps):
            await asyncio.sleep(duration / steps)
            yield i + 1
        return signals

    async def main():
        progress_ui = PipelineProgress()

        collectors = [
            ("github", 2.5, 15),
            ("sec_edgar", 5.0, 8),
            ("companies_house", 1.5, 12),
        ]

        with Live(progress_ui.progress, refresh_per_second=10):
            for name, duration, signals in collectors:
                progress_ui.start_collector(name)

            # Simulate concurrent execution
            for name, duration, signals in collectors:
                async for step in simulate_collector(name, duration, signals):
                    progress_ui.update_collector(name, step)

                progress_ui.complete_collector(name, success=True, signals=signals)

        progress_ui.add_summary("\n✅ Collection complete: 35 signals in 9.0s")

    asyncio.run(main())
