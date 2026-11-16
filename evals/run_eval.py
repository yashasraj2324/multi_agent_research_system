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
  * url_unverified       cited URLs that answered HTTP 403/429; the site refused our request, so they are
                         counted separately and excluded from the resolve share (not treated as failed)
  * url_200_share        share of the remaining cited URLs that answer HTTP 200 (after redirects)
  * url_resolve_share    share of the remaining cited URLs that "resolve" (definition below); resolve_n is
                         the denominator
  * grounded_share       share of cited sources whose already-retrieved text supports a claim citing them
                         (definition below); grounded_n is the denominator
  * usage                token counts read from pydantic-ai run results; cost only from --price-in/--price-out

Definition of "resolves": a cited source resolves when (1) a GET on its URL ends in HTTP 200 and (2) the
  fetched page text contains the keywords of a claim it is cited for. A "claim" is a sentence of the report
  body that carries the marker [n]. Its keywords are the lower-cased words of >= 4 letters that are not stop
  words, plus number tokens. The page supports the claim when at least KEYWORD_THRESHOLD (50%) of those
  keywords occur in the page text. A source resolves if it supports at least one sentence citing it.
  Limits: non-HTML pages (PDF etc.) cannot be text-matched and count as 200-but-unresolved; JavaScript-only
  pages can fail although the citation is legitimate; keyword overlap is a proxy for support, not proof.

Definition of "grounded": the same keyword test, but against the text the Search agents already retrieved
  during the run (Tavily result content and arXiv abstracts, captured as they are returned; no extra network
  calls) instead of a freshly fetched page. Sources whose URL has no retrieved text cannot be judged and are
  reported as no_retrieved_text, outside the grounded denominator. The agent only saw each Tavily result's
  first 1200 characters, so this checks the citation against what the pipeline actually had in hand.

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
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

QUERIES_PATH = ROOT / "evals" / "queries.json"
RESULTS_DIR = ROOT / "evals" / "results"
REQUIRED_ENV = ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "TAVILY_API_KEY")

KEYWORD_THRESHOLD = 0.5
UNVERIFIED_STATUSES = frozenset({403, 429})
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


