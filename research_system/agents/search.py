"""Search (subagent) — investigates one subtask using the Tavily tool.

Also gets a `memory_read` tool so it can fetch the shared plan / original
query from the run's memory store when useful.
"""
from __future__ import annotations

from dataclasses import dataclass
import json

from pydantic_ai import Agent, RunContext

from ..core.llm import get_model
from ..db.memory import MemoryStore
from ..core.schemas import SubagentFinding
from ..tools.arxiv import arxiv_search
from ..tools.search import TavilySearchTool


@dataclass
class SearchDeps:
    tool: TavilySearchTool
    memory: MemoryStore


STANDARD_SEARCH_PROMPT = """You are a SEARCH SUBAGENT in a multi-agent research team.

You have ONE subtask. Your goal:
1. Use tools to gather evidence:
   - `web_search(query)`  — general-purpose Tavily web search (use 2-4 times).
   - `arxiv_papers(query)` — academic papers on arXiv (use for scientific
     / technical / ML topics; usually 1-2 calls is enough).
   Start broad, then narrow based on what you find.
2. Extract concrete, source-backed facts. Never invent information.
3. Prefer authoritative sources (official docs, primary sources, peer-
   reviewed papers) over SEO-optimised content farms.
4. If you need the ORIGINAL user question or the LEAD's plan for context,
   call `memory_read("original_query")` or `memory_read("plan")`.
5. Return a SubagentFinding with:
   - key_findings: 3-8 self-contained bullet points; each ends with a
     source reference like "(source: <url>)".
   - sources: every URL you actually used, with title + short snippet.
   - confidence: how confident you are in the findings.
   - gaps: information you tried to find but couldn't.
6. Stop searching once you have enough. Do not loop forever."""


DEEP_SEARCH_PROMPT = """You are a SEARCH SUBAGENT in a multi-agent research team,
operating in **DEEP RESEARCH mode**.

Deep mode means: you have more tool budget and are expected to be thorough.

Your goal:
1. Use tools 4-8 times total. Start WIDE, then NARROW.
   - `web_search(query)`  — general Tavily search.
   - `arxiv_papers(query)` — academic papers; use liberally when the
     topic is scientific / technical / research-heavy.
2. Cross-reference: prefer facts confirmed by at least two independent
   authoritative sources. Peer-reviewed papers > blog posts > content farms.
3. If you need the ORIGINAL user question or the LEAD's plan for context,
   call `memory_read("original_query")` or `memory_read("plan")`. You can
   also list what's available with `memory_keys()`.
4. Return a SubagentFinding with:
   - key_findings: 5-12 self-contained bullet points; each ends with a
     source reference like "(source: <url>)".
   - sources: every URL you actually used, with title + short snippet.
   - confidence: honest self-assessment.
   - gaps: information you tried to find but couldn't — be specific.
5. Never invent information or URLs.
6. Stop once you have enough."""


def build_search_agent(deep: bool = False) -> Agent[SearchDeps, SubagentFinding]:
    agent: Agent[SearchDeps, SubagentFinding] = Agent(
        get_model(),
        deps_type=SearchDeps,
        output_type=SubagentFinding,
        system_prompt=DEEP_SEARCH_PROMPT if deep else STANDARD_SEARCH_PROMPT,
        retries=2,
    )

    @agent.tool
    async def web_search(ctx: RunContext[SearchDeps], query: str) -> list[dict]:
        """Search the web for the given query. Returns list of {title, url, content}."""
        results = await ctx.deps.tool.search(query)
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": (r.get("content") or "")[:1200],
            }
            for r in results
        ]

    @agent.tool
    async def arxiv_papers(
        ctx: RunContext[SearchDeps], query: str, max_results: int = 5
    ) -> list[dict]:
        """Search arXiv for academic papers. Use this for scientific / technical claims."""
        return await arxiv_search(query, max_results=max_results)

    @agent.tool
    def memory_read(ctx: RunContext[SearchDeps], key: str) -> str:
        """Read a value from the shared research memory. Returns '' if the key is missing."""
        val = ctx.deps.memory.read(key)
        if val is None:
            return ""
        return val if isinstance(val, str) else json.dumps(val, default=str)[:4000]

    @agent.tool
    def memory_keys(ctx: RunContext[SearchDeps]) -> list[str]:
        """List all keys currently in the shared research memory."""
        return ctx.deps.memory.keys()

    return agent
