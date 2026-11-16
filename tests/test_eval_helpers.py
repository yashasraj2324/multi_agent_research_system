"""Offline tests for the pure helpers in evals/run_eval.py (no network, no keys)."""
import asyncio

from evals import run_eval
from research_system.core.schemas import ReportSection, ResearchReport, SourceCitation


def _report(body: str, n_sources: int = 2) -> ResearchReport:
    return ResearchReport(
        title="T",
        executive_summary="E",
        sections=[ReportSection(heading="H", body_markdown=body)],
        all_sources=[
            SourceCitation(id=str(i), title=f"S{i}", url=f"https://example.com/{i}")
            for i in range(1, n_sources + 1)
        ],
    )


def test_claim_keywords_drop_markers_stop_words_and_short_words():
    kw = run_eval.claim_keywords("The battery market reached 45.2 billion in 2024 [1].")
    assert {"battery", "market", "reached", "billion", "45.2", "2024"} <= kw
    assert "the" not in kw and "1" not in kw


def test_extract_claims_maps_sentences_to_cited_sources_and_ignores_bad_markers():
    report = _report("Solid state cells reach higher density [1]. Costs remain high [2][9]. Uncited sentence here.")
    claims = run_eval.extract_claims(report)
    assert set(claims) == {1, 2}  # [9] has no listed source; the uncited sentence is skipped
    assert any("density" in kw for kw in claims[1])
    assert run_eval.count_citation_markers(report) == 2


def test_supports_uses_keyword_share_threshold():
    kw = {"alpha", "bravo", "charlie", "delta"}
    assert run_eval.supports("alpha bravo", kw) is True       # 2/4 = 50%
    assert run_eval.supports("alpha only", kw) is False       # 1/4
    assert run_eval.supports("anything", set()) is False


def test_html_to_text_skips_scripts_and_styles():
    html = "<html><style>.x{}</style><script>var secret=1</script><p>Visible text</p></html>"
    text = run_eval.html_to_text(html)
    assert "Visible text" in text and "secret" not in text and ".x" not in text


def test_check_citations_requires_status_200_and_keyword_support(monkeypatch):
    report = _report("Solid state cells reach higher density [1]. Totally unrelated words appear [2].", 3)
    pages = {
        "https://example.com/1": {"status": 200, "text": "solid state cells reach higher density", "error": None},
        "https://example.com/2": {"status": 200, "text": "nothing relevant here", "error": None},
    }

    async def fake_fetch(client, url):
        return pages[url]

    monkeypatch.setattr(run_eval, "fetch_page", fake_fetch)
    out = asyncio.run(run_eval.check_citations(report))
    assert out["sources_cited"] == 2  # source 3 is listed but never cited
    assert out["url_200_share"] == 1.0
    assert out["url_resolve_share"] == 0.5


def test_check_citations_counts_404_as_neither_200_nor_resolving(monkeypatch):
    report = _report("Solid state cells reach higher density [1].", 1)

    async def fake_fetch(client, url):
        return {"status": 404, "text": "solid state cells reach higher density", "error": None}

    monkeypatch.setattr(run_eval, "fetch_page", fake_fetch)
    out = asyncio.run(run_eval.check_citations(report))
    assert out["url_200_share"] == 0.0 and out["url_resolve_share"] == 0.0


def test_check_citations_with_no_citations_reports_null_shares(monkeypatch):
    out = asyncio.run(run_eval.check_citations(_report("No markers here at all.")))
    assert out["sources_cited"] == 0 and out["url_resolve_share"] is None


