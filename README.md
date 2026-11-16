# Deep Research System

A highly autonomous, multi-agent AI research platform designed to execute deep, iterative research loops and generate comprehensive, richly detailed reports. 

Built using `pydantic-ai` and Streamlit, this system orchestrates a team of specialized AI agents that collaboratively plan, search, synthesize, outline, and concurrently write multi-section reports with robust inline citations.

## Features

- **Map-Reduce Writing Architecture**: To avoid token limits and "shallow" text generation, the system generates a structural outline and spawns a fleet of parallel `SectionWriter` agents. Each agent focuses purely on a single section, producing incredibly detailed and extensive reports.
- **Deep Research Mode**: Enables an iterative verification loop where a `LeadReviewer` agent assesses initial findings for gaps and spawns additional gap-filler search subagents until the topic is thoroughly covered.
- **Multi-Agent Orchestration**:
  - **Planner**: Decomposes the user's query into focused subtasks.
  - **Search Agents**: Execute parallel web searches (via Tavily) to gather source-backed facts.
  - **Summarizer**: Synthesizes the raw data into a digestible brief.
  - **Outliner**: Structures the final report.
  - **Section Writers**: Write distinct report sections concurrently.
  - **Citation Agent**: In Deep Research Mode, performs a final audit pass to ensure all claims are accurately attributed to their sources.
  - **Critique Agent**: Evaluates the report for biases, missing angles, and rigor.
- **Azure OpenAI Integration**: Fully localized to use enterprise-grade Azure OpenAI models.

## Setup & Installation

