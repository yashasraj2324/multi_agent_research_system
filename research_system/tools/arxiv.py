"""arXiv search tool — fetches recent academic papers by keyword."""
from __future__ import annotations

import asyncio
from typing import Any
import arxiv

async def arxiv_search(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Search arXiv for papers matching `query`. Returns [{title, authors, url, abstract, published}]."""

    def _run() -> list[dict[str, Any]]:
        search = arxiv.Search(
            query=query,
            max_results=max_results,
            sort_by=arxiv.SortCriterion.Relevance,
        )
        out: list[dict[str, Any]] = []
        for r in arxiv.Client().results(search):
            out.append({
                "title": r.title,
                "authors": ", ".join(a.name for a in r.authors[:5]),
                "url": r.entry_id,
                "pdf_url": r.pdf_url,
                "abstract": (r.summary or "").strip()[:1200],
                "published": r.published.isoformat() if r.published else "",
                "categories": r.categories,
            })
        return out

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:  # noqa: BLE001
        return [{"title": "arxiv_error", "url": "", "abstract": str(exc), "authors": "", "published": "", "categories": []}]
