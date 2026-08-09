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
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List

from pymongo import MongoClient
from pydantic import BaseModel, Field


_client: MongoClient | None = None


def _coll():
    global _client
    if _client is None:
        _client = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db = _client[os.environ.get("DB_NAME", "research_agents")]
    return db["sessions"]


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
            ) if "key" in d
        )


class ResearchSession(BaseModel):
    """
    Stores messages and metadata for a research session.
    """
    session_id: str
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def append_message(self, role: str, content: str) -> None:
        """
        Add a new message to the session.

        Args:
            role: Message sender role (e.g., "user", "assistant", "system")
            content: Message content
        """
        self.messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        self.updated_at = datetime.now(timezone.utc).isoformat()


class SessionManager:
    """
    Manages research sessions with MongoDB fallback to in-memory storage.

    Uses MongoDB when MONGO_URL environment variable is set, otherwise
    falls back to an in-memory dictionary store (thread-safe).
    """

    def __init__(self) -> None:
        self._use_mongodb = os.environ.get("MONGO_URL") is not None
        if self._use_mongodb:
            self._client = MongoClient(
                os.environ.get("MONGO_URL", "mongodb://localhost:27017")
            )
            self._db = self._client[
                os.environ.get("DB_NAME", "research_agents")
            ]
            self._collection = self._db["chat_sessions"]
        else:
            self._sessions: Dict[str, ResearchSession] = {}
            self._lock = threading.Lock()

    def _get_collection(self):
        """Get MongoDB collection, ensuring it's initialized."""
        return self._collection

    def get_or_create_session(self, session_id: str) -> ResearchSession:
        """
        Get existing session or create a new one.

        Args:
            session_id: Unique identifier for the session

        Returns:
            ResearchSession instance
        """
        if self._use_mongodb:
            # Attempt to retrieve existing session from MongoDB
            doc = self._collection.find_one({"session_id": session_id})
            if doc:
                return ResearchSession(**doc)
            # If not found, create a new session
            session = ResearchSession(session_id=session_id)
            self._collection.insert_one(session.model_dump())
            return session
        else:
            # In-memory fallback
            with self._lock:
                if session_id not in self._sessions:
                    self._sessions[session_id] = ResearchSession(
                        session_id=session_id
                    )
                return self._sessions[session_id]

    def save_session(self, session: ResearchSession) -> None:
        """
        Persist session to storage.

        Args:
            session: ResearchSession to save
        """
        if self._use_mongodb:
            self._collection.update_one(
                {"session_id": session.session_id},
                {"$set": session.model_dump()},
                upsert=True
            )
        else:
            with self._lock:
                self._sessions[session.session_id] = session

    def append_message(
        self, session_id: str, role: str, content: str
    ) -> None:
        """
        Append a message to an existing session.

        Args:
            session_id: Session identifier
            role: Message role (e.g., "user", "assistant")
            content: Message content
        """
        session = self.get_or_create_session(session_id)
        session.append_message(role, content)
        self.save_session(session)

    def list_sessions(self, limit: int = 30) -> list[ResearchSession]:
        """List recent sessions, ordered by updated_at descending."""
        if self._use_mongodb:
            docs = self._collection.find().sort("updated_at", -1).limit(limit)
            return [ResearchSession(**doc) for doc in docs]
        else:
            with self._lock:
                sessions = list(self._sessions.values())
                sessions.sort(key=lambda s: s.updated_at, reverse=True)
                return sessions[:limit]