Requirements: Python 3.10-3.13 (`.python-version` pins 3.11) and [uv](https://docs.astral.sh/uv/). Dependencies are declared in `pyproject.toml` and locked in `uv.lock`.

1. **Clone and install dependencies**:
   ```bash
   git clone https://github.com/yashasraj2324/multi_agent_research_system.git
   cd multi_agent_research_system
   uv sync
   ```
   Without `uv sync`: `pip install .` does **not** work (the project has no build configuration). Export the lockfile instead: `uv export --no-hashes --no-dev --no-emit-project -o requirements.txt`, then in a virtualenv `pip install -r requirements.txt`.

2. **Environment variables**:
   Copy `.env.example` to `.env` in the repository root and fill it in. `research_system/core/config.py` reads `<repo>/.env` first and falls back to `research_system/.env` for any key the root file lacks. Values in these files override variables already set in your shell. The variables:
   ```env
   # Required: Azure OpenAI
   AZURE_OPENAI_ENDPOINT="https://<your-resource-name>.openai.azure.com"
   AZURE_OPENAI_API_KEY="your-api-key"
   AZURE_OPENAI_DEPLOYMENT="your-deployment-name"   # or AZURE_OPENAI_DEPLOYMENT_NAME; default gpt-4o
   AZURE_OPENAI_API_VERSION="2024-02-01"            # default

   # Required for web search (without it search returns a "not configured" stub)
   TAVILY_API_KEY="your-tavily-api-key"

   # Optional: session memory (falls back to in-process memory if MongoDB is unreachable)
   MONGO_URL="mongodb://localhost:27017"
   DB_NAME="research_agents"

   # Optional: Chroma Cloud for semantic memory (falls back to a local ./chroma_db)
   CHROMA_API_KEY=""
   CHROMA_TENANT=""
   CHROMA_DATABASE="default"

   # Optional: runtime tuning
   MAX_SUBAGENTS="4"                  # cap on planner subtasks in standard mode
   MAX_SEARCH_RESULTS_PER_AGENT="5"

   # Optional: observability (Logfire / OpenTelemetry; defaults in research_system/observabilty/config.py)
   SEND_TO_LOGFIRE="false"            # default false: no Logfire setup needed. true needs LOGFIRE_TOKEN
   LOGFIRE_TOKEN=""                   # only used when SEND_TO_LOGFIRE=true
   CONSOLE_ENABLED="true"
   OTLP_ENDPOINT=""                   # custom OTel collector, e.g. http://localhost:4318
   SERVICE_NAME="multi-agent-research"
   ```
   Logfire is **off by default**, so the app starts with no Logfire account or terminal (including in Docker). To send traces to Logfire Cloud, set `SEND_TO_LOGFIRE=true` and provide `LOGFIRE_TOKEN` (or run `uv run logfire auth` locally).

   `LLM_PROVIDER`, `LEAD_MODEL` and `HISTORY_DB_PATH` are read in `config.py`, but only Azure OpenAI is implemented and the other two are not used elsewhere in the code.

## Usage

Run the Streamlit app locally (served on http://localhost:3000, set in `.streamlit/config.toml`):
```bash
uv run streamlit run streamlit_app.py
```

Run with Docker (the image is built from `dockerfile`; `.env` files are excluded from the image, so pass the variables at run time):
```bash
docker build -t deep-research -f dockerfile .
docker run --rm -p 3000:3000 --env-file .env deep-research
```
`--env-file` does not strip quotes, so in the file you pass to Docker write values without quotation marks (e.g. `TAVILY_API_KEY=abc`). Then open http://localhost:3000. Inside a container `localhost:27017` is not your host's MongoDB, so session memory uses the in-process fallback unless you set `MONGO_URL`.

1. Enter your research query in the UI.
2. Toggle **Deep Research Mode** if the topic is complex and requires iterative gap-filling.
3. Watch the live agent trace as subagents plan, search, review, and write your report.
4. The final report will be displayed with proper markdown formatting and inline citations.
5. Follow-up chat messages are routed by an intent router (answer from saved context, run a targeted gap-filling search, consolidate a report, or start a full run).

## Project Structure

- `streamlit_app.py`: The frontend UI and live agent trace viewer.
- `research_system/orchestrator.py`: The core orchestrator managing the multi-agent pipeline.
- `research_system/agents/`: Directory containing all the specialized agent logic (Planner, Search, Outliner, SectionWriter, etc.).
- `research_system/core/`: Central configuration, schemas, and the Azure OpenAI model factory.
- `research_system/db/`: Session memory (MongoDB with in-process fallback), scratchpad, and the Chroma vector store.
- `research_system/observabilty/`: Logfire / OpenTelemetry setup (the directory name is spelled this way in the code).
- `dockerfile`, `otel-collector-config.yaml`: container image and an optional OTel collector config.
- `tests/`: offline pytest suite. `evals/`: evaluation script, queries and saved results.

## Tests

The suite runs fully offline (LLM, Tavily and MongoDB are mocked; no API keys needed) and is also run by GitHub Actions on every push and pull request:
```bash
uv sync
uv run pytest
```

## Evaluation

`evals/run_eval.py` runs the pipeline on queries from `evals/queries.json` and records, per run: wall-clock time, the Critique agent's score (0-10), the number of citations, two citation-quality shares and token usage.

- **Resolves** (live check): each cited URL is fetched once. It *resolves* when it returns HTTP 200 and its page text contains at least 50% of the keywords (words of 4+ letters minus stop words, plus numbers) of a report sentence that cites it. URLs answering HTTP 403/429 are **unverified** (the site refused our request, so nothing is learned); they are counted separately and excluded from the share. The denominator is the cited URLs with a definite answer (`resolve_n`).
- **Grounded** (no extra requests): the same keyword test, run against the text the Search agents already retrieved during the run (Tavily result content and arXiv abstracts, captured as returned). Cited sources with no retrieved text cannot be judged and are excluded (`no_retrieved_text`); the denominator is `grounded_n`.
- Both are keyword-overlap proxies for support, not proof. PDFs and JavaScript-only pages cannot be text-matched, so they count as 200-but-unresolved.
- **Tokens** are read from the pydantic-ai run results. Cost is computed only from those counts and prices you pass with `--price-in` / `--price-out` (USD per 1M tokens); it is never estimated.

```bash
uv run python evals/run_eval.py -n 3                       # first 3 queries (needs Azure OpenAI + Tavily keys)
uv run python evals/run_eval.py                            # all 10 queries
uv run python evals/run_eval.py --price-in 1.1 --price-out 4.4   # also compute cost (your own prices)
uv run python evals/run_eval.py --resume evals/results/<file>.json   # rerun only failed queries
```
Each run writes `evals/results/<timestamp>.json` with per-run output and a summary (mean, min, max, n).

**Results** (`evals/results/20261116T131208Z.json`; standard mode, 10 queries, Azure deployment `o4-mini`; 10 of 10 succeeded, no search errors). Runs 5-10 were rerun with `--resume` after a transient Azure connection error failed them in the first attempt, so runs 1-4 come from that earlier attempt:

| Metric | Value | n |
|---|---|---|
| Run time | mean 364 s (min 262, max 451) | 10 reports |
| Critique score | mean 6.6 / 10 (min 6, max 7) | 10 reports |
| Cited sources per report | mean 16.4 (min 7, max 27) | 10 reports |
| Resolve share (excl. unverified) | 61.3% pooled (76 / 124); per-report mean 59.6% (min 18.2%, max 100%) | 124 cited URLs |
| Unverified URLs (HTTP 403/429), excluded above | 40 | 164 cited URLs |
| Grounded share | 71.5% pooled (108 / 151); per-report mean 75.3% (min 48.0%, max 100%) | 151 cited sources |
| Cited sources with no retrieved text, excluded above | 13 | 164 cited sources |
| Tokens per report | mean 305,746 (min 187,559, max 507,955); 2,560,473 input + 496,991 output in total | 10 reports |
| Cost | not measured (no prices were passed) | - |

This is a single run per query, and the citation checks are keyword-based (no LLM judge), so treat the shares as a rough signal rather than a benchmark.

## Demo

Video walkthrough: _link to be added_

Example output: a report generated by the system for the query "Summarize the current state of humanoid robotics in 2026, highlighting the top three companies leading the market and their primary technical bottlenecks" ([PDF](output/sample_report.pdf), [Markdown](output/sample_report.md)).

## Architecture Highlights

This project mirrors Anthropic's "Orchestrator-Worker" pattern. An overarching orchestrator manages state (via a MongoDB/local memory store) and delegates tasks to specialized workers. The transition from a single-shot generation pattern to the **Map-Reduce Distributed Writing** pattern uniquely positions this system to generate exceptionally detailed long-form content.

## Screenshots

**1. The Subagents View**  
Displays the Research Console during a Deep Research task. Shows the system spanning multiple parallel search subagents to gather evidence, along with their live progress, finding counts, and confidence scores.
![The Subagents View](screenshots/Screenshot%202026-07-16%20192001.png)

**2. The Critique View**  
Highlights the post-writing Critique Agent. After generating the massive report, the system audits itself—identifying weaknesses, missing angles, and delivering an honest academic rigor score out of 10.
![The Critique View](screenshots/Screenshot%202026-07-16%20192028.png)

**3. The Trace View**  
Provides a live, behind-the-scenes look into the orchestrator's brain. Shows the Planner Agent decomposing a complex query into targeted subtasks, followed by the parallel spawning of search agents assigned to specific objectives.
![The Trace View](screenshots/Screenshot%202026-07-16%20192044.png)

**4. The Export View**  
Once the multi-agent map-reduce architecture finishes assembling the sections, users can easily extract the final product as a polished Markdown file, a PDF, or raw JSON data.
![The Export View](screenshots/Screenshot%202026-07-16%20192057.png)
