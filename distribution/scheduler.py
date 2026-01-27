"""
Digest Scheduler

Idempotent job for scheduling and processing weekly digest emails.
Designed for cron/platform triggers (no daemon loop).

Usage:
    # Run full digest flow (enqueue + process)
    python -m distribution.scheduler run_once

    # Preview digest for a recipient (dry run)
    python -m distribution.scheduler preview --recipient gp@example.com

    # Enqueue digests only (don't send)
    python -m distribution.scheduler enqueue

    # Process pending digest events only
    python -m distribution.scheduler process
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from typing import List, Optional

from distribution.config import DistributionConfig, load_config
from distribution.sender import EmailMessage, get_email_sender
from distribution.builders.digest_builder import DigestBuilder, DigestResult
from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)

# Event type for digest emails in outbox
EVENT_TYPE = "email_digest"


class DigestScheduler:
    """
    Schedules and processes weekly digest emails.

    Features:
    - Idempotent enqueue (same recipient+date = same idempotency key)
    - Event-type isolation (only claims email_digest events)
    - Logs digest_sent actions for anti-spam
    """

    def __init__(self, config: DistributionConfig):
        self.config = config
        self.builder = DigestBuilder(config)
        self.sender = get_email_sender(config)

    async def run_once(self, store: SignalStore) -> dict:
        """
        Run the full digest flow: enqueue + process.

        This is the main entry point for cron/scheduled triggers.
        Idempotent: safe to call multiple times on the same day.

        Returns:
            Summary dict with enqueued and sent counts
        """
        logger.info("Starting digest run_once")

        # Step 1: Enqueue digests for all recipients
        enqueued = await self.enqueue_digests(store)

        # Step 2: Process pending digest events
        sent = await self.process_pending(store)

        summary = {
            "enqueued": enqueued,
            "sent": sent,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(f"Digest run_once complete: {summary}")
        return summary

    async def enqueue_digests(self, store: SignalStore) -> int:
        """
        Enqueue digest events for all configured recipients.

        Uses idempotency key to prevent duplicate events for same day.

        Returns:
            Number of events enqueued (0 if already exists)
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        enqueued = 0

        for recipient in self.config.digest_to_emails:
            # Generate idempotency key: email_digest:{recipient}:{date}
            idempotency_key = f"{EVENT_TYPE}:{recipient}:{today}"

            # Check if already enqueued
            if await self._event_exists(store, idempotency_key):
                logger.debug(f"Digest already enqueued for {recipient} on {today}")
                continue

            # Enqueue the digest event
            payload = {
                "recipient": recipient,
                "digest_date": today,
            }

            try:
                await store.enqueue_notion_write(
                    idempotency_key=idempotency_key,
                    payload=payload,
                    event_type=EVENT_TYPE,
                )
                logger.info(f"Enqueued digest for {recipient}")
                enqueued += 1

            except Exception as e:
                # Likely duplicate key - that's fine
                if "UNIQUE constraint" in str(e):
                    logger.debug(f"Digest already exists for {recipient}")
                else:
                    logger.error(f"Failed to enqueue digest for {recipient}: {e}")

        return enqueued

    async def process_pending(self, store: SignalStore) -> int:
        """
        Process pending email_digest events from outbox.

        Claims events, builds digests, sends emails, logs actions.

        Returns:
            Number of digests successfully sent
        """
        sent = 0

        # Claim pending digest events (email_digest type only)
        events = await store.claim_due_outbox(
            event_type=EVENT_TYPE,
            limit=10,
        )

        if not events:
            logger.debug("No pending digest events")
            return 0

        logger.info(f"Processing {len(events)} digest events")

        for event in events:
            outbox_id = event["id"]
            payload = event["payload"]
            recipient = payload.get("recipient")

            if not recipient:
                logger.error(f"Digest event {outbox_id} missing recipient")
                await store.finalize_outbox(outbox_id, success=False, error="Missing recipient")
                continue

            try:
                # Build the digest
                result = await self.builder.build_weekly_digest(
                    store=store,
                    recipient=recipient,
                )

                if result.company_count == 0:
                    logger.info(f"No companies for {recipient}, skipping send")
                    await store.finalize_outbox(outbox_id, success=True)
                    continue

                # Send the email
                send_result = await self.sender.send(EmailMessage(
                    to=[recipient],
                    subject=f"Weekly Deal Digest - {result.company_count} New Companies",
                    html_body=result.html,
                    text_body=result.text,
                ))

                if send_result.success:
                    # Log digest_sent for each company (anti-spam)
                    await self._log_digest_sent(store, recipient, result.company_keys)

                    await store.finalize_outbox(outbox_id, success=True)
                    sent += 1
                    logger.info(
                        f"Sent digest to {recipient}: {result.company_count} companies, "
                        f"message_id={send_result.message_id}"
                    )
                else:
                    await store.finalize_outbox(
                        outbox_id,
                        success=False,
                        error=send_result.error,
                    )
                    logger.error(f"Failed to send digest to {recipient}: {send_result.error}")

            except Exception as e:
                logger.exception(f"Error processing digest for {recipient}: {e}")
                await store.finalize_outbox(outbox_id, success=False, error=str(e))

        return sent

    async def preview(
        self,
        store: SignalStore,
        recipient: str,
        recipient_name: Optional[str] = None,
    ) -> DigestResult:
        """
        Preview a digest for a recipient (dry run).

        Does NOT enqueue, send, or log actions.

        Returns:
            DigestResult with HTML and text content
        """
        logger.info(f"Previewing digest for {recipient}")

        result = await self.builder.build_weekly_digest(
            store=store,
            recipient=recipient,
            recipient_name=recipient_name,
        )

        return result

    async def _event_exists(self, store: SignalStore, idempotency_key: str) -> bool:
        """Check if an outbox event already exists."""
        if not store._db:
            return False

        try:
            cursor = await store._db.execute(
                "SELECT 1 FROM notion_outbox WHERE idempotency_key = ?",
                (idempotency_key,)
            )
            row = await cursor.fetchone()
            return row is not None
        except Exception:
            return False

    async def _log_digest_sent(
        self,
        store: SignalStore,
        recipient: str,
        company_keys: List[str],
    ) -> None:
        """Log digest_sent action for each company (for anti-spam tracking)."""
        for key in company_keys:
            try:
                await store.log_company_action(
                    canonical_key=key,
                    action="digest_sent",
                    actor=recipient,
                    metadata={"digest_date": datetime.now(timezone.utc).strftime("%Y-%m-%d")},
                )
            except Exception as e:
                logger.warning(f"Failed to log digest_sent for {key}: {e}")


