"""Orchestrator — runs the full multi-agent research pipeline.

Standard mode:  Planner → parallel Search → Summarizer → Writer
Deep mode adds: Planner(deep) → parallel Search(deep) → Lead Review
                    → optional gap-filler Search subagents (iterative loop)
                    → Summarizer → Writer → Citation Agent (audit pass)

Every run gets a `MemoryStore` (per-session KV in Mongo). The orchestrator
pre-populates it with the plan + original query; subagents can fetch them
via a `memory_read` tool. This mirrors Anthropic's "save plan to Memory"
pattern from the multi-agent research article.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Callable

from .agents.citation import build_citation_agent
from .agents.critique import build_critique_agent
from .agents.planner import build_planner_agent
from .agents.reviewer import build_reviewer_agent
from .agents.search import SearchDeps, build_search_agent
from .agents.summarizer import build_summarizer_agent
from .agents.outliner import build_outliner_agent
from .agents.section_writer import build_section_writer_agent
from .agents.writer import build_writer_agent
from .core.config import config
from .db.memory import MemoryStore
from .core.schemas import (
    Critique,
    GapFollowup,
    LeadReview,
    ResearchPlan,
    ResearchReport,
    ReportSection,
    SourceCitation,
    Subtask,
    SubagentFinding,
    TraceEvent,
)
from .tools.search import TavilySearchTool


TraceCallback = Callable[[TraceEvent], None]


class ResearchOrchestrator:
    """Coordinates the multi-agent research flow (standard + deep mode)."""

    def __init__(
        self,
        on_trace: TraceCallback | None = None,
        *,
        deep: bool = False,
        max_followup_rounds: int = 1,
        session_id: str | None = None,
    ) -> None:
        self.on_trace = on_trace or (lambda ev: None)
        self.deep = deep
        self.max_followup_rounds = max_followup_rounds
        self.session_id = session_id or f"run-{uuid.uuid4().hex[:12]}"
        self.memory = MemoryStore(self.session_id)

    # ---------- helpers ----------

    def _emit(self, agent: str, stage: str, message: str, level: str = "info") -> None:
        ev = TraceEvent(agent=agent, stage=stage, message=message, level=level)  # type: ignore[arg-type]
        self.on_trace(ev)

    def _followup_to_subtask(self, gap: GapFollowup) -> Subtask:
        return Subtask(
            id=gap.id,
            objective=gap.objective,
            output_format="Concise bullet list of source-backed facts.",
            suggested_queries=gap.suggested_queries or [],
            scope="narrow",
        )

    # ---------- individual stages ----------

    async def _plan(self, query: str) -> ResearchPlan:
        self._emit(
            "Planner",
            "start",
            f"{'[DEEP] ' if self.deep else ''}Decomposing query: {query!r}",
        )
        planner = build_planner_agent(deep=self.deep)
        result = await planner.run(query)
        plan = result.output
        cap = 6 if self.deep else config.MAX_SUBAGENTS
        if len(plan.subtasks) > cap:
            plan.subtasks = plan.subtasks[:cap]
        self._emit(
            "Planner",
            "done",
            f"Plan ready: {len(plan.subtasks)} subtasks, complexity={plan.complexity}",
            level="success",
        )
        for st in plan.subtasks:
            # `spawn` events let the UI reserve a chip for each subagent up-front
            self._emit(
                "Search",
                "spawn",
                f"[{st.id}] {st.objective}",
                level="info",
            )
        return plan

    async def _run_subagent(
        self, subtask: Subtask, tool: TavilySearchTool
    ) -> SubagentFinding:
        tag = "Search" if not subtask.id.startswith("gap") else "GapFiller"
        self._emit(tag, "start", f"[{subtask.id}] {subtask.objective}")
        agent = build_search_agent(deep=self.deep)
        min_calls = "4-8" if self.deep else "2-4"
        prompt = (
            f"Subtask id: {subtask.id}\n"
            f"Objective: {subtask.objective}\n"
            f"Expected output format: {subtask.output_format}\n"
            f"Suggested queries: {subtask.suggested_queries}\n"
            f"Scope: {subtask.scope}\n\n"
            f"Use the web_search tool {min_calls} times and return a SubagentFinding."
        )
        deps = SearchDeps(tool=tool, memory=self.memory)
        try:
            result = await agent.run(prompt, deps=deps)
            finding = result.output
            finding.subtask_id = subtask.id
            # Persist finding to shared memory so later stages can look it up
            self.memory.write(f"finding.{subtask.id}", finding.model_dump(mode="json"))
            self._emit(
                tag,
                "done",
                f"[{subtask.id}] {len(finding.key_findings)} findings, "
                f"{len(finding.sources)} sources ({finding.confidence})",
                level="success",
            )
            return finding
        except Exception as exc:  # noqa: BLE001
            self._emit(tag, "error", f"[{subtask.id}] failed: {exc}", level="error")
            return SubagentFinding(
                subtask_id=subtask.id,
                objective=subtask.objective,
                key_findings=[],
                sources=[],
                confidence="low",
                gaps=f"Subagent failed: {exc}",
            )

    async def _review(
        self, query: str, plan: ResearchPlan, findings: list[SubagentFinding]
    ) -> LeadReview:
        """Deep-mode iterative loop: Lead reviews first-pass findings."""
        self._emit("LeadReview", "start", "Reviewing first-pass coverage for gaps…")
        reviewer = build_reviewer_agent()

        parts = [
            f"Original query: {query}",
            f"Plan approach: {plan.approach}",
            "",
            "Findings so far:",
        ]
        for f in findings:
            parts.append(f"### Subtask {f.subtask_id} — {f.objective}")
            parts.append(f"Confidence: {f.confidence}")
            if f.gaps:
                parts.append(f"Gaps: {f.gaps}")
            parts.append("Key findings:")
            for k in f.key_findings:
                parts.append(f"- {k}")
            parts.append("")

        result = await reviewer.run("\n".join(parts))
        review = result.output
        if len(review.followups) > 4:
            review.followups = review.followups[:4]
        self._emit(
            "LeadReview",
            "done",
            f"{'Sufficient.' if review.is_sufficient else 'Gaps found.'} "
            f"{len(review.followups)} follow-up(s).",
            level="success",
        )
        for g in review.followups:
            self._emit("LeadReview", "followup", f"[{g.id}→{g.parent_subtask_id}] {g.objective}")
        return review

    async def _synthesize(
        self, query: str, plan: ResearchPlan, findings: list[SubagentFinding]
    ) -> str:
        self._emit("Summarizer", "start", "Merging findings from all subagents…")
        summarizer = build_summarizer_agent()

        parts = [f"Original query: {query}", f"Plan approach: {plan.approach}", ""]
        for f in findings:
            parts.append(f"### Subtask {f.subtask_id} — {f.objective}")
            parts.append(f"Confidence: {f.confidence}")
            if f.gaps:
                parts.append(f"Gaps: {f.gaps}")
            parts.append("Key findings:")
            for k in f.key_findings:
                parts.append(f"- {k}")
            if f.sources:
                parts.append("Sources:")
                for s in f.sources:
                    parts.append(f"  - {s.title} — {s.url}")
            parts.append("")
        brief = "\n".join(parts)

        result = await summarizer.run(brief)
        self._emit("Summarizer", "done", "Synthesis brief ready", level="success")
        return result.output

    async def _write(
        self, query: str, brief: str, all_sources: list[SourceCitation]
    ) -> ResearchReport:
        self._emit("Outliner", "start", "Generating hierarchical report outline…")
        outliner = build_outliner_agent()

        outline_prompt = (
            f"Original user query:\n{query}\n\n"
            f"Synthesized brief:\n{brief}\n\n"
            "Now produce the ReportOutline."
        )
        
        outline_result = await outliner.run(outline_prompt)
        outline = outline_result.output
        self._emit(
            "Outliner",
            "done",
            f"Outline ready: {len(outline.sections)} sections.",
            level="success",
        )

        numbered = "\n".join(
            f"[{i + 1}] {s.title} — {s.url}" for i, s in enumerate(all_sources)
        )
        
        self._emit("SectionWriter", "start", f"Spawning {len(outline.sections)} writers in parallel…")
        
        async def _write_section(sec_idx: int, sec: object) -> ReportSection:
            writer_agent = build_section_writer_agent()
            prompt = (
                f"Original user query:\n{query}\n\n"
                f"Synthesized brief:\n{brief}\n\n"
                f"Numbered sources you may cite (use [n] inline):\n{numbered}\n\n"
                f"Your assigned Section Outline:\nHeading: {sec.heading}\nFocus Areas: {sec.focus_areas}\n\n"
                "Write your ReportSection now. Be extremely detailed."
            )
            res = await writer_agent.run(prompt)
            self._emit("SectionWriter", "done", f"Finished section {sec_idx + 1}/{len(outline.sections)}: {sec.heading}")
            return res.output

        # Run section writers in parallel
        tasks = [
            _write_section(i, sec)
            for i, sec in enumerate(outline.sections)
        ]
        sections = await asyncio.gather(*tasks)

        self._emit(
            "Compiler",
            "start",
            "Assembling sections into final ResearchReport…",
            level="success",
        )
        
        report = ResearchReport(
            title=outline.title,
            executive_summary=outline.executive_summary,
            sections=list(sections),
            all_sources=all_sources
        )
        
        return report

    async def _audit_citations(self, report: ResearchReport) -> ResearchReport:
        """Deep-mode citation audit pass (Anthropic-style CitationAgent)."""
        self._emit("CitationAgent", "start", "Auditing every claim for source attribution…")
        agent = build_citation_agent()

        numbered = "\n".join(
            f"[{i + 1}] {s.title} — {s.url}" for i, s in enumerate(report.all_sources)
        )
        current_sections = "\n\n".join(
            f"## {sec.heading}\n{sec.body_markdown}" for sec in report.sections
        )
        prompt = (
            f"Report title: {report.title}\n\n"
            f"Executive summary:\n{report.executive_summary}\n\n"
            f"Current sections:\n{current_sections}\n\n"
            f"Available sources (numbered):\n{numbered}\n\n"
            "Audit the citations, renumber consistently, and return the polished sections."
        )
        try:
            result = await agent.run(prompt)
            audit = result.output
        except Exception as exc:  # noqa: BLE001
            self._emit("CitationAgent", "error", f"Skipping audit: {exc}", level="warn")
            return report

        report.sections = audit.fixed_sections or report.sections
        if audit.ordered_sources:
            report.all_sources = audit.ordered_sources
        self._emit(
            "CitationAgent",
            "done",
            f"Audit complete — {len(report.all_sources)} sources, {audit.notes or 'no issues'}",
            level="success",
        )
        return report

    async def _critique(self, report: ResearchReport, query: str) -> Critique | None:
        """Run the Critique Agent — honest quality assessment of the final report."""
        self._emit("Critique", "start", "Critiquing final report for gaps, bias, rigor…")
        agent = build_critique_agent()

        sources_block = "\n".join(
            f"[{i + 1}] {s.title} — {s.url}"
            for i, s in enumerate(report.all_sources)
        )
        body_block = "\n\n".join(
            f"## {sec.heading}\n{sec.body_markdown}" for sec in report.sections
        )
        prompt = (
            f"Original user query:\n{query}\n\n"
            f"Report title: {report.title}\n\n"
            f"Executive summary:\n{report.executive_summary}\n\n"
            f"Body:\n{body_block}\n\n"
            f"Sources cited:\n{sources_block}\n\n"
            "Return a Critique. Be honest, specific, and terse."
        )
        try:
            result = await agent.run(prompt)
            critique = result.output
        except Exception as exc:  # noqa: BLE001
            self._emit("Critique", "error", f"Critique skipped: {exc}", level="warn")
            return None

        self._emit(
            "Critique",
            "done",
            f"Score {critique.overall_score}/10 · "
            f"{len(critique.weaknesses)} weakness(es), "
            f"{len(critique.missing_angles)} missing angle(s)",
            level="success",
        )
        return critique

    # ---------- public API ----------

    async def run(
        self, query: str
    ) -> tuple[ResearchPlan, list[SubagentFinding], ResearchReport, Critique | None]:
        tool = TavilySearchTool()

        self.memory.write("original_query", query)
        self.memory.write("mode", "deep" if self.deep else "standard")
        self._emit("Memory", "init", f"session_id={self.session_id}")

        # 1. Plan
        plan = await self._plan(query)
        self.memory.write("plan", plan.model_dump(mode="json"))
        self._emit("Memory", "write", "plan saved (key='plan')")

        # 2. First-pass parallel search
        findings: list[SubagentFinding] = list(
            await asyncio.gather(
                *[self._run_subagent(st, tool) for st in plan.subtasks]
            )
        )

        # 3. Deep mode: iterative gap-filling loop
        if self.deep:
            rounds = 0
            while rounds < self.max_followup_rounds:
                rounds += 1
                review = await self._review(query, plan, findings)
                if review.is_sufficient or not review.followups:
                    break
                self._emit(
                    "LeadReview",
                    "spawn",
                    f"Spawning {len(review.followups)} gap-filler subagent(s), round {rounds}",
                )
                followup_subtasks = [self._followup_to_subtask(g) for g in review.followups]
                # Emit spawn events so UI reserves chips for gap-fillers too
                for st in followup_subtasks:
                    self._emit("GapFiller", "spawn", f"[{st.id}] {st.objective}")
                followup_findings = await asyncio.gather(
                    *[self._run_subagent(st, tool) for st in followup_subtasks]
                )
                findings.extend(followup_findings)
                plan.subtasks.extend(followup_subtasks)

        # 4. Deduplicate sources
        seen: dict[str, SourceCitation] = {}
        for f in findings:
            for s in f.sources:
                if s.url and s.url not in seen:
                    seen[s.url] = s
        all_sources = list(seen.values())

        # 5. Synthesize + write
        brief = await self._synthesize(query, plan, findings)
        report = await self._write(query, brief, all_sources)

        # 6. Deep mode: citation audit
        if self.deep:
            report = await self._audit_citations(report)

        # 7. Critique pass (always) — honest quality signal
        critique = await self._critique(report, query)

        return plan, findings, report, critique
