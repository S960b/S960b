"""Crash-safe outbox consumer kernel with a pluggable durable local inbox.

Integration with MultiTool's actual API is deliberately NOT guessed here.
Implement a `DurableInbox.put_once(delivery)` adapter only after inspecting the
current MT plugin. put_once must persist the receipt and the instruction
atomically (or use the same idempotent task_id+seq key), BEFORE returning.

Do NOT execute a task inside put_once: acknowledge receipt, then execute it
from the local inbox so a long task cannot exceed the PostgreSQL lease.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol
from .store import Store, BridgeError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Delivery:
    delivery_id: int
    task_id: str
    seq: int
    message_id: str
    sender: str
    kind: str
    body: str
    attempts: int

    @property
    def dedup_key(self) -> str:
        return f'{self.task_id}:{self.seq}'


class DurableInbox(Protocol):
    def put_once(self, delivery: Delivery) -> None:
        """Synchronous; durable commit; safe for an arbitrary number of repeats.

        Raise an exception if durable persistence did not succeed. Must NOT
        execute the instruction or block until the agent's job is complete.
        """
        ...


class Consumer:
    def __init__(self, store: Store, inbox: DurableInbox, recipient: str,
                 consumer_id: str, batch_size: int = 10, lease_seconds: int = 60):
        self.store = store
        self.inbox = inbox
        self.recipient = recipient
        self.consumer_id = consumer_id
        self.batch_size = batch_size
        self.lease_seconds = lease_seconds

    def poll_once(self) -> tuple[int, int]:
        """Return (ack_count, failed_count); retry failed deliveries later."""
        rows = self.store.claim_outbox(self.recipient, self.consumer_id,
                                       self.batch_size, self.lease_seconds)
        acked = failed = 0
        for row in rows:
            token = str(row['lease_token'])
            did = row['delivery_id']
            delivery = Delivery(
                delivery_id=did, task_id=row['task_id'], seq=row['seq'],
                message_id=row['message_id'], sender=row['sender'],
                kind=row['kind'], body=row['body'], attempts=row['attempts'],
            )
            try:
                self.inbox.put_once(delivery)
            except Exception as exc:
                failed += 1
                # Don't log the body or exception text: it may contain secrets.
                logger.warning('durable inbox failed for delivery_id=%d task_id=%s', did, delivery.task_id)
                backoff = min(3600, 2 ** min(10, delivery.attempts))
                self.store.release_outbox(did, token, self.consumer_id,
                                          retry_seconds=backoff, error=type(exc).__name__)
            else:
                if self.store.ack_outbox(did, token, self.consumer_id):
                    acked += 1
                else:
                    # Lease may have been replaced. put_once() MUST be idempotent.
                    failed += 1
                    logger.warning('ack race for delivery_id=%d task_id=%s', did, delivery.task_id)
        return acked, failed
