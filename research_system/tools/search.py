"""Tavily-backed web search tool used by subagents."""
from __future__ import annotations

import asyncio
from typing import Any

from tavily import TavilyClient

from ..core.config import config


class TavilySearchTool:
    """Thin async wrapper around Tavily search (tolerant of missing key)."""

    def __init__(self) -> None:
        self._client: TavilyClient | None = None
        if config.is_tavily_configured():
            self._client = TavilyClient(api_key=config.TAVILY_API_KEY)

    async def search(
        self, query: str, max_results: int | None = None
    ) -> list[dict[str, Any]]:
        """Execute a search, returning a list of {title, url, content}.

        Returns [] gracefully if TAVILY_API_KEY is not configured so the
        agent can adapt (mention gaps) instead of the pipeline crashing.
        """
        if self._client is None:
            return [{
                "title": "tavily_not_configured",
                "url": "",
                "content": (
                    "TAVILY_API_KEY is not set — web search is disabled. "
                    "Report this in the `gaps` field of your finding."
                ),
            }]

        max_results = max_results or config.MAX_SEARCH_RESULTS_PER_AGENT

        def _do() -> list[dict[str, Any]]:
            resp = self._client.search(  # type: ignore[union-attr]
                query=query,
                max_results=max_results,
                search_depth="advanced",
                include_answer=False,
            )
            return resp.get("results", []) or []

        try:
            return await asyncio.to_thread(_do)
        except Exception as exc:  # noqa: BLE001
            return [{"title": "search_error", "url": "", "content": str(exc)}]
