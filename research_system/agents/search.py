"""Search (subagent) — investigates one subtask using the Tavily tool.

Also gets a `memory_read` tool so it can fetch the shared plan / original
query from the run's memory store when useful.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from pydantic_ai import Agent, RunContext

from ..core.llm import get_model
from ..db.memory import MemoryStore
from ..db.scratchpad import ResearchScratchpad
from ..core.schemas import SubagentFinding
from ..tools.arxiv import arxiv_search
from ..tools.search import TavilySearchTool


@dataclass
class SearchDeps:
    tool: TavilySearchTool
    memory: MemoryStore


SCRATCHPAD_STORE: dict[str, ResearchScratchpad] = {}


def _get_scratchpad_state(ctx: RunContext[Any]) -> tuple[ResearchScratchpad, Any | None]:
    deps = getattr(ctx, "deps", None)
    memory = getattr(deps, "memory", None) if deps is not None else None

    if memory is not None and hasattr(memory, "read") and hasattr(memory, "write"):
        return ResearchScratchpad.from_memory(memory), memory

    session_key = getattr(getattr(deps, "memory", None), "session_id", None)
    if session_key is None:
        session_key = "default"

    if str(session_key) not in SCRATCHPAD_STORE:
        SCRATCHPAD_STORE[str(session_key)] = ResearchScratchpad()
    return SCRATCHPAD_STORE[str(session_key)], None


def register_shared_agent_tools(agent: Agent[Any, Any]) -> None:
    @agent.tool
    def scratchpad_read(ctx: RunContext[Any], sections: list[str]) -> str:
        """Read one or more scratchpad sections and return the contents."""
        state, _ = _get_scratchpad_state(ctx)
        if not sections:
            return state.to_prompt_context()

        lines: list[str] = []
        for section in sections:
            value = state.read_section(section)
            if isinstance(value, (dict, list)):
                lines.append(f"{section}: {json.dumps(value, default=str)}")
            else:
                lines.append(f"{section}: {value}")
        return "\n".join(lines)

    @agent.tool
    def scratchpad_write(ctx: RunContext[Any], section_name: str, content: str) -> str:
        """Write content into a named scratchpad section."""
        state, memory = _get_scratchpad_state(ctx)
        state.write_section(section_name, content)
        if memory is not None:
            state.write_to_memory(memory)
        return f"Wrote content to scratchpad section '{section_name}'."

    @agent.tool
    def semantic_search_memory(ctx: RunContext[Any], query: str) -> str:
        """Search the session's semantic memory for relevant content."""
        deps = getattr(ctx, "deps", None)
        memory = getattr(deps, "memory", None) if deps is not None else None
        session_id = getattr(memory, "session_id", None) if memory is not None else None

        try:
            from ..db.vector import VectorStore

            vector_store = VectorStore()
            results = vector_store.similarity_search(query, k=3, session_id=session_id)
        except Exception as exc:  # noqa: BLE001
            return f"Semantic memory search unavailable: {exc}"

        if not results:
            return ""
        return "\n".join(f"- {item.get('text', '')}" for item in results)


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
   For current facts, search for publication/update dates and prefer recent
   primary or reputable news sources.
4. If you need the ORIGINAL user question or the LEAD's plan for context,
   call `memory_read("original_query")` or `memory_read("plan")`.
5. Return a SubagentFinding with:
   - key_findings: 3-8 self-contained bullet points; each ends with a
     source reference like "(source: <url>)".
   - sources: every URL you actually used, with title, snippet when
     available, publication date when known, and a source_type.
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
   Identify primary evidence, conflicting evidence, dates, and stale-source
   risk. Do not treat search-result snippets as definitive when the claim
   needs a primary source.
3. If you need the ORIGINAL user question or the LEAD's plan for context,
   call `memory_read("original_query")` or `memory_read("plan")`. You can
   also list what's available with `memory_keys()`.
4. Return a SubagentFinding with:
   - key_findings: 5-12 self-contained bullet points; each ends with a
     source reference like "(source: <url>)".
   - sources: every URL you actually used, with title, snippet when
     available, publication date when known, and a source_type.
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

    register_shared_agent_tools(agent)

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

    agent._function_tools = agent._function_toolset.tools
    return agent
