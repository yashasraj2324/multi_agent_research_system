"""Pydantic schemas exchanged between agents."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field


class Subtask(BaseModel):
    """A single research subtask a subagent will investigate."""
    id: str = Field(description="Short unique id, e.g. 'st1'.")
    objective: str = Field(description="What the subagent must find out.")
    output_format: str = Field(description="Shape of the findings expected (list, table, timeline...).")
    suggested_queries: list[str] = Field(default_factory=list, description="2-4 seed search queries.")
    scope: Literal["broad", "narrow", "verify"] = "broad"


class ResearchPlan(BaseModel):
    """Structured plan produced by the Planner agent."""
    original_query: str
    interpretation: str = Field(description="How the planner understood the query.")
    approach: str = Field(description="Overall research strategy in 1-3 sentences.")
    complexity: Literal["simple", "moderate", "complex"] = "moderate"
    subtasks: list[Subtask]


class SourceCitation(BaseModel):
    """A single web source used by a subagent."""
    title: str
    url: str
    snippet: str = ""


class SubagentFinding(BaseModel):
    """Findings returned by a search subagent."""
    subtask_id: str
    objective: str
    key_findings: list[str] = Field(description="Bullet-pointed key facts, each self-contained.")
    sources: list[SourceCitation] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "medium"
    gaps: str = Field(default="", description="Any information gaps left.")


class GapFollowup(BaseModel):
    """A follow-up task the Lead spawns after reviewing first-pass findings."""
    id: str = Field(description="Short unique id, e.g. 'gap1'.")
    parent_subtask_id: str = Field(description="The subtask whose gap this addresses.")
    objective: str = Field(description="Specific gap to close.")
    suggested_queries: list[str] = Field(default_factory=list)


class LeadReview(BaseModel):
    """The Lead's review of first-pass findings (deep-research iterative loop)."""
    summary: str = Field(description="Brief assessment of coverage so far.")
    is_sufficient: bool = Field(description="True if no further research is required.")
    followups: list[GapFollowup] = Field(
        default_factory=list,
        description="0-4 follow-up gap-filler subtasks. Empty when is_sufficient=True.",
    )


class SectionOutline(BaseModel):
    """An outline for a single section of the report."""
    heading: str = Field(description="The section heading.")
    focus_areas: list[str] = Field(description="2-4 bullet points describing what this section must cover.")
    relevant_subtask_ids: list[str] = Field(description="List of subtask IDs (e.g. 'st1') relevant to this section.")


class ReportOutline(BaseModel):
    """Structured outline produced by the Outliner agent."""
    title: str = Field(description="Proposed title for the final report.")
    executive_summary: str = Field(description="2-4 sentence TL;DR of the whole report.")
    sections: list[SectionOutline] = Field(description="List of sections to be written.")


class ReportSection(BaseModel):
    heading: str
    body_markdown: str = Field(description="Markdown body with inline citations like [1], [2].")


class ResearchReport(BaseModel):
    """Final research report produced by the Writer agent."""
    title: str
    executive_summary: str
    sections: list[ReportSection]
    all_sources: list[SourceCitation]


class CitationAudit(BaseModel):
    """Output of the Citation Agent — a post-hoc validation & polishing pass."""
    fixed_sections: list[ReportSection] = Field(
        description="Sections rewritten with corrected/added inline citations."
    )
    ordered_sources: list[SourceCitation] = Field(
        description="Final source list in the order matching the inline [n] markers."
    )
    notes: str = Field(
        default="",
        description="Short note: unsupported claims removed, citations added, etc.",
    )


class Critique(BaseModel):
    """Output of the Critique Agent — honest quality assessment of the report."""
    weaknesses: list[str] = Field(description="2-5 concrete weaknesses.")
    missing_angles: list[str] = Field(
        default_factory=list, description="0-4 angles the report should have covered."
    )
    unsupported_claims: list[str] = Field(
        default_factory=list, description="0-3 quoted phrases lacking proper source support."
    )
    overall_score: int = Field(
        ge=0, le=10, description="0-10 honest quality score (10 = publishable, <5 = re-run)."
    )
    recommendations: list[str] = Field(
        default_factory=list, description="1-3 concrete follow-up actions."
    )


class TraceEvent(BaseModel):
    """Live-trace event emitted while agents work."""
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent: str
    stage: str
    message: str
    level: Literal["info", "success", "warn", "error"] = "info"