def normalize_url(url: str) -> str:
    """Normalise a URL for matching: lower-case scheme/host, drop fragment and trailing slash."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or parts.path
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


class RetrievalCapture:
    """Records the source text the Search agents already retrieved (no extra network calls).

    Wraps TavilySearchTool.search and the arXiv tool used by the search agent, keeping
    url -> [text] for every result they return. `search_errors` counts Tavily calls that
    returned an error / not-configured stub instead of results.
    """

    def __init__(self) -> None:
        self.texts: dict[str, list[str]] = {}
        self.search_errors = 0
        self._restore: list = []

    def _add(self, url: str, text: str) -> None:
        key = normalize_url(url)
        if key and text:
            self.texts.setdefault(key, []).append(text)

    def __enter__(self):
        from research_system.agents import search as search_agent
        from research_system.tools.search import TavilySearchTool

        capture = self
        orig_tavily = TavilySearchTool.search
        orig_arxiv = search_agent.arxiv_search

        async def tavily_search(tool_self, *args, **kwargs):
            results = await orig_tavily(tool_self, *args, **kwargs)
            for r in results:
                if r.get("title") in ("search_error", "tavily_not_configured"):
                    capture.search_errors += 1
                else:
                    capture._add(r.get("url", ""), f"{r.get('title', '')} {r.get('content') or ''}")
            return results

        async def arxiv_search(*args, **kwargs):
            results = await orig_arxiv(*args, **kwargs)
            for r in results:
                text = f"{r.get('title', '')} {r.get('abstract') or ''}"
                capture._add(r.get("url", ""), text)
                capture._add(r.get("pdf_url", ""), text)
            return results

        TavilySearchTool.search = tavily_search
        search_agent.arxiv_search = arxiv_search
        self._restore = [(TavilySearchTool, "search", orig_tavily), (search_agent, "arxiv_search", orig_arxiv)]
        return self

    def __exit__(self, *exc):
        for obj, name, original in self._restore:
            setattr(obj, name, original)


def classify_status(status: int | None) -> str:
    """'ok' (HTTP 200), 'unverified' (403/429: the site refused a bot, so we learn nothing), else 'failed'."""
    if status == 200:
        return "ok"
    if status in UNVERIFIED_STATUSES:
        return "unverified"
    return "failed"


def is_grounded(retrieved_texts: list[str], claim_sets: list[set[str]]) -> bool | None:
    """Do the already-retrieved texts support at least one claim citing the source?

    Returns None when nothing was retrieved for the URL (cannot be judged).
    """
    if not retrieved_texts:
        return None
    joined = " ".join(retrieved_texts)
    return any(supports(joined, kw) for kw in claim_sets)


def _share(num: int, den: int) -> float | None:
    return (num / den) if den else None


async def check_citations(report, retrieved: dict[str, list[str]] | None = None) -> dict:
    """Fetch each cited source once and classify it. Returns metrics + per-URL detail.

    resolve_share  = resolves / (cited sources with a definite answer), i.e. excluding
                     "unverified" (HTTP 403/429); resolve_n is that denominator.
    grounded_share = grounded / (cited sources with retrieved text); grounded_n is that
                     denominator and no_retrieved_text counts the rest.
    """
    import httpx

    retrieved = retrieved or {}
    claims = extract_claims(report)
    cited = sorted(claims)
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=FETCH_TIMEOUT_S,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        pages = await asyncio.gather(*[fetch_page(client, report.all_sources[n - 1].url) for n in cited])

    details = []
    for n, page in zip(cited, pages):
        url = report.all_sources[n - 1].url
        category = classify_status(page["status"])
        resolves = category == "ok" and any(supports(page["text"], kw) for kw in claims[n])
        grounded = is_grounded(retrieved.get(normalize_url(url), []), claims[n])
        details.append(
            {
                "n": n,
                "url": url,
                "status": page["status"],
                "error": page["error"],
                "category": category,
                "resolves": resolves,
                "grounded": grounded,
                "claims_checked": len(claims[n]),
            }
        )

    counts = {c: sum(d["category"] == c for d in details) for c in ("ok", "unverified", "failed")}
    resolve_n = counts["ok"] + counts["failed"]
    judged = [d for d in details if d["grounded"] is not None]
    return {
        "sources_cited": len(details),
        "url_ok": counts["ok"],
        "url_unverified": counts["unverified"],
        "url_failed": counts["failed"],
        "url_200_share": _share(counts["ok"], resolve_n),
        "resolve_n": resolve_n,
        "resolves": sum(d["resolves"] for d in details),
        "url_resolve_share": _share(sum(d["resolves"] for d in details), resolve_n),
        "grounded_n": len(judged),
        "grounded": sum(d["grounded"] for d in judged),
        "grounded_share": _share(sum(d["grounded"] for d in judged), len(judged)),
        "no_retrieved_text": len(details) - len(judged),
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
        """Token counts as read from pydantic-ai run results; cost only from those real counts.

        If no LLM request was counted the usage was not measured, so tokens and cost are null
        (never 0, which would look like a measurement). Cost is also null unless both prices
        (USD per 1M tokens) were given; nothing is estimated.
        """
        if self.requests == 0:
            return {"input_tokens": None, "output_tokens": None, "total_tokens": None,
                    "llm_requests": 0, "cost_usd": None}
        cost = None
        if price_in is not None and price_out is not None:
            cost = self.input_tokens / 1e6 * price_in + self.output_tokens / 1e6 * price_out
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
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
    capture = RetrievalCapture()
    start = time.perf_counter()
    try:
        with meter, capture:
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
    record["search_errors"] = capture.search_errors
    checks = await check_citations(report, capture.texts)
    record.update({k: v for k, v in checks.items() if k != "urls"})
    record["url_checks"] = checks["urls"]
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


def pooled(runs: list[dict], num_key: str, den_key: str) -> dict:
    """Counts summed over all runs (every cited source weighs the same, whatever its run)."""
    num = sum(r.get(num_key) or 0 for r in runs)
    den = sum(r.get(den_key) or 0 for r in runs)
    return {"numerator": num, "denominator": den, "share": _share(num, den)}


def build_summary(runs: list[dict], requested: int) -> dict:
    ok = [r for r in runs if not r.get("error")]
    metrics = {
        "runtime_s": [r["runtime_s"] for r in ok],
        "critique_score": [r.get("critique_score") for r in ok],
        "citation_markers": [r.get("citation_markers") for r in ok],
        "sources_cited": [r.get("sources_cited") for r in ok],
        "url_unverified": [r.get("url_unverified") for r in ok],
        "url_200_share": [r.get("url_200_share") for r in ok],
        "url_resolve_share": [r.get("url_resolve_share") for r in ok],
        "grounded_share": [r.get("grounded_share") for r in ok],
        "total_tokens": [r["usage"]["total_tokens"] for r in ok],
        "cost_usd": [r["usage"]["cost_usd"] for r in ok],
    }
    summary = {name: summarise(vals) for name, vals in metrics.items()}
    summary["pooled_resolve"] = pooled(ok, "resolves", "resolve_n")
    summary["pooled_grounded"] = pooled(ok, "grounded", "grounded_n")
    summary["pooled_unverified_urls"] = sum(r.get("url_unverified") or 0 for r in ok)
    summary["pooled_no_retrieved_text"] = sum(r.get("no_retrieved_text") or 0 for r in ok)
    summary["search_errors"] = sum(r.get("search_errors") or 0 for r in runs)
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
            f"cited={rec['sources_cited']}, resolve={rec['url_resolve_share']} (n={rec['resolve_n']}, "
            f"unverified={rec['url_unverified']}), grounded={rec['grounded_share']} (n={rec['grounded_n']}), "
            f"search_errors={rec['search_errors']}"
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
