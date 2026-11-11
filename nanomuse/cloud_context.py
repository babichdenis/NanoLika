"""A verified cloud client belongs to one bot turn, never the shared process."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from nanomuse.cloud import CloudClient

_turn_client: ContextVar[CloudClient | None] = ContextVar("nanomuse_turn_cloud", default=None)


def current_cloud_client() -> CloudClient | None:
    return _turn_client.get()


@asynccontextmanager
async def turn_cloud_client(base_url: str, key: str) -> AsyncIterator[CloudClient | None]:
    """Create one client for a verified turn and close it, even on cancellation."""
    client = CloudClient(base_url=base_url, api_key=key) if base_url and key else None
    token = _turn_client.set(client)
    try:
        yield client
    finally:
        _turn_client.reset(token)
        if client is not None:
            await client.close()
