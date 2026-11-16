"""Citation handling: URL normalisation, source de-duplication and the audit pass."""
import asyncio

import pytest

from research_system.core.schemas import (
    CitationAudit,
    ReportSection,
    ResearchReport,
    SourceCitation,
    SubagentFinding,
)
from research_system.orchestrator import ResearchOrchestrator

from .conftest import make_finding, make_source


@pytest.fixture
def orch(orch_env):
    return ResearchOrchestrator(session_id="t")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("", ""),
        ("HTTPS://Example.COM/Path/", "https://example.com/Path"),
        ("https://example.com/a#section", "https://example.com/a"),
        ("  https://example.com/a  ", "https://example.com/a"),
        ("https://example.com/a?x=1", "https://example.com/a?x=1"),
        ("https://example.com/", "https://example.com/"),
    ],
)
def test_normalize_source_url(orch, raw, expected):
    assert orch._normalize_source_url(raw) == expected


def test_dedupe_sources_merges_url_variants_and_keeps_first(orch):
    a = SourceCitation(id="a", title="first", url="https://Example.com/x/")
    b = SourceCitation(id="b", title="dupe", url="https://example.com/x#frag")
    c = SourceCitation(id="c", title="other", url="https://example.com/y")
    f1 = SubagentFinding(subtask_id="st1", objective="o", key_findings=[], sources=[a, c])
    f2 = SubagentFinding(subtask_id="st2", objective="o", key_findings=[], sources=[b])
    out = orch._dedupe_sources([f1, f2])
    assert [s.id for s in out] == ["a", "c"]


def test_dedupe_sources_drops_sources_without_url(orch):
    blank = SourceCitation(id="x", title="no url", url="")
    f = SubagentFinding(subtask_id="st1", objective="o", key_findings=[], sources=[blank, make_source(1)])
    assert [s.id for s in orch._dedupe_sources([f])] == ["s1"]


def _report(n_sources=3) -> ResearchReport:
    return ResearchReport(
        title="T",
        executive_summary="E",
        sections=[ReportSection(heading="H", body_markdown="claim [3] and claim [1]")],
        all_sources=[make_source(i) for i in range(1, n_sources + 1)],
    )


def test_audit_applies_renumbered_sections_and_source_order(orch_env, orch):
    audit = CitationAudit(
        fixed_sections=[ReportSection(heading="H", body_markdown="claim [1] and claim [2]")],
        ordered_sources=[make_source(3), make_source(1)],
        notes="renumbered",
    )
    orch_env.handlers["citation"] = lambda p, **kw: audit
    report = asyncio.run(orch._audit_citations(_report()))
    assert report.sections[0].body_markdown == "claim [1] and claim [2]"
    assert [s.id for s in report.all_sources] == ["s3", "s1"]


def test_audit_prompt_lists_numbered_sources(orch_env, orch):
    seen = {}

    def handler(prompt, **kw):
        seen["prompt"] = prompt
        return CitationAudit(fixed_sections=[], ordered_sources=[])

    orch_env.handlers["citation"] = handler
    asyncio.run(orch._audit_citations(_report(2)))
    assert "[1] Source 1 - https://example.com/1" in seen["prompt"].replace("—", "-")
    assert "[2] Source 2 - https://example.com/2" in seen["prompt"].replace("—", "-")


def test_audit_keeps_original_when_agent_returns_nothing(orch_env, orch):
    orch_env.handlers["citation"] = lambda p, **kw: CitationAudit(fixed_sections=[], ordered_sources=[])
    report = asyncio.run(orch._audit_citations(_report()))
    assert report.sections[0].body_markdown == "claim [3] and claim [1]"
    assert [s.id for s in report.all_sources] == ["s1", "s2", "s3"]


def test_audit_failure_returns_report_unchanged_and_warns(orch_env):
    events = []
    o = ResearchOrchestrator(on_trace=events.append, session_id="t")

    def boom(prompt, **kw):
        raise RuntimeError("llm down")

    orch_env.handlers["citation"] = boom
    report = asyncio.run(o._audit_citations(_report()))
    assert report.sections[0].body_markdown == "claim [3] and claim [1]"
    assert any(e.agent == "CitationAgent" and e.level == "warn" for e in events)


@pytest.mark.parametrize(
    "message, expected",
    [
        ("Please generate a report from this", True),
        ("Give me the FINAL REPORT", True),
        ("What about battery life?", False),
    ],
)
def test_consolidation_request_detection(orch, message, expected):
    assert orch._looks_like_consolidation_request(message) is expected


def test_load_findings_from_memory_skips_invalid_and_dedupes(orch):
    good = make_finding("st1").model_dump(mode="json")
    orch.memory.write("findings", [good, {"not": "a finding"}])
    orch.memory.write("finding.st1", good)  # same id as the list entry
    orch.memory.write("finding.st2", make_finding("st2").model_dump(mode="json"))
    orch.memory.write("finding.bad", {"subtask_id": 5})
    assert sorted(f.subtask_id for f in orch._load_findings_from_memory()) == ["st1", "st2"]