def test_summary_stats_and_cost_not_measured_note():
    runs = [
        {"runtime_s": 10.0, "critique_score": 6, "citation_markers": 5, "sources_cited": 4,
         "url_200_share": 1.0, "url_resolve_share": 0.5, "usage": {"total_tokens": 100, "cost_usd": None}},
        {"runtime_s": 20.0, "critique_score": None, "citation_markers": 7, "sources_cited": 6,
         "url_200_share": 0.5, "url_resolve_share": 0.25, "usage": {"total_tokens": 300, "cost_usd": None}},
        {"error": "boom", "runtime_s": 1.0, "usage": {"total_tokens": 0, "cost_usd": None}},
    ]
    s = run_eval.build_summary(runs, requested=3)
    assert s["runs_succeeded"] == 2 and s["runs_failed"] == 1
    assert s["runtime_s"] == {"n": 2, "mean": 15.0, "min": 10.0, "max": 20.0}
    assert s["critique_score"]["n"] == 1  # null scores are excluded, not counted as 0
    assert s["cost_usd"]["n"] == 0 and "not measured" in s["cost_usd"]["note"]


def test_usage_meter_cost_only_when_prices_given():
    m = run_eval.UsageMeter()
    m.input_tokens, m.output_tokens, m.requests = 1_000_000, 500_000, 3
    assert m.snapshot(None, None)["cost_usd"] is None
    assert m.snapshot(2.0, 8.0)["cost_usd"] == 6.0


def test_main_exits_with_code_2_and_names_missing_vars(monkeypatch, capsys):
    monkeypatch.setattr(run_eval, "missing_env", lambda: ["TAVILY_API_KEY"])
    assert run_eval.main(["-n", "1"]) == 2
    assert "TAVILY_API_KEY" in capsys.readouterr().err


def test_usage_meter_counts_tokens_from_real_agent_runs():
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    agent = Agent(TestModel())
    with run_eval.UsageMeter() as meter:
        asyncio.run(agent.run("hello"))
        asyncio.run(agent.run("again"))
    assert meter.requests == 2
    assert meter.input_tokens > 0 and meter.output_tokens > 0
    # the patch is removed on exit
    assert Agent.run.__qualname__ == "AbstractAgent.run"


def test_usage_meter_reports_null_not_zero_when_nothing_was_measured():
    snap = run_eval.UsageMeter().snapshot(2.0, 8.0)
    assert snap["total_tokens"] is None and snap["cost_usd"] is None


# ---------------- unverified (403/429) and grounded checks ----------------

def test_403_and_429_are_unverified_and_excluded_from_resolve_denominator(monkeypatch):
    report = _report("Alpha words appear here [1]. Bravo words appear here [2]. Charlie words appear here [3]. Delta words appear here [4].", 4)
    pages = {
        "https://example.com/1": {"status": 200, "text": "alpha words appear here", "error": None},
        "https://example.com/2": {"status": 403, "text": "", "error": None},
        "https://example.com/3": {"status": 429, "text": "", "error": None},
        "https://example.com/4": {"status": 404, "text": "", "error": None},
    }

    async def fake_fetch(client, url):
        return pages[url]

    monkeypatch.setattr(run_eval, "fetch_page", fake_fetch)
    out = asyncio.run(run_eval.check_citations(report))
    assert out["sources_cited"] == 4
    assert out["url_unverified"] == 2 and out["url_failed"] == 1 and out["url_ok"] == 1
    assert out["resolve_n"] == 2                     # the 200 and the 404; 403/429 excluded
    assert out["url_resolve_share"] == 0.5
    assert [d["category"] for d in out["urls"]] == ["ok", "unverified", "unverified", "failed"]


def test_network_error_is_failed_not_unverified(monkeypatch):
    report = _report("Alpha words appear here [1].", 1)

    async def fake_fetch(client, url):
        return {"status": None, "text": "", "error": "ConnectError: boom"}

    monkeypatch.setattr(run_eval, "fetch_page", fake_fetch)
    out = asyncio.run(run_eval.check_citations(report))
    assert out["url_failed"] == 1 and out["url_unverified"] == 0 and out["resolve_n"] == 1


