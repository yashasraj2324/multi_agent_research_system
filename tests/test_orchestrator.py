"""Orchestrator control flow with every LLM / search call mocked."""
import asyncio

from research_system.core.config import config
from research_system.core.schemas import (
    Critique,
    GapFollowup,
    LeadReview,
    ReportSection,
    SubagentFinding,
)
from research_system.orchestrator import ResearchOrchestrator

from .conftest import make_plan, make_source


def _search_by_subtask(prompt, **kw):
    """Fake search agent: read the subtask id from the prompt, return one unique source."""
    sid = prompt.split("Subtask id: ")[1].split("\n")[0]
    n = int("".join(ch for ch in sid if ch.isdigit()) or 0)
    return SubagentFinding(
        subtask_id="ignored", objective="o", key_findings=["fact"], sources=[make_source(n)]
    )


def _gap_review(n=1):
    return LeadReview(
        summary="gaps",
        is_sufficient=False,
        followups=[
            GapFollowup(id=f"gap{i}", parent_subtask_id="st1", objective="fill", suggested_queries=["q"])
            for i in range(1, n + 1)
        ],
    )


def run(orch, query="q"):
    return asyncio.run(orch.run(query))


def test_standard_pipeline_order_and_no_deep_stages(orch_env, calls):
    orch_env.handlers["search"] = _search_by_subtask
    plan, findings, report, critique = run(ResearchOrchestrator(session_id="t"))

    # planner -> search x2 -> summarizer -> outliner -> section writers x3 -> critique
    assert calls[0] == "planner"
    assert calls.count("search") == 2
    assert calls.index("summarizer") > max(i for i, c in enumerate(calls) if c == "search")
    assert calls.index("outliner") > calls.index("summarizer")
    assert calls.count("section_writer") == 3
    assert calls.index("critique") > max(i for i, c in enumerate(calls) if c == "section_writer")
    assert "reviewer" not in calls and "citation" not in calls

    assert [f.subtask_id for f in findings] == ["st1", "st2"]
    assert report.title == "T" and len(report.sections) == 3
    assert critique.overall_score == 7


def test_run_persists_state_to_memory(orch_env):
    orch_env.handlers["search"] = _search_by_subtask
    o = ResearchOrchestrator(session_id="t")
    run(o, "my query")
    for key in ("original_query", "plan", "all_sources", "synthesis_brief", "report", "critique", "findings"):
        assert o.memory.read(key) is not None, key
    assert o.memory.read("mode") == "standard"
    assert o.memory.read("original_query") == "my query"


def test_section_writers_run_in_parallel(orch_env):
    """Each writer waits until all 3 have started; a sequential loop would fail the assert."""
    orch_env.handlers["search"] = _search_by_subtask
    started = 0

    async def writer(prompt, **kw):
        nonlocal started
        started += 1
        for _ in range(100):
            if started == 3:
                break
            await asyncio.sleep(0.01)
        assert started == 3, "section writers did not overlap"
        return ReportSection(heading="h", body_markdown="b")

    orch_env.handlers["section_writer"] = writer
    run(ResearchOrchestrator(session_id="t"))


def test_section_order_matches_outline_order(orch_env):
    orch_env.handlers["search"] = _search_by_subtask

    async def writer(prompt, **kw):
        heading = prompt.split("Heading: ")[1].split("\n")[0]
        # later sections finish first: order must come from the outline, not completion time
        await asyncio.sleep({"H1": 0.06, "H2": 0.03, "H3": 0}[heading])
        return ReportSection(heading=heading, body_markdown="b")

    orch_env.handlers["section_writer"] = writer
    _, _, report, _ = run(ResearchOrchestrator(session_id="t"))
    assert [s.heading for s in report.sections] == ["H1", "H2", "H3"]


def test_planner_subtasks_are_capped_in_standard_mode(orch_env):
    orch_env.handlers["planner"] = lambda p, **kw: make_plan(10)
    orch_env.handlers["search"] = _search_by_subtask
    plan, findings, _, _ = run(ResearchOrchestrator(session_id="t"))
    assert len(plan.subtasks) == config.MAX_SUBAGENTS == len(findings)


