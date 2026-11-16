"""Shared offline fixtures: no network, no API keys, no MongoDB."""
from __future__ import annotations

import pytest

from research_system.core.schemas import (
    CitationAudit,
    Critique,
    LeadReview,
    ReportOutline,
    ReportSection,
    ResearchPlan,
    SectionOutline,
    SourceCitation,
    Subtask,
    SubagentFinding,
)


class FakeMemory:
    """In-process stand-in for MemoryStore (same write/read/keys surface)."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._data: dict = {}

    def write(self, key, value) -> None:
        self._data[key] = value

    def read(self, key):
        return self._data.get(key)

    def keys(self) -> list[str]:
        return sorted(self._data)


class FakeAgent:
    """Mimics the part of pydantic_ai.Agent the orchestrator uses: `await run()`."""

    def __init__(self, handler, calls: list, name: str) -> None:
        self._handler = handler
        self._calls = calls
        self._name = name

    async def run(self, prompt, **kwargs):
        self._calls.append(self._name)
        out = self._handler(prompt, **kwargs)
        if hasattr(out, "__await__"):
            out = await out
        return type("FakeResult", (), {"output": out})()


def make_source(n: int) -> SourceCitation:
    return SourceCitation(id=f"s{n}", title=f"Source {n}", url=f"https://example.com/{n}")


def make_plan(n_subtasks: int = 2) -> ResearchPlan:
    return ResearchPlan(
        original_query="q",
        interpretation="i",
        approach="a",
        subtasks=[
            Subtask(id=f"st{i}", objective=f"obj {i}", output_format="list")
            for i in range(1, n_subtasks + 1)
        ],
    )


def make_outline(n_sections: int = 3) -> ReportOutline:
    return ReportOutline(
        title="T",
        executive_summary="E",
        sections=[
            SectionOutline(heading=f"H{i}", focus_areas=["f"], relevant_subtask_ids=["st1"])
            for i in range(1, n_sections + 1)
        ],
    )


def make_finding(subtask_id: str, n_sources: int = 1, start: int = 1) -> SubagentFinding:
    return SubagentFinding(
        subtask_id=subtask_id,
        objective="o",
        key_findings=["fact"],
        sources=[make_source(start + i) for i in range(n_sources)],
    )


@pytest.fixture
def calls() -> list[str]:
    """Ordered log of which agent ran, shared by every fake agent in a test."""
    return []


@pytest.fixture
def orch_env(monkeypatch, calls):
    """Patch every agent factory, the search tool, memory and vector indexing.

    Returns a namespace of mutable handlers so a test can override one stage:
    `env.handlers["reviewer"] = lambda prompt, **kw: LeadReview(...)`.
    """
    from research_system import orchestrator as orch

    class Env:
        pass

    env = Env()
    env.orch = orch
    env.handlers = {
        "planner": lambda p, **kw: make_plan(2),
        "search": lambda p, **kw: make_finding("placeholder"),
        "reviewer": lambda p, **kw: LeadReview(summary="ok", is_sufficient=True),
        "summarizer": lambda p, **kw: "brief",
        "outliner": lambda p, **kw: make_outline(3),
        "section_writer": lambda p, **kw: ReportSection(heading="h", body_markdown="body [1]"),
        "citation": lambda p, **kw: CitationAudit(fixed_sections=[], ordered_sources=[]),
        "critique": lambda p, **kw: Critique(weaknesses=["w"], overall_score=7),
    }

    def factory(name):
        return lambda *a, **k: FakeAgent(lambda p, **kw: env.handlers[name](p, **kw), calls, name)

    monkeypatch.setattr(orch, "build_planner_agent", factory("planner"))
    monkeypatch.setattr(orch, "build_search_agent", factory("search"))
    monkeypatch.setattr(orch, "build_reviewer_agent", factory("reviewer"))
    monkeypatch.setattr(orch, "build_summarizer_agent", factory("summarizer"))
    monkeypatch.setattr(orch, "build_outliner_agent", factory("outliner"))
    monkeypatch.setattr(orch, "build_section_writer_agent", factory("section_writer"))
    monkeypatch.setattr(orch, "build_citation_agent", factory("citation"))
    monkeypatch.setattr(orch, "build_critique_agent", factory("critique"))
    monkeypatch.setattr(orch, "TavilySearchTool", lambda: object())
    monkeypatch.setattr(orch, "MemoryStore", FakeMemory)
    monkeypatch.setattr(orch.ResearchOrchestrator, "_index_research_memory", lambda *a, **k: None)
    return env
