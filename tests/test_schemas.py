"""Schema validation: constraints, enums, defaults and JSON round-trips."""
import pytest
from pydantic import ValidationError

from research_system.core.schemas import (
    CitationAudit,
    Critique,
    IntentCategory,
    LeadReview,
    ReportSection,
    ResearchPlan,
    ResearchReport,
    SourceCitation,
    Subtask,
    SubagentFinding,
    TraceEvent,
)


@pytest.mark.parametrize("score", [0, 5, 10])
def test_critique_accepts_score_in_range(score):
    assert Critique(weaknesses=["w"], overall_score=score).overall_score == score


@pytest.mark.parametrize("score", [-1, 11])
def test_critique_rejects_score_out_of_range(score):
    with pytest.raises(ValidationError):
        Critique(weaknesses=["w"], overall_score=score)


def test_critique_requires_score_and_weaknesses():
    with pytest.raises(ValidationError):
        Critique(weaknesses=["w"])
    with pytest.raises(ValidationError):
        Critique(overall_score=5)


def test_subtask_scope_is_a_closed_enum_with_default():
    assert Subtask(id="st1", objective="o", output_format="f").scope == "broad"
    with pytest.raises(ValidationError):
        Subtask(id="st1", objective="o", output_format="f", scope="huge")


def test_plan_requires_subtasks_and_validates_complexity():
    with pytest.raises(ValidationError):
        ResearchPlan(original_query="q", interpretation="i", approach="a")
    with pytest.raises(ValidationError):
        ResearchPlan(
            original_query="q", interpretation="i", approach="a",
            complexity="extreme", subtasks=[],
        )


def test_source_citation_defaults_and_type_enum():
    s = SourceCitation(id="1", title="t", url="https://x.test")
    assert s.snippet == "" and s.source_type == "unknown"
    with pytest.raises(ValidationError):
        SourceCitation(id="1", title="t", url="u", source_type="blog")


def test_finding_confidence_enum():
    with pytest.raises(ValidationError):
        SubagentFinding(subtask_id="st1", objective="o", key_findings=[], confidence="certain")


def test_lead_review_defaults_to_no_followups():
    assert LeadReview(summary="s", is_sufficient=True).followups == []


def test_intent_category_rejects_unknown_route():
    assert IntentCategory(category="FULL_RUN", reasoning="r").response == ""
    with pytest.raises(ValidationError):
        IntentCategory(category="SMALL_TALK", reasoning="r")


def test_trace_event_timestamp_is_timezone_aware_and_level_checked():
    ev = TraceEvent(agent="a", stage="s", message="m")
    assert ev.ts.tzinfo is not None
    with pytest.raises(ValidationError):
        TraceEvent(agent="a", stage="s", message="m", level="fatal")


def test_report_round_trips_through_json():
    report = ResearchReport(
        title="T",
        executive_summary="E",
        sections=[ReportSection(heading="H", body_markdown="x [1]")],
        all_sources=[SourceCitation(id="1", title="t", url="https://x.test")],
    )
    again = ResearchReport.model_validate(report.model_dump(mode="json"))
    assert again == report
    assert again.quality_notes == []


def test_citation_audit_requires_sections_and_sources():
    with pytest.raises(ValidationError):
        CitationAudit(fixed_sections=[])
