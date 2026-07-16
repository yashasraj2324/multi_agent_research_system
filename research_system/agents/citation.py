"""Citation Agent — validates and polishes citations in the final report.

Inspired by Anthropic's multi-agent research system:
> "the system exits the research loop and passes all findings to a
>  CitationAgent, which processes the documents and research report to
>  identify specific locations for citations. This ensures all claims are
>  properly attributed to their sources."
"""
from __future__ import annotations

from pydantic_ai import Agent

from ..core.llm import get_model
from ..core.schemas import CitationAudit


CITATION_SYSTEM_PROMPT = """You are the CITATION AGENT of a multi-agent research team.

You receive:
- The current draft ResearchReport (title, executive_summary, sections, all_sources)
- The full list of sources available (numbered)

Your job — post-hoc citation validation:
1. Every non-trivial factual claim MUST be followed by an inline citation
   marker like [1], [3], etc. that references `ordered_sources`.
2. Renumber the citations so that [1] is the FIRST cited source in reading
   order, [2] the second, and so on. `ordered_sources` MUST match that order.
3. Merge duplicate URLs so a URL appears only once in `ordered_sources`.
4. If a claim has NO supporting source in the given list, either:
   a) delete the claim, or
   b) soften it and mark it as unsupported in `notes`.
5. Preserve the substance and structure of the report. Do not paraphrase
   heavily; only touch citations, wording of unsupported claims, and order.
6. Return the polished sections in `fixed_sections`.
7. Keep `notes` under 60 words: list what you changed."""


def build_citation_agent() -> Agent[None, CitationAudit]:
    return Agent(
        get_model(),
        output_type=CitationAudit,
        system_prompt=CITATION_SYSTEM_PROMPT,
        retries=2,
    )
