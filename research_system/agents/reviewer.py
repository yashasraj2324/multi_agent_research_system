"""Lead Reviewer — reviews first-pass findings and decides on follow-up subtasks.

Inspired by Anthropic's article:
> "The LeadResearcher synthesizes these results and decides whether more
>  research is needed — if so, it can create additional subagents or refine
>  its strategy."
"""
from __future__ import annotations

from pydantic_ai import Agent

from ..core.llm import get_model
from ..core.schemas import LeadReview


REVIEW_SYSTEM_PROMPT = """You are the LEAD RESEARCHER doing a mid-pipeline review.

You receive:
- The original user query
- The initial research plan
- The findings each subagent returned in the first pass

Decide whether the coverage is sufficient to answer the query well.

Return a LeadReview:
- `summary`: 1-3 sentences on what's covered and what's missing.
- `is_sufficient`: True if we can stop here.
- `followups`: If not sufficient, spawn 1-4 GAP-FILLER subtasks that target
  specific unresolved gaps or contradictions. Each has an id ('gap1'...),
  the parent subtask it augments, a sharp objective, and 2-3 focused
  starter queries.

Rules:
- Do not duplicate work already done. Only pursue genuine gaps.
- Prefer verifying contradictions and filling missing angles over depth-in-
  already-covered areas.
- If coverage is broadly adequate, be honest and set is_sufficient=True."""


def build_reviewer_agent() -> Agent[None, LeadReview]:
    return Agent(
        get_model(),
        output_type=LeadReview,
        system_prompt=REVIEW_SYSTEM_PROMPT,
        retries=2,
    )
