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
    m.input_tokens, m.output_tokens = 1_000_000, 500_000
    assert m.snapshot(None, None)["cost_usd"] is None
    assert m.snapshot(2.0, 8.0)["cost_usd"] == 6.0


def test_main_exits_with_code_2_and_names_missing_vars(monkeypatch, capsys):
    monkeypatch.setattr(run_eval, "missing_env", lambda: ["TAVILY_API_KEY"])
    assert run_eval.main(["-n", "1"]) == 2
    assert "TAVILY_API_KEY" in capsys.readouterr().err
