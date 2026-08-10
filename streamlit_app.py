"""Streamlit UI for the Pydantic-AI multi-agent research system."""
from __future__ import annotations

import asyncio
import json
import os
import queue as thread_queue
import sys
import threading
import uuid
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
from research_system.db.memory import SessionManager, MemoryStore  # noqa: E402
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
.stTextArea textarea, .stTextInput input, .stChatInputContainer {
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

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "trace" not in st.session_state:
    st.session_state.trace = []
if "running" not in st.session_state:
    st.session_state.running = False
if "deep_mode" not in st.session_state:
    st.session_state.deep_mode = False

session_manager = SessionManager()

# ------------------------------ Sidebar ------------------------------ #

with st.sidebar:
    st.markdown("### 🧭 Research Console")
    st.caption("Conversational Multi-Agent Research")

    if st.button("➕ New Chat", use_container_width=True):
        st.session_state.session_id = str(uuid.uuid4())
        st.session_state.trace = []
        st.rerun()

    st.markdown("---")
    st.markdown("**LLM provider**")
    provider_options = {"azure": "Azure OpenAI (GPT-4o)"}
    default_prov = config.active_provider()
    provider = st.selectbox(
        "Provider",
        options=list(provider_options.keys()),
        format_func=lambda k: provider_options[k],
        index=list(provider_options.keys()).index(default_prov) if default_prov in provider_options else 0,
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
    st.markdown(f"- LLM key: {'🟢 ready' if llm_ok else '🔴 add key'}")
    st.markdown(f"- Tavily search: {'🟢 ready' if tav_ok else '🟠 add TAVILY_API_KEY'}")
    
    st.markdown("---")
    deep_mode = st.toggle("🔬 Deep Mode", value=st.session_state.deep_mode, key="deep_mode")

    st.markdown("---")
    st.markdown("### Recent Chats")
    
    try:
        sessions = session_manager.list_sessions(limit=30)
    except Exception as exc:  # noqa: BLE001
        st.error(f"DB Error: {exc}")
        sessions = []

    if not sessions:
        st.caption("_No past chats._")
    else:
        seen_sessions = set()
        for s in sessions:
            if s.session_id in seen_sessions:
                continue
            seen_sessions.add(s.session_id)
            
            label_text = s.session_id[:8]
            if s.messages:
                first_msg = next((m["content"] for m in s.messages if m["role"] == "user"), None)
                if first_msg:
                    label_text = (first_msg[:30] + "…") if len(first_msg) > 30 else first_msg
            
            # Simple button row
            if st.button(f"💬 {label_text}", key=f"open_{s.session_id}", use_container_width=True):
                st.session_state.session_id = s.session_id
                st.session_state.trace = []  # will be loaded from MemoryStore in the tab
                st.rerun()

# ------------------------------ Helper Functions ------------------------------ #

def _agent_class(agent: str) -> str:
    a = agent.lower()
    if a.startswith("plan"): return "planner"
    if a.startswith("search") or a.startswith("gapfiller"): return "search"
    if a.startswith("leadreview"): return "planner"
    if a.startswith("followup"): return "search"
    if a.startswith("outliner"): return "planner"
    if a.startswith("sectionwriter") or a.startswith("compiler"): return "writer"
    if a.startswith("citation"): return "citation"
    if a.startswith("memory"): return "memory"
    if a.startswith("context"): return "memory"
    if a.startswith("summar"): return "summarizer"
    if a.startswith("write"): return "writer"
    if a.startswith("router"): return "memory"
    return "err"

def _agent_badge(agent: str, level: str) -> str:
    cls = _agent_class(agent)
    if level == "error": cls = "err"
    label = {
        "planner": "Lead" if agent.lower().startswith("leadreview") else "Lead Researcher",
        "search": "Gap-Filler" if agent.lower().startswith("gapfiller") else "Search Subagent",
        "summarizer": "Summarizer",
        "writer": "Writer",
        "citation": "Citation Agent",
        "critique": "Critique Agent",
        "memory": "Memory",
        "router": "Intent Router"
    }.get(cls, agent)
    return f'<span class="agent-badge {cls}">{label}</span>'

import re as _re  # noqa: E402
_SUBTASK_RE = _re.compile(r"\[((?:st|gap)[\w-]*)\]")

def _extract_subtask_id(message: str) -> str | None:
    m = _SUBTASK_RE.search(message or "")
    return m.group(1) if m else None

def _run_pipeline_threaded(session_id: str, user_query: str, deep: bool, provider: str, q: thread_queue.Queue) -> None:
    def on_trace(ev: TraceEvent) -> None:
        q.put(("event", ev.model_dump(mode="json")))

    async def _go() -> None:
        os.environ["LLM_PROVIDER"] = provider
        try:
            orch = ResearchOrchestrator(on_trace=on_trace, deep=deep, session_id=session_id)
            intent = await orch.chat_turn(session_id, user_query)
            q.put(("done", {"intent": intent.model_dump(mode="json")}))
        except Exception as exc:  # noqa: BLE001
            q.put(("error", str(exc)))

    asyncio.run(_go())

# ------------------------------ Main Layout ------------------------------ #

st.markdown(
    """
    <div class="hero">
      <h1>Multi-Agent Research Chat</h1>
      <p>Conversational intent routing combined with deep research execution. Ask a question to trigger a full search, or ask a follow-up to check your memory.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

col_chat, col_memory = st.columns([2, 1])

current_session = session_manager.get_or_create_session(st.session_state.session_id)
mem_store = MemoryStore(st.session_state.session_id)

with col_chat:
    # Render messages
    for msg in current_session.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if prompt := st.chat_input("Ask a research question...", disabled=st.session_state.running or not llm_ok):
        st.session_state.running = True
        session_manager.append_message(st.session_state.session_id, "user", prompt)
        st.rerun()

    # If running, process the request
    if st.session_state.running:
        last_msg = current_session.messages[-1]
        if last_msg["role"] == "user":
            q: thread_queue.Queue = thread_queue.Queue()
            worker = threading.Thread(
                target=_run_pipeline_threaded,
                args=(st.session_state.session_id, last_msg["content"], st.session_state.deep_mode, provider, q),
                daemon=True,
            )
            worker.start()

            with st.chat_message("assistant"):
                chip_row = st.empty()
                trace_container = st.container()
                subagent_state: dict[str, str] = {}
                
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
                
                final_payload = None
                error_msg = None
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
                        status.update(label="Complete ✔", state="complete")
            
            if error_msg:
                st.error(f"Error: {error_msg}")
                st.session_state.running = False
            elif final_payload:
                intent = final_payload["intent"]
                intent_cat = intent["category"]
                
                # Save trace to memory store
                mem_store.write("trace", st.session_state.trace)
                
                msg_content = intent.get("response") or (
                    f"Handled as **{intent_cat}**.\n\n"
                    f"Reasoning: {intent['reasoning']}\n\n"
                    "Check the **Scratchpad & Memory** tab for any updates."
                )
                session_manager.append_message(st.session_state.session_id, "assistant", msg_content)
                st.session_state.running = False
                st.rerun()

with col_memory:
    st.markdown("### Scratchpad & Memory")
    tab_scratch, tab_mem, tab_trace, tab_export = st.tabs(["📝 Scratchpad", "🧠 Working Memory", "🕰 Trace", "📄 Export"])

    with tab_scratch:
        plan_data = mem_store.read("plan")
        if plan_data:
            plan = ResearchPlan.model_validate(plan_data)
            st.markdown(f"**Interpretation:** {plan.interpretation}")
            st.markdown(f"**Approach:** {plan.approach}")
            for t in plan.subtasks:
                with st.expander(f"[{t.id}] {t.objective}"):
                    st.markdown(f"**Scope:** {t.scope} | **Format:** {t.output_format}")
        else:
            st.info("No active plan generated yet. Run a FULL_RUN query to start.")
            
    with tab_mem:
        keys = mem_store.keys()
        finding_keys = [k for k in keys if k.startswith("finding.")]
        if not finding_keys:
            st.info("No findings collected yet.")
        else:
            for fk in finding_keys:
                f_data = mem_store.read(fk)
                if f_data:
                    finding = SubagentFinding.model_validate(f_data)
                    with st.expander(f"[{finding.subtask_id}] {finding.objective}"):
                        if finding.key_findings:
                            for kf in finding.key_findings:
                                st.markdown(f"- {kf}")
                        if finding.sources:
                            st.markdown("**Sources:**")
                            for s in finding.sources:
                                st.markdown(f"- [{s.title or s.url}]({s.url})")
                        if finding.gaps:
                            st.markdown(f"**Gaps:** {finding.gaps}")

    with tab_trace:
        trace_data = mem_store.read("trace")
        display_trace = st.session_state.trace if st.session_state.running else (trace_data or [])
        if not display_trace:
            st.caption("No trace available.")
        for ev in display_trace:
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
        report_data = mem_store.read("report")
        if not report_data:
            st.caption("No report available yet. Run a FULL_RUN query to generate one.")
        else:
            report_obj = ResearchReport.model_validate(report_data)
            st.markdown(f"#### {report_obj.title}")
            
            original_query = mem_store.read("original_query") or st.session_state.session_id
            md = report_to_markdown(original_query, report_obj)
            safe_stem = (report_obj.title[:60].replace(" ", "_") or "research_report")
            
            st.download_button(
                "⬇ Download Markdown (.md)",
                data=md.encode("utf-8"),
                file_name=f"{safe_stem}.md",
                mime="text/markdown",
                use_container_width=True,
            )
            try:
                pdf_bytes = report_to_pdf_bytes(original_query, report_obj)
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
                st.code(json.dumps(report_data, indent=2), language="json")