def test_failed_subagent_becomes_low_confidence_finding_and_run_continues(orch_env):
    def search(prompt, **kw):
        if "Subtask id: st1" in prompt:
            raise RuntimeError("tavily exploded")
        return _search_by_subtask(prompt)

    orch_env.handlers["search"] = search
    _, findings, report, _ = run(ResearchOrchestrator(session_id="t"))
    bad = findings[0]
    assert bad.subtask_id == "st1" and bad.confidence == "low" and "tavily exploded" in bad.gaps
    assert report.title == "T"


def test_critique_failure_is_non_fatal(orch_env):
    orch_env.handlers["search"] = _search_by_subtask

    def boom(prompt, **kw):
        raise RuntimeError("critique down")

    orch_env.handlers["critique"] = boom
    _, _, report, critique = run(ResearchOrchestrator(session_id="t"))
    assert critique is None and report.quality_notes == []


def test_critique_feeds_report_quality_notes(orch_env):
    orch_env.handlers["search"] = _search_by_subtask
    orch_env.handlers["critique"] = lambda p, **kw: Critique(
        weaknesses=["w1", "w2", "w3", "w4"], missing_angles=["a1", "a2", "a3"], overall_score=4
    )
    _, _, report, critique = run(ResearchOrchestrator(session_id="t"))
    assert report.quality_notes == ["w1", "w2", "w3", "Missing angle: a1", "Missing angle: a2"]
    assert critique.overall_score == 4


# ---------------- deep mode: reviewer gap-fill loop ----------------

def test_deep_loop_stops_immediately_when_reviewer_says_sufficient(orch_env, calls):
    orch_env.handlers["search"] = _search_by_subtask
    run(ResearchOrchestrator(session_id="t", deep=True, max_followup_rounds=3))
    assert calls.count("reviewer") == 1
    assert calls.count("search") == 2  # no gap-fillers
    assert "citation" in calls  # deep mode adds the citation audit


def test_deep_loop_stops_at_max_rounds_when_reviewer_never_satisfied(orch_env, calls):
    orch_env.handlers["search"] = _search_by_subtask
    orch_env.handlers["reviewer"] = lambda p, **kw: _gap_review(1)
    o = ResearchOrchestrator(session_id="t", deep=True, max_followup_rounds=2)
    plan, findings, _, _ = run(o)
    assert calls.count("reviewer") == 2
    assert calls.count("search") == 2 + 2  # first pass + one gap-filler per round
    assert [f.subtask_id for f in findings][-2:] == ["gap1", "gap1"]
    assert [s.id for s in plan.subtasks].count("gap1") == 2


def test_deep_loop_stops_when_insufficient_but_no_followups(orch_env, calls):
    orch_env.handlers["search"] = _search_by_subtask
    orch_env.handlers["reviewer"] = lambda p, **kw: LeadReview(summary="?", is_sufficient=False, followups=[])
    run(ResearchOrchestrator(session_id="t", deep=True, max_followup_rounds=5))
    assert calls.count("reviewer") == 1


def test_deep_followups_are_capped_at_four(orch_env, calls):
    orch_env.handlers["search"] = _search_by_subtask
    reviews = iter([_gap_review(7), LeadReview(summary="ok", is_sufficient=True)])
    orch_env.handlers["reviewer"] = lambda p, **kw: next(reviews)
    run(ResearchOrchestrator(session_id="t", deep=True, max_followup_rounds=3))
    assert calls.count("reviewer") == 2
    assert calls.count("search") == 2 + 4


def test_gap_filler_findings_reach_summary_and_are_deduped(orch_env):
    orch_env.handlers["search"] = _search_by_subtask
    reviews = iter([_gap_review(1), LeadReview(summary="ok", is_sufficient=True)])
    orch_env.handlers["reviewer"] = lambda p, **kw: next(reviews)
    briefs = []
    orch_env.handlers["summarizer"] = lambda p, **kw: briefs.append(p) or "brief"
    o = ResearchOrchestrator(session_id="t", deep=True, max_followup_rounds=3)
    run(o)
    assert "gap1" in briefs[0]
    # the fake gap-filler (id gap1) returns the same URL as st1, so dedupe leaves st1 + st2
    assert len(o.memory.read("all_sources")) == 2