def test_grounded_uses_retrieved_text_and_excludes_urls_without_any(monkeypatch):
    report = _report(
        "Solid state cells reach higher density [1]. Unrelated zebra giraffe claim [2]. Another sourced statement here [3].", 3
    )

    async def fake_fetch(client, url):  # page contents are irrelevant to grounding
        return {"status": 403, "text": "", "error": None}

    monkeypatch.setattr(run_eval, "fetch_page", fake_fetch)
    retrieved = {
        "https://example.com/1": ["Solid state cells reach higher density in lab tests"],
        "https://example.com/2": ["completely different content about batteries"],
        # source 3 has no retrieved text
    }
    out = asyncio.run(run_eval.check_citations(report, retrieved))
    assert out["grounded_n"] == 2 and out["grounded"] == 1 and out["grounded_share"] == 0.5
    assert out["no_retrieved_text"] == 1
    assert [d["grounded"] for d in out["urls"]] == [True, False, None]


def test_grounded_matches_urls_after_normalisation(monkeypatch):
    report = ResearchReport(
        title="T", executive_summary="E",
        sections=[ReportSection(heading="H", body_markdown="Solid state cells reach higher density [1].")],
        all_sources=[SourceCitation(id="1", title="t", url="https://Example.com/a/")],
    )

    async def fake_fetch(client, url):
        return {"status": 200, "text": "", "error": None}

    monkeypatch.setattr(run_eval, "fetch_page", fake_fetch)
    retrieved = {run_eval.normalize_url("https://example.com/a#x"): ["solid state cells reach higher density"]}
    assert asyncio.run(run_eval.check_citations(report, retrieved))["grounded"] == 1


def test_classify_status():
    assert [run_eval.classify_status(s) for s in (200, 403, 429, 404, 500, None)] == [
        "ok", "unverified", "unverified", "failed", "failed", "failed",
    ]


def test_retrieval_capture_records_results_and_counts_search_errors(monkeypatch):
    from research_system.tools.search import TavilySearchTool

    async def fake_search(self, query, max_results=None):
        return [
            {"title": "Page", "url": "https://Example.com/p/", "content": "retrieved body text"},
            {"title": "search_error", "url": "", "content": "quota exceeded"},
        ]

    monkeypatch.setattr(TavilySearchTool, "search", fake_search)
    with run_eval.RetrievalCapture() as cap:
        asyncio.run(TavilySearchTool().search("q"))
    assert "retrieved body text" in cap.texts["https://example.com/p"][0]
    assert cap.search_errors == 1
    assert TavilySearchTool.search is fake_search  # patch removed on exit


def test_summary_pools_resolve_and_grounded_counts():
    base = {"runtime_s": 1.0, "usage": {"total_tokens": 1, "cost_usd": None}}
    runs = [
        {**base, "resolves": 3, "resolve_n": 4, "grounded": 1, "grounded_n": 2, "url_unverified": 2, "no_retrieved_text": 1,
         "url_resolve_share": 0.75, "grounded_share": 0.5, "search_errors": 0},
        {**base, "resolves": 1, "resolve_n": 4, "grounded": 2, "grounded_n": 2, "url_unverified": 0, "no_retrieved_text": 0,
         "url_resolve_share": 0.25, "grounded_share": 1.0, "search_errors": 1},
    ]
    s = run_eval.build_summary(runs, 2)
    assert s["pooled_resolve"] == {"numerator": 4, "denominator": 8, "share": 0.5}
    assert s["pooled_grounded"] == {"numerator": 3, "denominator": 4, "share": 0.75}
    assert s["pooled_unverified_urls"] == 2 and s["search_errors"] == 1


def test_split_resumed_keeps_only_successful_runs_in_scope():
    prior = [
        {"query": "a", "error": None},
        {"query": "b", "error": "ModelAPIError: Connection error."},
        {"query": "z", "error": None},  # not among the requested queries
    ]
    kept, todo = run_eval.split_resumed(prior, ["a", "b", "c"])
    assert list(kept) == ["a"] and todo == ["b", "c"]
