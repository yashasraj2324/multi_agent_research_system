"""Run the research pipeline on a set of queries and record quality / cost metrics.

Usage (from the repo root):
    uv run python evals/run_eval.py                 # 10 queries from evals/queries.json
    uv run python evals/run_eval.py -n 3            # first 3 queries only
    uv run python evals/run_eval.py -n 3 --deep     # deep research mode

Per run it records:
  * runtime_s            wall-clock seconds for ResearchOrchestrator.run()
  * critique_score       Critique agent's overall_score (0-10); null if the critique failed
  * citation_markers     number of inline [n] markers in the report body that point at a listed source
  * sources_cited        number of distinct listed sources that are cited at least once
  * url_200_share        share of cited sources whose URL answers HTTP 200 (after redirects)
  * url_resolve_share    share of cited sources that "resolve" (definition below)
  * tokens / cost        token usage measured from pydantic-ai run results; cost only if prices are given

Definition of "resolves" (deliberately simple and mechanical):
  A cited source resolves when (1) a GET on its URL ends in HTTP 200 and (2) the page text contains
  the keywords of the claim it is cited for. A "claim" is a sentence of the report body that carries
  the marker [n]. Its keywords are the lower-cased words of >= 4 letters that are not stop words, plus
  any number tokens. The page supports the claim when at least KEYWORD_THRESHOLD (50%) of those
  keywords occur in the page text. A source resolves if it supports at least one sentence citing it.
  Limits: non-HTML pages (PDF etc.) cannot be text-matched and so count as 200-but-unresolved; pages
  that need JavaScript or block bots can fail even when the citation is legitimate; keyword overlap is
  a proxy for support, not proof of it.

Cost: the code does not know model prices. Cost is reported only when --price-in / --price-out
(USD per 1M tokens) are passed; otherwise it is null.

Required configuration (env vars or .env): AZURE_OPENAI_ENDPOINT,
AZURE_OPENAI_API_KEY and TAVILY_API_KEY. Without them the script exits before spending anything.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

QUERIES_PATH = ROOT / "evals" / "queries.json"
RESULTS_DIR = ROOT / "evals" / "results"
REQUIRED_ENV = ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "TAVILY_API_KEY")

KEYWORD_THRESHOLD = 0.5
FETCH_TIMEOUT_S = 15.0
MAX_PAGE_BYTES = 2_000_000
USER_AGENT = "Mozilla/5.0 (compatible; research-eval/1.0)"

STOP_WORDS = frozenset(
    """about above after again against also because been before being below between both could does doing
    down during each from further have having here hers herself himself into itself just more most
    other ours ourselves over same should some such than that their theirs them themselves then there
    these they this those through under until very were what when where which while whom will with
    would your yours yourself yourselves according however including among within without""".split()
)

CITATION_RE = re.compile(r"\[(\d+)\]")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


# --------------------------- claim / citation extraction --------------------------- #

def claim_keywords(sentence: str) -> set[str]:
    """Keywords of a claim: words of >= 4 letters minus stop words, plus number tokens."""
    text = CITATION_RE.sub(" ", sentence).lower()
    words = {w for w in re.findall(r"[a-z]{4,}", text) if w not in STOP_WORDS}
    numbers = set(re.findall(r"\d[\d,.]*\d|\d", text))
    return words | {n.strip(".,") for n in numbers}


def extract_claims(report) -> dict[int, list[set[str]]]:
    """Map source number n (1-based, in report.all_sources) -> keyword sets of sentences citing it."""
    n_sources = len(report.all_sources)
    claims: dict[int, list[set[str]]] = {}
    for section in report.sections:
        for sentence in SENTENCE_SPLIT_RE.split(section.body_markdown):
            nums = {int(m) for m in CITATION_RE.findall(sentence)}
            keywords = claim_keywords(sentence)
            if not keywords:
                continue
            for n in nums:
                if 1 <= n <= n_sources:
                    claims.setdefault(n, []).append(keywords)
    return claims


def count_citation_markers(report) -> int:
    n_sources = len(report.all_sources)
    return sum(
        1
        for section in report.sections
        for m in CITATION_RE.findall(section.body_markdown)
        if 1 <= int(m) <= n_sources
    )


def supports(page_text: str, keywords: set[str], threshold: float = KEYWORD_THRESHOLD) -> bool:
    if not keywords:
        return False
    haystack = page_text.lower()
    hits = sum(1 for k in keywords if k in haystack)
    return hits / len(keywords) >= threshold


# --------------------------------- URL checking ---------------------------------- #

class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - malformed HTML: use what was parsed
        pass
    return " ".join(parser.parts)


async def fetch_page(client, url: str) -> dict:
    """GET a URL; return {status, text, error}. `text` is empty for non-HTML content."""
    try:
        resp = await client.get(url)
    except Exception as exc:  # noqa: BLE001
        return {"status": None, "text": "", "error": f"{type(exc).__name__}: {exc}"}
    content_type = resp.headers.get("content-type", "").lower()
    text = ""
    if "html" in content_type or not content_type:
        text = html_to_text(resp.text[:MAX_PAGE_BYTES])
    elif content_type.startswith("text/"):
        text = resp.text[:MAX_PAGE_BYTES]
    return {"status": resp.status_code, "text": text, "error": None}


async def check_citations(report) -> dict:
    """Fetch each cited source once and decide 200 / resolves. Returns metrics + per-URL detail."""
    import httpx

    claims = extract_claims(report)
    cited = sorted(claims)
    details = []
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=FETCH_TIMEOUT_S,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        pages = await asyncio.gather(*[fetch_page(client, report.all_sources[n - 1].url) for n in cited])

    for n, page in zip(cited, pages):
        ok_200 = page["status"] == 200
        resolves = ok_200 and any(supports(page["text"], kw) for kw in claims[n])
        details.append(
            {
                "n": n,
                "url": report.all_sources[n - 1].url,
                "status": page["status"],
                "error": page["error"],
                "is_200": ok_200,
                "resolves": resolves,
                "claims_checked": len(claims[n]),
            }
        )

    total = len(details)
    return {
        "sources_cited": total,
        "url_200_share": (sum(d["is_200"] for d in details) / total) if total else None,
        "url_resolve_share": (sum(d["resolves"] for d in details) / total) if total else None,
        "urls": details,
    }


# ------------------------------- usage accounting -------------------------------- #

class UsageMeter:
    """Accumulates token usage from every pydantic-ai Agent.run() while active."""

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.requests = 0
        self._original = None

    def __enter__(self):
        from pydantic_ai import Agent

        self._original = Agent.run
        meter = self
        original = self._original

        async def run(agent_self, *args, **kwargs):
            result = await original(agent_self, *args, **kwargs)
            try:
                u = result.usage() if callable(result.usage) else result.usage  # property in pydantic-ai 2.x
                meter.input_tokens += u.input_tokens or 0
                meter.output_tokens += u.output_tokens or 0
                meter.requests += u.requests or 0
            except Exception:  # noqa: BLE001 - never break a run over accounting
                pass
            return result

        Agent.run = run
        return self

    def __exit__(self, *exc):
        from pydantic_ai import Agent

        Agent.run = self._original

    def snapshot(self, price_in: float | None, price_out: float | None) -> dict:
        total = self.input_tokens + self.output_tokens
        cost = None
        if price_in is not None and price_out is not None:
            cost = self.input_tokens / 1e6 * price_in + self.output_tokens / 1e6 * price_out
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": total,
            "llm_requests": self.requests,
            "cost_usd": cost,
        }


# ----------------------------------- running ------------------------------------ #

def missing_env() -> list[str]:
    """Names of required settings that are empty after the environment and .env files are loaded."""
    from research_system.core.config import config

    values = {
        "AZURE_OPENAI_ENDPOINT": config.AZURE_OPENAI_ENDPOINT,
        "AZURE_OPENAI_API_KEY": config.AZURE_OPENAI_API_KEY,
        "TAVILY_API_KEY": config.TAVILY_API_KEY,
    }
    return [name for name in REQUIRED_ENV if not values[name]]


async def run_one(query: str, args) -> dict:
    from research_system.orchestrator import ResearchOrchestrator

    # Skip the optional Chroma indexing step: it is unrelated to the metrics and writes ./chroma_db.
    ResearchOrchestrator._index_research_memory = lambda *a, **k: None

    record: dict = {"query": query, "mode": "deep" if args.deep else "standard", "error": None}
    orch = ResearchOrchestrator(deep=args.deep)
    meter = UsageMeter()
    start = time.perf_counter()
    try:
        with meter:
            plan, findings, report, critique = await asyncio.wait_for(orch.run(query), timeout=args.timeout)
    except Exception as exc:  # noqa: BLE001
        record["runtime_s"] = round(time.perf_counter() - start, 2)
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["usage"] = meter.snapshot(args.price_in, args.price_out)
        return record
    record["runtime_s"] = round(time.perf_counter() - start, 2)
    record["usage"] = meter.snapshot(args.price_in, args.price_out)

    record["critique_score"] = critique.overall_score if critique else None
    record["citation_markers"] = count_citation_markers(report)
    record["sources_listed"] = len(report.all_sources)
    record["subtasks"] = len(plan.subtasks)
    checks = await check_citations(report)
    record.update(
        sources_cited=checks["sources_cited"],
        url_200_share=checks["url_200_share"],
        url_resolve_share=checks["url_resolve_share"],
        url_checks=checks["urls"],
    )
    record["report"] = report.model_dump(mode="json")
    record["critique"] = critique.model_dump(mode="json") if critique else None
    return record


def summarise(values: list) -> dict:
    nums = [v for v in values if v is not None]
    if not nums:
        return {"n": 0, "mean": None, "min": None, "max": None}
    return {
        "n": len(nums),
        "mean": round(statistics.fmean(nums), 4),
        "min": min(nums),
        "max": max(nums),
    }


def build_summary(runs: list[dict], requested: int) -> dict:
    ok = [r for r in runs if not r.get("error")]
    metrics = {
        "runtime_s": [r["runtime_s"] for r in ok],
        "critique_score": [r.get("critique_score") for r in ok],
        "citation_markers": [r.get("citation_markers") for r in ok],
        "sources_cited": [r.get("sources_cited") for r in ok],
        "url_200_share": [r.get("url_200_share") for r in ok],
        "url_resolve_share": [r.get("url_resolve_share") for r in ok],
        "total_tokens": [r["usage"]["total_tokens"] for r in ok],
        "cost_usd": [r["usage"]["cost_usd"] for r in ok],
    }
    summary = {name: summarise(vals) for name, vals in metrics.items()}
    summary["runs_requested"] = requested
    summary["runs_succeeded"] = len(ok)
    summary["runs_failed"] = len(runs) - len(ok)
    if summary["cost_usd"]["n"] == 0:
        summary["cost_usd"]["note"] = "not measured: pass --price-in and --price-out (USD per 1M tokens)"
    return summary


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("-n", type=int, default=10, help="number of queries to run (default 10)")
    p.add_argument("--queries", type=Path, default=QUERIES_PATH)
    p.add_argument("--deep", action="store_true", help="run in Deep Research mode")
    p.add_argument("--timeout", type=float, default=900.0, help="per-run timeout in seconds")
    p.add_argument("--price-in", type=float, default=None, help="USD per 1M input tokens")
    p.add_argument("--price-out", type=float, default=None, help="USD per 1M output tokens")
    return p.parse_args(argv)


async def main_async(args) -> int:
    queries = json.loads(args.queries.read_text(encoding="utf-8"))[: args.n]
    if not queries:
        print("No queries to run.", file=sys.stderr)
        return 2

    missing = missing_env()
    if missing:
        print("Missing required configuration (set as env vars or in .env):", file=sys.stderr)
        for name in missing:
            print(f"  - {name}", file=sys.stderr)
        return 2

    from research_system.core.config import config

    runs = []
    for i, query in enumerate(queries, 1):
        print(f"[{i}/{len(queries)}] {query}", flush=True)
        rec = await run_one(query, args)
        runs.append(rec)
        status = f"ERROR {rec['error']}" if rec["error"] else (
            f"{rec['runtime_s']}s, critique={rec['critique_score']}, "
            f"cited={rec['sources_cited']}, resolve={rec['url_resolve_share']}"
        )
        print(f"    -> {status}", flush=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = {
        "timestamp": stamp,
        "config": {
            "n": len(queries),
            "mode": "deep" if args.deep else "standard",
            "deployment": config.AZURE_OPENAI_DEPLOYMENT_NAME,
            "keyword_threshold": KEYWORD_THRESHOLD,
            "price_in_per_1m": args.price_in,
            "price_out_per_1m": args.price_out,
        },
        "summary": build_summary(runs, len(queries)),
        "runs": runs,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{stamp}.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved {path.relative_to(ROOT)}")
    print(json.dumps(out["summary"], indent=2))
    return 0 if out["summary"]["runs_succeeded"] else 1


def main(argv=None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
