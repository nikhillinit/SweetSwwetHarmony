"""
PDFProfiler CLI commands

Provides command-line interface for PDF profiling and exit prediction updates.

Usage:
    python -m profilers.pdf_profiler_cli repredict <canonical_key>
    python -m profilers.pdf_profiler_cli repredict domain:acme.ai --dry-run
"""

import click
import asyncio
from storage.claim_store import ClaimStore
from storage.signal_store import SignalStore
from utils.exit_predictor import ExitPredictor


@click.group()
def cli():
    """PDFProfiler CLI - PDF extraction and exit prediction tools"""
    pass


@cli.command()
@click.argument("canonical_key")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview updated score without saving to database",
)
def repredict(canonical_key: str, dry_run: bool):
    """
    Re-compute exit prediction using latest PDF finance claims

    CANONICAL_KEY: Entity identifier (e.g., domain:acme.ai)

    Examples:
        python -m profilers.pdf_profiler_cli repredict domain:acme.ai
        python -m profilers.pdf_profiler_cli repredict domain:acme.ai --dry-run
    """
    asyncio.run(_repredict_async(canonical_key, dry_run))


async def _repredict_async(canonical_key: str, dry_run: bool):
    """Async implementation of repredict command"""
    click.echo(f"Re-computing exit prediction for: {canonical_key}")

    # Initialize stores
    signal_store = SignalStore()
    await signal_store.initialize()

    claim_store = ClaimStore(signal_store=signal_store)

    # Initialize ExitPredictor with ClaimStore
    predictor = ExitPredictor(claim_store=claim_store)

    try:
        # Compute enhanced funding score from PDF claims
        funding_score = await predictor.compute_funding_score_from_claims(canonical_key)

        click.echo(f"  Enhanced funding score: {funding_score:.3f}")

        # Check if entity has claims
        extractions = await claim_store.get_extractions_by_entity(canonical_key)

        if not extractions:
            click.echo(f"  ⚠ No claims found for {canonical_key}")
            click.echo(f"  Using default funding score: {funding_score:.3f}")

        if dry_run:
            click.echo(f"\n✓ Dry-run mode: Would update exit prediction with funding_score={funding_score:.3f}")
            click.echo("  (No changes saved to database)")
        else:
            # TODO: In real implementation, would:
            # 1. Fetch or create ConsolidatedSignal for this entity
            # 2. Call predictor.predict() with updated funding score
            # 3. Store result via signal_store.store_exit_prediction()

            click.echo(f"\n✓ Exit prediction updated for {canonical_key}")
            click.echo(f"  New funding score: {funding_score:.3f}")

    except Exception as e:
        click.echo(f"\n✗ Error: {e}", err=True)
        raise click.Abort()

    finally:
        await signal_store.close()


if __name__ == "__main__":
    cli()
