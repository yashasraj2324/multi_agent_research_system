"""Outliner agent — generates a detailed report outline based on findings."""
from __future__ import annotations

from pydantic_ai import Agent

from ..core.llm import get_model
from ..core.schemas import ReportOutline


OUTLINER_SYSTEM_PROMPT = """You are the OUTLINER agent for a research team.

You receive:
- The original user query
- A synthesized brief containing findings from multiple search subagents

Your goal is to produce a comprehensive `ReportOutline` that will be used to generate a deeply detailed research report.
The outline should scale with the complexity of the findings. 
- Create a specific, clear title for the report.
- Write a 2-4 sentence executive summary.
- Define a list of sections to be written (up to 7 sections).
- For each section, provide a heading, 2-4 focus areas (bullet points describing what the section must cover), and map it to the relevant subtask IDs from the findings.

Rules:
- Make the sections logically flow from introduction to conclusion/analysis.
- Ensure the focus areas are highly specific to guide the section writers.
- Do not invent information; base the outline strictly on the synthesized brief."""


def build_outliner_agent() -> Agent[None, ReportOutline]:
    return Agent(
        get_model(),
        output_type=ReportOutline,
        system_prompt=OUTLINER_SYSTEM_PROMPT,
        retries=2,
    )