async def run_once_command(args):
    """CLI: Run full digest flow."""
    config = load_config()
    store = SignalStore()
    await store.initialize()

    try:
        scheduler = DigestScheduler(config)
        result = await scheduler.run_once(store)

        print(f"\nDigest run complete:")
        print(f"  Enqueued: {result['enqueued']}")
        print(f"  Sent: {result['sent']}")
        print(f"  Timestamp: {result['timestamp']}")

    finally:
        await store.close()


async def enqueue_command(args):
    """CLI: Enqueue digests only."""
    config = load_config()
    store = SignalStore()
    await store.initialize()

    try:
        scheduler = DigestScheduler(config)
        enqueued = await scheduler.enqueue_digests(store)
        print(f"Enqueued {enqueued} digest events")

    finally:
        await store.close()


async def process_command(args):
    """CLI: Process pending digests only."""
    config = load_config()
    store = SignalStore()
    await store.initialize()

    try:
        scheduler = DigestScheduler(config)
        sent = await scheduler.process_pending(store)
        print(f"Sent {sent} digest emails")

    finally:
        await store.close()


async def preview_command(args):
    """CLI: Preview digest for a recipient."""
    config = load_config()
    store = SignalStore()
    await store.initialize()

    try:
        scheduler = DigestScheduler(config)
        result = await scheduler.preview(
            store=store,
            recipient=args.recipient,
            recipient_name=args.name,
        )

        print(f"\n{'='*60}")
        print(f"DIGEST PREVIEW for {args.recipient}")
        print(f"{'='*60}")
        print(f"Companies: {result.company_count}")
        print(f"Company keys: {result.company_keys}")
        print(f"HTML length: {len(result.html)} chars")
        print(f"Text length: {len(result.text)} chars")

        if args.output:
            # Write HTML to file
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(result.html)
            print(f"\nHTML written to: {args.output}")
        else:
            print(f"\n--- TEXT PREVIEW ---\n")
            print(result.text[:2000])
            if len(result.text) > 2000:
                print(f"\n... ({len(result.text)} total chars)")

    finally:
        await store.close()


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Weekly Digest Scheduler",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # run_once command
    run_parser = subparsers.add_parser(
        "run_once",
        help="Run full digest flow (enqueue + process)",
    )

    # enqueue command
    enqueue_parser = subparsers.add_parser(
        "enqueue",
        help="Enqueue digest events only (don't send)",
    )

    # process command
    process_parser = subparsers.add_parser(
        "process",
        help="Process pending digest events only",
    )

    # preview command
    preview_parser = subparsers.add_parser(
        "preview",
        help="Preview digest for a recipient (dry run)",
    )
    preview_parser.add_argument(
        "--recipient", "-r",
        required=True,
        help="Recipient email address",
    )
    preview_parser.add_argument(
        "--name", "-n",
        help="Recipient display name",
    )
    preview_parser.add_argument(
        "--output", "-o",
        help="Write HTML to file",
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.command == "run_once":
        asyncio.run(run_once_command(args))
    elif args.command == "enqueue":
        asyncio.run(enqueue_command(args))
    elif args.command == "process":
        asyncio.run(process_command(args))
    elif args.command == "preview":
        asyncio.run(preview_command(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
