"""Streamlit UI for the Pydantic-AI multi-agent research system.

Architecture (Anthropic-style orchestrator-worker):

    Planner  ->  Search subagents (parallel, Tavily tool)  ->  Summarizer  ->  Writer

All four agents are built with `pydantic-ai`. LLM calls go through
Azure OpenAI via standard API keys.
"""
from __future__ import annotations

import asyncio
import json
import os
import queue as thread_queue
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_system.observabilty.telemetry import setup_observability  # noqa: E402
from research_system.core.config import config  # noqa: E402

# Initialize OpenTelemetry + Logfire instrumentation once at application startup
setup_observability()
from research_system.utils.export import report_to_markdown, report_to_pdf_bytes  # noqa: E402
from research_system.db.history import (  # noqa: E402
    delete_research,
    list_history,
    load_research,
    save_research,
)
from research_system.orchestrator import ResearchOrchestrator  # noqa: E402
from research_system.core.schemas import (  # noqa: E402
    ResearchPlan,
    ResearchReport,
    SubagentFinding,
    TraceEvent,
)


# ------------------------------ Page setup ------------------------------ #

st.set_page_config(
    page_title="Pydantic-AI · Multi-Agent Research",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
)

CUSTOM_CSS = """
<style>
:root {
  --bg: #0d1117;
  --panel: #161b22;
  --panel-2: #1c232d;
  --border: #30363d;
  --text: #e6edf3;
  --muted: #8b949e;
  --accent: #f4a261;
  --accent-2: #e76f51;
  --ok: #3fb950;
  --info: #58a6ff;
  --err: #f85149;
}
html, body, .stApp { background: var(--bg) !important; color: var(--text) !important; }
section[data-testid="stSidebar"] { background: #0a0e13 !important; border-right: 1px solid var(--border); }
h1, h2, h3, h4 { font-family: 'JetBrains Mono', 'Fira Code', monospace !important; letter-spacing: -0.01em; }
.stMarkdown a { color: var(--accent) !important; }
.stButton>button {
    background: var(--panel-2); color: var(--text); border: 1px solid var(--border);
    border-radius: 10px; padding: 0.45rem 0.9rem; font-family: monospace;
    transition: all .18s ease;
}
.stButton>button:hover { border-color: var(--accent); color: var(--accent); transform: translateY(-1px); }
.stButton>button:focus { box-shadow: 0 0 0 2px rgba(244,162,97,0.35) !important; }
.stTextArea textarea, .stTextInput input {
    background: var(--panel) !important; color: var(--text) !important;
    border: 1px solid var(--border) !important; border-radius: 10px !important;
    font-family: monospace !important;
}
.stDownloadButton>button { background: var(--accent); color: #14181f; border: 0; font-weight: 600; }
.stDownloadButton>button:hover { background: var(--accent-2); color: #fff; }
div[data-testid="stExpander"] {
    background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
}
.agent-badge {
    display: inline-block; padding: 2px 10px; border-radius: 999px;
    font-family: monospace; font-size: 0.72rem; letter-spacing: 0.05em; text-transform: uppercase;
    background: #21262d; border: 1px solid var(--border); color: var(--muted);
}
.agent-badge.planner     { color: #f4a261; border-color: #f4a26155; background:#2a1c10; }
.agent-badge.search      { color: #58a6ff; border-color: #58a6ff55; background:#0f1c2b; }
.agent-badge.summarizer  { color: #d29922; border-color: #d2992255; background:#2a2110; }
.agent-badge.writer      { color: #3fb950; border-color: #3fb95055; background:#0e2415; }
.agent-badge.citation    { color: #a371f7; border-color: #a371f755; background:#1a1230; }
.agent-badge.memory      { color: #8b949e; border-color: #8b949e55; background:#161b22; }
.agent-badge.critique    { color: #ff7b72; border-color: #ff7b7255; background:#2a1214; }
.agent-badge.err         { color: #f85149; border-color: #f8514955; background:#2a1114; }
.tag {
    display:inline-block; padding: 1px 8px; border-radius: 6px;
    font-family: monospace; font-size:0.72rem; color: var(--muted);
    background: #161b22; border:1px solid var(--border); margin-right:4px;
}
.hero {
    padding: 26px 28px; border-radius: 18px; margin-bottom: 18px;
    background:
       radial-gradient(1200px 300px at 0% 0%, rgba(244,162,97,0.12), transparent 60%),
       radial-gradient(900px 260px at 100% 100%, rgba(88,166,255,0.10), transparent 60%),
       linear-gradient(180deg, #11161d, #0d1117);
    border: 1px solid var(--border);
}
.hero h1 { margin: 0 0 6px 0; font-size: 1.8rem; }
.hero p  { margin: 0; color: var(--muted); }
.trace-row { padding: 8px 10px; border-left: 2px solid var(--border); margin: 6px 0 6px 4px; }
.trace-row.planner    { border-left-color: var(--accent); }
.trace-row.search     { border-left-color: var(--info); }
.trace-row.summarizer { border-left-color: #d29922; }
.trace-row.writer     { border-left-color: var(--ok); }
.trace-row.citation   { border-left-color: #a371f7; }
.trace-row.memory     { border-left-color: #8b949e; }
.trace-row.critique   { border-left-color: #ff7b72; }
.trace-row.err        { border-left-color: var(--err); }

/* Live subagent chip row */
.chip-row { display: flex; flex-wrap: wrap; gap: 8px; margin: 6px 0 14px 0; }
.chip {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 4px 10px; border-radius: 999px;
    font-family: monospace; font-size: 0.78rem;
    border: 1px solid var(--border); background: #161b22; color: var(--muted);
    transition: all .18s ease;
}
.chip.pending  { opacity: 0.45; }
.chip.running  { border-color: #58a6ff; color: #58a6ff; background: #0f1c2b;
                 animation: pulse 1.4s ease-in-out infinite; }
.chip.done     { border-color: #3fb950; color: #3fb950; background: #0e2415; }
.chip.error    { border-color: #f85149; color: #f85149; background: #2a1114; }
.chip .dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
@keyframes pulse {
    0%,100% { box-shadow: 0 0 0 0 rgba(88,166,255,0.35); }
    50%     { box-shadow: 0 0 0 6px rgba(88,166,255,0.00); }
}
.small { color: var(--muted); font-size: 0.82rem; }
hr { border-color: var(--border) !important; }
.finding-src a { color: var(--info) !important; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ------------------------------ Session state ------------------------------ #

if "trace" not in st.session_state:
    st.session_state.trace = []
if "report" not in st.session_state:
    st.session_state.report = None
if "plan" not in st.session_state:
    st.session_state.plan = None
if "findings" not in st.session_state:
    st.session_state.findings = None
if "critique" not in st.session_state:
    st.session_state.critique = None
if "query" not in st.session_state:
    st.session_state.query = ""
if "running" not in st.session_state:
    st.session_state.running = False


# ------------------------------ Sidebar ------------------------------ #

def _load_history(rid: int) -> None:
    rec = load_research(rid)
    if not rec:
        return
    st.session_state.query = rec["query"]
    st.session_state.plan = rec["plan"].model_dump()
    st.session_state.findings = [f.model_dump() for f in rec["findings"]]
    st.session_state.report = rec["report"].model_dump()
    st.session_state.critique = None  # history rows don't persist critique
    st.session_state.trace = []


with st.sidebar:
    st.markdown("### 🧭 Research Console")
    st.caption("Multi-agent research with **Pydantic-AI** + Tavily.")

    st.markdown("---")
    st.markdown("**LLM provider**")
    provider_options = {
        "azure": "Azure OpenAI (GPT-4o)",
    }
    default_prov = config.active_provider()
    provider = st.selectbox(
        "Provider",
        options=list(provider_options.keys()),
        format_func=lambda k: provider_options[k],
        index=list(provider_options.keys()).index(default_prov)
        if default_prov in provider_options
        else 0,
        label_visibility="collapsed",
        key="llm_provider",
    )
    os.environ["LLM_PROVIDER"] = provider

    model_now = config.AZURE_OPENAI_DEPLOYMENT_NAME
    st.caption(f"Model: `{model_now}`")

    st.markdown("---")
    st.markdown("**Status**")
    llm_ok = config.is_llm_configured(provider)
    tav_ok = config.is_tavily_configured()
    key_hint = "AZURE_OPENAI_API_KEY"
    st.markdown(f"- LLM key: {'🟢 ready' if llm_ok else '🔴 add ' + key_hint}")
    st.markdown(f"- Tavily search: {'🟢 ready' if tav_ok else '🟠 add TAVILY_API_KEY'}")
    if not tav_ok:
        st.info(
            "Web search is disabled without `TAVILY_API_KEY`.\n\n"
            "Add it to `/app/backend/.env` then rerun."
        )

    st.markdown("---")
    st.markdown("### History")
    try:
        rows = list_history(limit=30)
    except Exception as exc:  # noqa: BLE001
        st.error(f"History DB error: {exc}")
        rows = []

    if not rows:
        st.caption("_No past research runs yet._")
    else:
        for row in rows:
            label = (row["query"][:60] + "…") if len(row["query"]) > 60 else row["query"]
            c1, c2 = st.columns([5, 1])
            if c1.button(f"#{row['id']} · {label}", key=f"open_{row['id']}", use_container_width=True):
                _load_history(row["id"])
                st.rerun()
            if c2.button("🗑", key=f"del_{row['id']}"):
                delete_research(row["id"])
                st.rerun()

    st.markdown("---")
    st.caption("Search: Tavily API")


# ------------------------------ Hero ------------------------------ #

st.markdown(
    """
    <div class="hero">
      <h1>Multi-Agent Research System</h1>
      <p>A Planner decomposes your question, parallel Search subagents gather evidence with Tavily,
      a Summarizer merges findings, and a Writer produces a cited report — all coordinated with
      <b>Pydantic-AI</b>.
      Toggle <b>🔬 Deep</b> to add an iterative gap-filling loop and a dedicated Citation Agent
      (inspired by Anthropic's multi-agent research architecture).</p>
    </div>
    """,
    unsafe_allow_html=True,
)


# ------------------------------ Query input ------------------------------ #

col_q, col_run = st.columns([5, 1])
with col_q:
    query = st.text_area(
        "Research question",
        value=st.session_state.query,
        placeholder="e.g.  Compare the leading open-source vector databases in 2025 by ecosystem, cost, and scalability.",
        height=100,
        label_visibility="collapsed",
        key="query_input",
    )
with col_run:
    st.write("")
    st.write("")
    deep_mode = st.toggle(
        "🔬 Deep",
        value=st.session_state.get("deep_mode", False),
        help=(
            "Deep-research mode:\n"
            "• Planner spawns 5-6 subagents (broader coverage)\n"
            "• Subagents run 4-8 web searches each (vs 2-4)\n"
            "• Lead reviews first-pass findings and spawns gap-fillers\n"
            "• Dedicated Citation Agent audits every claim\n"
            "\nUses ~3-5× more tokens. Inspired by Anthropic's multi-agent research."
        ),
        key="deep_mode",
    )
    run_clicked = st.button(
        "▶ Research",
        type="primary",
        use_container_width=True,
        disabled=st.session_state.running or not config.is_llm_configured(),
    )


# ------------------------------ Runner ------------------------------ #

def _run_pipeline_threaded(user_query: str, deep: bool, provider: str, q: thread_queue.Queue) -> None:
    def on_trace(ev: TraceEvent) -> None:
        q.put(("event", ev.model_dump(mode="json")))

    async def _go() -> None:
        os.environ["LLM_PROVIDER"] = provider
        try:
            orch = ResearchOrchestrator(on_trace=on_trace, deep=deep)
            plan, findings, report, critique = await orch.run(user_query)
            q.put(("done", {
                "plan": plan.model_dump(mode="json"),
                "findings": [f.model_dump(mode="json") for f in findings],
                "report": report.model_dump(mode="json"),
                "critique": critique.model_dump(mode="json") if critique else None,
                "deep": deep,
                "provider": provider,
            }))
        except Exception as exc:  # noqa: BLE001
            q.put(("error", str(exc)))

    asyncio.run(_go())


def _agent_class(agent: str) -> str:
    a = agent.lower()
    if a.startswith("plan"):
        return "planner"
    if a.startswith("search"):
        return "search"
    if a.startswith("gapfiller"):
        return "search"
    if a.startswith("leadreview"):
        return "planner"
    if a.startswith("citation"):
        return "citation"
    if a.startswith("memory"):
        return "memory"
    if a.startswith("summar"):
        return "summarizer"
    if a.startswith("write"):
        return "writer"
    return "err"


def _agent_badge(agent: str, level: str) -> str:
    cls = _agent_class(agent)
    if level == "error":
        cls = "err"
    label = {
        "planner": "Lead" if agent.lower().startswith("leadreview") else "Lead Researcher",
        "search": "Gap-Filler" if agent.lower().startswith("gapfiller") else "Search Subagent",
        "summarizer": "Summarizer",
        "writer": "Writer",
        "citation": "Citation Agent",
        "critique": "Critique Agent",
        "memory": "Memory",
    }.get(cls, agent)
    return f'<span class="agent-badge {cls}">{label}</span>'


# Extract subtask id (e.g. "[st1] …" or "[gap2] …") from a trace message.
import re as _re  # noqa: E402

_SUBTASK_RE = _re.compile(r"\[(st\d+|gap\d+)\]")


def _extract_subtask_id(message: str) -> str | None:
    m = _SUBTASK_RE.search(message or "")
    return m.group(1) if m else None


if run_clicked and query.strip():
    st.session_state.running = True
    st.session_state.trace = []
    st.session_state.report = None
    st.session_state.plan = None
    st.session_state.findings = None
    st.session_state.query = query.strip()

    q: thread_queue.Queue = thread_queue.Queue()
    worker = threading.Thread(
        target=_run_pipeline_threaded,
        args=(query.strip(), bool(deep_mode), provider, q),
        daemon=True,
    )
    worker.start()

    mode_label = "🔬 Deep Research" if deep_mode else "Standard Research"
    prov_label = "Azure OpenAI"
    st.markdown(f"### Live Agent Trace · _{mode_label}_ · `{prov_label}`")

    chip_row = st.empty()                 # live subagent chip strip
    trace_container = st.container()      # streaming event log
    subagent_state: dict[str, str] = {}   # id -> pending|running|done|error

    def _render_chips() -> None:
        if not subagent_state:
            chip_row.markdown("")
            return
        chips = []
        for sid, state in subagent_state.items():
            icon = {"pending": "•", "running": "◈", "done": "✓", "error": "✗"}[state]
            chips.append(
                f'<span class="chip {state}"><span class="dot"></span>'
                f'{icon}&nbsp;{sid}</span>'
            )
        chip_row.markdown(
            f'<div class="chip-row">{"".join(chips)}</div>',
            unsafe_allow_html=True,
        )

    final_payload: dict[str, Any] | None = None
    error_msg: str | None = None

    with st.status("Agents at work…", expanded=True) as status:
        while True:
            try:
                kind, data = q.get(timeout=0.4)
            except thread_queue.Empty:
                if not worker.is_alive():
                    break
                continue

            if kind == "event":
                st.session_state.trace.append(data)

                # ----- update chip state from subagent lifecycle events -----
                sid = _extract_subtask_id(data.get("message", ""))
                agent = data.get("agent", "").lower()
                stage = data.get("stage", "")
                if sid and (agent.startswith("search") or agent.startswith("gapfiller")):
                    if stage == "spawn":
                        subagent_state.setdefault(sid, "pending")
                    elif stage == "start":
                        subagent_state[sid] = "running"
                    elif stage == "done":
                        subagent_state[sid] = "done"
                    elif stage == "error":
                        subagent_state[sid] = "error"
                    _render_chips()

                cls = _agent_class(data["agent"])
                if data.get("level") == "error":
                    cls = "err"
                badge = _agent_badge(data["agent"], data.get("level", "info"))
                trace_container.markdown(
                    f'<div class="trace-row {cls}">{badge} &nbsp; '
                    f'<span class="small">{data.get("stage","")}</span> '
                    f'{data["message"]}</div>',
                    unsafe_allow_html=True,
                )
                status.update(label=f"{data['agent']}: {data['message'][:80]}")

            elif kind == "done":
                final_payload = data
                break
            elif kind == "error":
                error_msg = data
                break

        worker.join(timeout=1.0)
        if error_msg:
            status.update(label=f"Failed: {error_msg}", state="error")
        else:
            status.update(label="Research complete ✔", state="complete")

    if error_msg:
        st.error(f"Pipeline error: {error_msg}")
        st.session_state.running = False
    elif final_payload:
        st.session_state.plan = final_payload["plan"]
        st.session_state.findings = final_payload["findings"]
        st.session_state.report = final_payload["report"]
        st.session_state.critique = final_payload.get("critique")

        try:
            save_research(
                st.session_state.query,
                ResearchPlan.model_validate(final_payload["plan"]),
                [SubagentFinding.model_validate(f) for f in final_payload["findings"]],
                ResearchReport.model_validate(final_payload["report"]),
            )
        except Exception as exc:  # noqa: BLE001
            st.warning(f"Could not persist session: {exc}")

        st.session_state.running = False
        st.rerun()


# ------------------------------ Persistent view ------------------------------ #

if st.session_state.report and not st.session_state.running:
    plan_dict: dict = st.session_state.plan or {}
    findings_list: list[dict] = st.session_state.findings or []
    report_dict: dict = st.session_state.report

    report_obj = ResearchReport.model_validate(report_dict)

    st.markdown("---")
    tab_report, tab_plan, tab_subs, tab_critique, tab_trace, tab_export = st.tabs(
        ["📄 Report", "🧭 Plan", "🛰 Subagents", "🎯 Critique", "🕰 Trace", "⬇ Export"]
    )

    with tab_report:
        st.markdown(f"## {report_obj.title}")
        st.markdown(f"> **Query:** {st.session_state.query}")
        st.markdown("#### Executive Summary")
        st.markdown(report_obj.executive_summary)
        for sec in report_obj.sections:
            st.markdown(f"#### {sec.heading}")
            st.markdown(sec.body_markdown)
        if report_obj.all_sources:
            st.markdown("#### Sources")
            for i, s in enumerate(report_obj.all_sources, start=1):
                st.markdown(f"[{i}] [{s.title or s.url}]({s.url})")

    with tab_plan:
        st.markdown(
            f"<span class='tag'>complexity: {plan_dict.get('complexity','?')}</span>"
            f"<span class='tag'>subtasks: {len(plan_dict.get('subtasks', []))}</span>",
            unsafe_allow_html=True,
        )
        st.markdown(f"**Interpretation:** {plan_dict.get('interpretation','')}")
        st.markdown(f"**Approach:** {plan_dict.get('approach','')}")
        for i, t in enumerate(plan_dict.get("subtasks", []), 1):
            with st.expander(
                f"Subtask {i} · [{t.get('id','')}] {t.get('objective','')}  "
                f"({t.get('scope','')})"
            ):
                st.markdown(f"**Expected output:** {t.get('output_format','')}")
                st.markdown("**Suggested starter queries**")
                for qq in t.get("suggested_queries", []):
                    st.markdown(f"- `{qq}`")

    with tab_subs:
        if not findings_list:
            st.caption("_No subagent findings._")
        for f_dict in findings_list:
            f = SubagentFinding.model_validate(f_dict)
            with st.expander(
                f"🛰 [{f.subtask_id}] · {len(f.key_findings)} finding(s) · "
                f"{len(f.sources)} source(s) · confidence: {f.confidence}"
            ):
                st.markdown(f"**Objective:** {f.objective}")
                if f.key_findings:
                    st.markdown("**Key findings**")
                    for k in f.key_findings:
                        st.markdown(f"- {k}")
                if f.sources:
                    st.markdown("**Sources**")
                    for s in f.sources:
                        st.markdown(
                            f'<div class="finding-src">• <a href="{s.url}" target="_blank">'
                            f'{s.title or s.url}</a></div>',
                            unsafe_allow_html=True,
                        )
                if f.gaps:
                    st.markdown(f"**Gaps:** {f.gaps}")

    with tab_critique:
        crit = st.session_state.get("critique")
        if not crit:
            st.caption("_No critique available for this run (either skipped or older loaded run)._")
        else:
            score = crit.get("overall_score", 0)
            colour = "#3fb950" if score >= 7 else ("#d29922" if score >= 5 else "#f85149")
            st.markdown(
                f"<div style='padding:14px 18px;border:1px solid {colour}55;"
                f"background:{colour}18;border-radius:12px;font-family:monospace'>"
                f"<span style='font-size:1.4rem;color:{colour};font-weight:700'>"
                f"Overall score: {score}/10</span></div>",
                unsafe_allow_html=True,
            )
            if crit.get("weaknesses"):
                st.markdown("#### Weaknesses")
                for w in crit["weaknesses"]:
                    st.markdown(f"- {w}")
            if crit.get("missing_angles"):
                st.markdown("#### Missing angles")
                for m in crit["missing_angles"]:
                    st.markdown(f"- {m}")
            if crit.get("unsupported_claims"):
                st.markdown("#### Unsupported claims")
                for u in crit["unsupported_claims"]:
                    st.markdown(f"- _{u}_")
            if crit.get("recommendations"):
                st.markdown("#### Recommended next steps")
                for r in crit["recommendations"]:
                    st.markdown(f"- {r}")

    with tab_trace:
        if not st.session_state.trace:
            st.caption("_Trace was not saved for this loaded run._")
        for ev in st.session_state.trace:
            cls = _agent_class(ev["agent"])
            if ev.get("level") == "error":
                cls = "err"
            badge = _agent_badge(ev["agent"], ev.get("level", "info"))
            st.markdown(
                f'<div class="trace-row {cls}">{badge} '
                f'<span class="small">{ev.get("stage","")}</span> · '
                f'<span class="small">{ev.get("ts","")}</span><br>{ev["message"]}</div>',
                unsafe_allow_html=True,
            )

    with tab_export:
        md = report_to_markdown(st.session_state.query, report_obj)
        safe_stem = (report_obj.title[:60].replace(" ", "_") or "research_report")
        st.download_button(
            "⬇ Download Markdown (.md)",
            data=md.encode("utf-8"),
            file_name=f"{safe_stem}.md",
            mime="text/markdown",
            use_container_width=True,
        )
        try:
            pdf_bytes = report_to_pdf_bytes(st.session_state.query, report_obj)
            st.download_button(
                "⬇ Download PDF (.pdf)",
                data=pdf_bytes,
                file_name=f"{safe_stem}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"PDF generation failed: {exc}")

        with st.expander("Raw JSON"):
            st.code(json.dumps(report_dict, indent=2), language="json")

elif not st.session_state.running:
    st.markdown(
        """
        <div class="small" style="margin-top:24px">
        Try one of these:
        <ul>
          <li><i>What are the top 5 causes of GPU underutilization in LLM training pipelines?</i></li>
          <li><i>Compare LangGraph, CrewAI, and Pydantic-AI for building agentic apps in 2025.</i></li>
          <li><i>How did European AI-regulation change between 2023 and 2025?</i></li>
        </ul>
        </div>
        """,
        unsafe_allow_html=True,
    )
