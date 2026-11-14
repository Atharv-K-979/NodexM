# NodexM

Multi-agent research where every claim is checked.

NodexM answers research questions the way an analyst would. A team of agents plans the question, searches
official, academic, news and data sources, reads them in full, and checks every factual claim against the
other sources. Each claim is labelled **agreed**, **disputed** or **single source** with a confidence score,
and every citation opens the exact sentence it came from. **Watches** re-run a topic on a schedule and tell
you when a fact changes instead of sending a whole new report.

## Quick start

Requirements: Python 3.11+ and Node 18+.

```bash
# 1. Backend
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # then add GEMINI_API_KEY (and ideally TAVILY_API_KEY)
uvicorn main:app --reload            # http://localhost:8000, API docs at /docs

# 2. Frontend (second terminal)
cd frontend
npm install
npm run dev                          # http://localhost:5173
```

Create an account in the app and ask a question. To run everything from one process, build the frontend
(`cd frontend && npm run build`); `uvicorn main:app` then serves it at http://localhost:8000.

Run the tests (offline; they use a scripted LLM): `pytest`

## What you need

| Key | Needed? | Free allowance | Used for |
|---|---|---|---|
| `GEMINI_API_KEY` | Required | Free tier with daily limits | All agents and claim embeddings |
| `NODEXM_JWT_SECRET` | Required in production | You generate it | Signing login tokens |
| `TAVILY_API_KEY` | Recommended | 1,000 searches a month | General web search |
| `JINA_API_KEY` | Optional | Free key | Faster page reading |
| `RESEND_API_KEY` | Optional | 3,000 emails a month | Watch change alerts |
| `GOOGLE_CLIENT_ID` | Optional | Free | "Continue with Google" sign-in |
| `S2_API_KEY`, `FRED_API_KEY` | Optional | Free | More academic and economic data |

OpenAlex, arXiv, the Federal Register and GDELT need no key. See `.env.example` for every setting.

## Google sign-in (optional, free)

"Continue with Google" uses Google Identity Services. It is free at any number of users and needs only a
Client ID, no client secret and no billing account.

1. Open https://console.cloud.google.com and create a project (or pick an existing one).
2. Go to **Google Auth Platform** (also listed as **APIs & Services → OAuth consent screen**) and set it up:
   app name, your support email, audience **External**. Only the basic `email`, `profile` and `openid`
   scopes are used, so no verification review is needed.
3. Under **Audience**, either add yourself and your testers as **Test users**, or click **Publish app** so
   anyone with a Google account can sign in.
4. Go to **Clients** (or **Credentials → Create credentials → OAuth client ID**), choose **Web application**,
   and add these **Authorized JavaScript origins**:
   `http://localhost:5173`, `http://localhost:8000` and `http://localhost`
   (plus your real domain when you deploy). No redirect URI is needed.
5. Copy the Client ID (it ends in `.apps.googleusercontent.com`) into `.env` as `GOOGLE_CLIENT_ID=` and
   restart the server.

If someone already has a NodexM account with the same email, signing in with Google links the two, so both
methods work. Accounts created with Google have no password.

## How a question is answered

```
router → planner → specialist searchers → reader → credibility → extractor → clusterer → verifier
                         ↑                                                                  │
                         └──── gap finder (≤2 rounds) · devil's advocate (1 round) ◄────────┘
→ quant analyst → writer ⇄ critic + citation auditor (≤2 revisions) → finalize → change detector (Watches)
```

| Agent | What it does | How |
|---|---|---|
| Router | Picks Quick or Deep, rewrites follow-ups as standalone questions | Lite model |
| Planner | Splits the question into sub-questions and assigns searchers | Lite model |
| Specialist searchers | Web, academic, regulatory, news and data search in parallel | Tavily/SearXNG, OpenAlex, arXiv, Semantic Scholar, Federal Register, official domains, GDELT, FRED |
| Reader | Reads full pages and PDFs, respects robots.txt | Jina Reader or trafilatura + pypdf |
| Credibility scorer | Scores each source and sets aside copies of other sources | Rules (code) |
| Extractor | Pulls claims with an exact quote; quotes not found in the page are dropped | Lite model + quote locator |
| Claim clusterer | Groups claims that state the same fact | Embeddings, or word vectors as fallback |
| Verifier | Labels each group agreed or disputed, with stances per source | Flash model |
| Gap finder | Searches again where evidence is thin | Lite model |
| Devil's advocate | Looks for counter-evidence and for sources that settle disputes | Lite model |
| Quant analyst | Normalises figures and flags where sources measure differently | Flash model |
| Writer | Writes only from verified claims, cites every fact, shows both sides of disputes | Flash model (Lite in Quick mode) |
| Critic | Scores accuracy, completeness and clarity | Lite model |
| Citation auditor | Checks each cited sentence against the source it cites | Code |
| Change detector | Compares a Watch run with the claim ledger | Code |

Quick mode reads about 8 sources in one pass. Deep mode reads up to 24, with gap-filling and a
counter-evidence round. Both have hard limits on searches, sources and revisions, so cost stays predictable.

## Free runs and costs

Each account gets `FREE_QUICK_RUNS` + `FREE_DEEP_RUNS` runs on the server's keys (2 + 1 by default). Failed
or stopped runs don't count. Users can add their own Gemini key in Settings to keep going at no cost to you.
`DAILY_BUDGET_USD` pauses free runs if the day's estimated spend reaches the limit. Every run records its
tokens, searches and estimated cost (see the Steps tab).

## Project layout

```
main.py                  entry point (uvicorn main:app)
nodexm/
  config.py              settings from .env
  models.py, db.py       database tables (SQLite by default, Postgres supported)
  auth.py, crypto.py     JWT auth, encryption of users' own keys
  llm.py                 Gemini client: model tiers, fallbacks, structured output, cost tracking
  agents.py              LLM agents and their prompts
  search.py, reader.py   specialist searchers and full-text reader (24h cache in cache.py)
  credibility.py         source scoring
  evidence.py            quote locating, duplicates, clustering, confidence, citation audit
  pipeline.py            the LangGraph research graph
  ledger.py              Watch claim ledger and change detection
  jobs.py                run queue, worker threads, Watch scheduler, live events
  quota.py               free-run allowance and daily budget
  routes/                REST API: auth, settings, threads, runs (incl. SSE), watches
frontend/                React + Vite + Tailwind app
tests/                   offline tests for the evidence engine, pipeline, jobs and API
```

## API overview

All endpoints except register, login, health and config need `Authorization: Bearer <token>`.

| Method and path | Purpose |
|---|---|
| `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me` | Accounts |
| `GET /api/threads`, `POST /api/threads` | List threads, ask a new question |
| `GET/PATCH/DELETE /api/threads/{id}`, `POST /api/threads/{id}/runs` | Open, rename, delete, follow up |
| `GET /api/runs/{id}`, `GET /api/runs/{id}/events` | Full answer, live trace (server-sent events) |
| `PATCH /api/runs/{id}/draft`, `POST .../rewrite-section`, `POST .../approve`, `GET .../export` | Review |
| `GET/POST /api/watches`, `GET/PATCH/DELETE /api/watches/{id}`, `POST /api/watches/{id}/run` | Watches |
| `GET/PUT /api/settings`, `PUT /api/settings/keys`, `GET /api/usage` | Settings and usage |
