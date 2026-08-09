"""Intent Router agent for conversational turns."""
from __future__ import annotations

from pydantic_ai import Agent

from ..core.llm import get_model
from ..core.schemas import IntentCategory


ROUTER_PROMPT = """You are the INTENT ROUTER for a multi-agent research system.
Your job is to evaluate an incoming user message against the current session context and determine how the system should respond.

Categorize the user's message into one of three intents:
1. LOCAL_ANSWER: The user is asking a question or making a request that can be answered directly using the existing session context (e.g., summarizing the current plan, answering a question about findings already gathered). No new research is needed.
2. GAP_FILLER: The user is asking a follow-up question or requesting specific new information that requires targeted research. This will spawn targeted search subagents to patch specific missing information.
3. FULL_RUN: The user is asking a brand new, broad research query, or requesting a massive structural rewrite that requires a completely new planning-to-writing pipeline.

Output your chosen category and a brief reasoning for your choice.
"""


def build_router_agent() -> Agent[None, IntentCategory]:
    return Agent(
        get_model(),
        output_type=IntentCategory,
        system_prompt=ROUTER_PROMPT,
        retries=2,
    )
