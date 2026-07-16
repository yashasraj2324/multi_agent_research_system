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
  - **Citation Agent**: Performs a final audit pass to ensure all claims are accurately attributed to their sources.
  - **Critique Agent**: Evaluates the report for biases, missing angles, and rigor.
- **Azure OpenAI Integration**: Fully localized to use enterprise-grade Azure OpenAI models.

## Setup & Installation

1. **Clone the repository and install dependencies**:
   Ensure you have Python 3.10+ installed.
   ```bash
   pip install -r backend/requirements.txt
   ```

2. **Environment Variables**:
   Create a `.env` file in the `backend/` directory with the following variables:
   ```env
   # Azure OpenAI Configuration
   AZURE_OPENAI_ENDPOINT="https://<your-resource-name>.openai.azure.com"
   AZURE_OPENAI_API_KEY="your-api-key"
   AZURE_OPENAI_DEPLOYMENT="your-deployment-name" # e.g. gpt-4o
   AZURE_OPENAI_API_VERSION="2024-02-01"

   # Tavily Search API
   TAVILY_API_KEY="your-tavily-api-key"

   # Database (Optional for memory persistence)
   MONGO_URL="mongodb://localhost:27017"
   DB_NAME="research_agents"
   ```

## Usage

Start the Streamlit application:
```bash
streamlit run streamlit_app.py
```

1. Enter your research query in the UI.
2. Toggle **Deep Research Mode** if the topic is complex and requires iterative gap-filling.
3. Watch the live agent trace as subagents plan, search, review, and write your report.
4. The final report will be displayed with proper markdown formatting and inline citations.

## Project Structure

- `streamlit_app.py`: The frontend UI and live agent trace viewer.
- `research_system/orchestrator.py`: The core orchestrator managing the multi-agent pipeline.
- `research_system/agents/`: Directory containing all the specialized agent logic (Planner, Search, Outliner, SectionWriter, etc.).
- `research_system/core/`: Central configurations, schemas, and LLM integrations.
- `docs/`: Product Requirements and architectural documentation.

## Architecture Highlights

This project mirrors Anthropic's "Orchestrator-Worker" pattern. An overarching orchestrator manages state (via a MongoDB/local memory store) and delegates tasks to specialized workers. The transition from a single-shot generation pattern to the **Map-Reduce Distributed Writing** pattern uniquely positions this system to generate exceptionally detailed long-form content.

## Screenshots

**1. The Subagents View**  
Displays the Research Console during a Deep Research task. Shows the system spanning multiple parallel search subagents to gather evidence, along with their live progress, finding counts, and confidence scores.
![The Subagents View](screenshoots/Screenshot%202026-07-16%20192001.png)

**2. The Critique View**  
Highlights the post-writing Critique Agent. After generating the massive report, the system audits itself—identifying weaknesses, missing angles, and delivering an honest academic rigor score out of 10.
![The Critique View](screenshoots/Screenshot%202026-07-16%20192028.png)

**3. The Trace View**  
Provides a live, behind-the-scenes look into the orchestrator's brain. Shows the Planner Agent decomposing a complex query into targeted subtasks, followed by the parallel spawning of search agents assigned to specific objectives.
![The Trace View](screenshoots/Screenshot%202026-07-16%20192044.png)

**4. The Export View**  
Once the multi-agent map-reduce architecture finishes assembling the sections, users can easily extract the final product as a polished Markdown file, a PDF, or raw JSON data.
![The Export View](screenshoots/Screenshot%202026-07-16%20192057.png)
