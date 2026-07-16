"""Per-run memory store — MongoDB-backed key/value scoped by session_id.

Motivation (from Anthropic's multi-agent research article):
> "The LeadResearcher begins by thinking through the approach and saving
>  its plan to Memory to persist the context, since if the context window
>  exceeds 200,000 tokens it will be truncated…"

Kept intentionally tiny: 3 methods (write / read / keys). No expiry, no
namespaces beyond `session_id`. The orchestrator pre-populates memory
with the plan + original query; subagents can pull them back via a
`memory_read` tool when useful.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from pymongo import MongoClient


_client: MongoClient | None = None


def _coll():
    global _client
    if _client is None:
        _client = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db = _client[os.environ.get("DB_NAME", "research_agents")]
    return db["agent_memory"]


class MemoryStore:
    """Per-session key/value memory. All ops are cheap Mongo upserts."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id

    def write(self, key: str, value: Any) -> None:
        _coll().update_one(
            {"session_id": self.session_id, "key": key},
            {
                "$set": {
                    "value": value,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            },
            upsert=True,
        )

    def read(self, key: str) -> Any:
        doc = _coll().find_one({"session_id": self.session_id, "key": key})
        return doc["value"] if doc else None

    def keys(self) -> list[str]:
        return sorted(
            d["key"] for d in _coll().find(
                {"session_id": self.session_id}, projection={"key": 1}
            )
        )
