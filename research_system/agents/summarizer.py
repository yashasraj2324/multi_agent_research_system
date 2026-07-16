"""Summarizer agent — compresses subagent findings before writing."""
from __future__ import annotations

from pydantic_ai import Agent

from ..core.llm import get_model


SUMMARIZER_SYSTEM_PROMPT = """You are the SYNTHESIS agent.

You receive raw findings from multiple search subagents. Your job:
1. Deduplicate overlapping facts across subagents.
2. Group related findings under clear thematic headings.
3. Highlight contradictions between sources explicitly.
4. Preserve every source URL you use in parentheses next to the claim.
5. Return a concise Markdown brief (300-600 words) that the writer will
   use to produce the final report. Do NOT write the report yourself."""


def build_summarizer_agent() -> Agent[None, str]:
    return Agent(
        get_model(),
        output_type=str,
        system_prompt=SUMMARIZER_SYSTEM_PROMPT,
        retries=1,
    )
