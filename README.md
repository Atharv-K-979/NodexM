# NodexM

**Research answers where every claim is checked, and alerts when a fact changes.**

![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![React](https://img.shields.io/badge/react-18-61DAFB?logo=react&logoColor=black)
![FastAPI](https://img.shields.io/badge/FastAPI-backend-009688?logo=fastapi&logoColor=white)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

NodexM is an open-source research assistant built from a team of AI agents. You ask a question; it plans
the research, searches the web, academic papers, regulators, news and official statistics in parallel,
reads every source in full, and checks each factual claim against the others before it writes anything.

Every claim comes back labelled:

| Label | Meaning |
|---|---|
| **Agreed** | At least two *independent* original sources state it |
| **Disputed** | Credible sources give conflicting figures, dates or conclusions — both sides are shown |
| **Single source** | Only one original source states it, even if several sites repeat it |

Each citation opens the exact sentence it came from. And with **Watches**, NodexM re-runs a topic on a
schedule and tells you only what changed: a fact that became disputed, a new fact, or one that disappeared.

---

## Why NodexM exists

AI search tools are fluent but often wrong about where their facts come from. Independent audits have found
AI search engines giving incorrect answers to more than 60% of news-citation queries
([Tow Center, 2025](https://www.cjr.org/tow_center/we-compared-eight-ai-search-engines-theyre-all-bad-at-citing-news.php)),
and significant problems — most often with sourcing — in 45% of AI assistants' news answers
([EBU/BBC, 2025](https://www.npr.org/sections/npr-extra/2025/10/21/g-s1-94424/global-study-on-news-integrity-in-ai-assistants-shows-need-for-safeguards-and-improved-accuracy)).

Most tools show a list of links and leave the checking to you. NodexM does the checking, shows its work,
and keeps watching.

## What makes it different

- **Counts independent sources, not sites.** Ten websites republishing one Reuters story count as *one*
  source. Wire datelines, word-for-word repeats, sources that cite each other, and sources crediting the
  same organisation are all grouped, so "agreed" really means independently confirmed.
- **Evidence you can click.** A claim is only kept if its quote is found word for word in the source.
  Click any citation to see that sentence highlighted in context, with the source's credibility score.
- **Looks for counter-evidence.** In Deep mode a devil's-advocate agent searches specifically for sources
  that contradict the findings, or that settle a dispute.
- **Checks its own citations.** A citation auditor tests every cited sentence — including every number —
  against the source it cites, and sends weak answers back for revision.
- **Watches that alert on confirmed changes.** A claim ledger remembers every fact a Watch has found.
  Alerts fire only when a change is confirmed by a new independent source or seen on two runs in a row,
  and each alert shows the quotes before and after.
- **Transparent and predictable.** Every step of the research is visible live, and every run reports its
  model calls, searches and estimated cost (typically about $0.03 for a Quick run and $0.14 for a Deep run).

## Features

**Research**
- Auto, **Quick** (about 8 sources, ~1 minute) and **Deep** (up to 24 sources, extra research rounds and a
  counter-evidence round, ~3–4 minutes) modes
- Specialist searchers: general web, academic (OpenAlex, arXiv, Semantic Scholar), regulatory (official
  domains by region, US Federal Register), news (incl. GDELT) and data (FRED, statistics agencies)
- Full-text reading of web pages and PDFs, with a 24-hour cache
- Region focus (Global, EU, US, UK, APAC) and writing depth (brief, analyst, technical)
- Threads with follow-up questions that understand the earlier answers

**Answers**
- A **Facts** view that leads with disputes (both sides' quotes side by side), then facts confirmed by
  independent sources, then facts resting on one original source
- A written answer where every factual sentence is cited, and disagreements are shown, never averaged
- Sources with type, date, credibility score and reasons, and which wire story a page republishes
- A key-figures table that lines up numbers by unit and period
- Edit, rewrite one section with an instruction, approve, and export to Markdown or HTML

**Watches**
- Daily, weekday, weekly, twice-monthly or monthly schedules
- A "what changed" feed: status changes, new facts, facts that are no longer found
- Before/after quotes and the reason each alert was confirmed
- *Real change* / *Noise* feedback on each alert, giving an alert-precision score per Watch
- Optional email alerts

**Accounts**
- Email and password sign-in with JWT
- Free runs per account (2 Quick + 1 Deep by default), a daily spending cap, and the option for users to
  add their own API keys (stored encrypted)

## How a question is answered

```
router → planner → specialist searchers → reader → credibility → extractor
                          ↑                                          │
                          └──── gap finder (Deep: up to 2 rounds) ◄──┘
→ verifier → devil's advocate (Deep: 1 round, then verify again)
→ quant analyst → writer ⇄ critic + citation auditor (up to 2 revisions) → answer
→ change detector (Watches only)
```

| Agent | What it does |
|---|---|
| Router | Picks Quick or Deep and turns follow-ups into complete questions |
| Planner | Splits the question into sub-questions and chooses which searchers to use |
| Specialist searchers | Search the web, academic papers, regulators, news and data sources in parallel |
| Reader | Reads full pages and PDFs, respecting robots.txt |
| Credibility scorer | Scores each source with reasons and flags copies and wire stories |
| Extractor | Pulls out claims, each with an exact quote that must exist in the source |
| Verifier | Groups claims by fact, decides agreed or disputed, and counts independent sources |
| Gap finder | Searches again where the evidence is thin |
| Devil's advocate | Searches for counter-evidence and for sources that settle disputes |
| Quant analyst | Normalises figures and flags where sources measure differently |
| Writer | Writes only from verified claims and cites every fact |
| Critic | Scores accuracy, completeness and clarity |
| Citation auditor | Checks every cited sentence and number against its source |
| Change detector | Compares a Watch run with the claim ledger and records confirmed changes |

Rule-based agents (credibility, citation audit, change detection, independence checks) run as plain code,
which keeps them fast, cheap and repeatable.

## Getting started

### Prerequisites

- Python 3.11 or newer
- Node.js 18 or newer
- A [Google Gemini API key](https://aistudio.google.com/apikey) (free tier available)
- Recommended: a [Tavily API key](https://app.tavily.com) for web search (1,000 free searches a month)

### Install and run

```bash
git clone https://github.com/Atharv-K-979/NodexM.git
cd NodexM

# Backend
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                   # then fill in your keys (see below)
uvicorn main:app --reload              # API on http://localhost:8000, docs at /docs

# Frontend, in a second terminal
cd frontend
npm install
npm run dev                            # open http://localhost:5173
```

Create an account in the app and ask a question.

To serve everything from one process, build the frontend once (`cd frontend && npm run build`); the API
then serves the app at http://localhost:8000.

### Configuration

All settings live in `.env`; `.env.example` lists every option with comments. The main ones:

| Setting | Needed? | What it does |
|---|---|---|
| `GEMINI_API_KEY` | Required | Powers every AI agent and claim matching |
| `NODEXM_JWT_SECRET` | Required in production | Signs login tokens (`python -c "import secrets; print(secrets.token_hex(32))"`) |
| `TAVILY_API_KEY` | Recommended | General web, news and regulator search |
| `ADMIN_EMAILS` | Optional | Accounts with unlimited runs (for example, your own) |
| `DATABASE_URL` | Optional | SQLite file by default; Postgres is supported |
| `JINA_API_KEY` | Optional | More reliable page reading |
| `RESEND_API_KEY` | Optional | Email alerts for Watches |
| `S2_API_KEY`, `FRED_API_KEY` | Optional | Extra academic and economic data |
| `FREE_QUICK_RUNS`, `FREE_DEEP_RUNS`, `DAILY_BUDGET_USD` | Optional | Free-run limits and the daily spending cap |

OpenAlex, arXiv, the US Federal Register and GDELT need no key. Gemini model names can be changed with
`GEMINI_LITE_MODEL`, `GEMINI_FLASH_MODEL` and `GEMINI_FALLBACK_MODELS`.

### Run the tests

```bash
pytest
```

The tests run offline with a scripted model and stubbed sources, so they need no API keys.

## Benchmarks

NodexM ships tools to measure itself, rather than asking you to take its accuracy on trust.

```bash
python -m bench.eval_syndication           # offline: how well it groups copies of the same source
python -m bench.run_benchmark --limit 3    # live, uses your keys: copy inflation, citation support, cost
```

- `bench/syndication_cases.json` — a small hand-labeled set of sources behind one claim, with the true
  independent groups, including cases the current method is known to miss.
- `bench/questions.json` — questions about widely reported facts, used to measure how often counting
  sites would overstate agreement.

The labeled set is small and was written by the same author as the method, so treat its scores as a sanity
check. Contributions of real-world labeled cases are very welcome.

## Project structure

```
main.py                  entry point (uvicorn main:app)
nodexm/
  config.py              settings from .env
  models.py, db.py       database tables and migrations
  auth.py, crypto.py     JWT auth and encryption of users' own keys
  llm.py                 model tiers, fallbacks, structured output and cost tracking
  agents.py              the language-model agents and their prompts
  search.py, reader.py   specialist searchers and the full-text reader
  credibility.py         source credibility scoring
  evidence.py            quote locating, copies, source independence, clustering, citation audit
  pipeline.py            the research graph (LangGraph)
  ledger.py              Watch claim ledger and change detection
  jobs.py                run queue, worker threads, scheduler and live events
  quota.py               free runs and the daily budget
  routes/                REST API
frontend/                React, Vite and Tailwind web app
bench/                   independence test set and the live benchmark
tests/                   offline test suite
```

## API overview

The REST API is documented interactively at `/docs` when the server runs. All endpoints except register,
login, health and config need an `Authorization: Bearer <token>` header.

| Endpoint | Purpose |
|---|---|
| `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me` | Accounts |
| `GET /api/threads`, `POST /api/threads` | List threads, ask a new question |
| `GET/PATCH/DELETE /api/threads/{id}`, `POST /api/threads/{id}/runs` | Open, rename, delete, follow up |
| `GET /api/runs/{id}`, `GET /api/runs/{id}/events` | Full answer, live progress (server-sent events) |
| `PATCH /api/runs/{id}/draft`, `POST .../rewrite-section`, `POST .../approve`, `GET .../export` | Review and export |
| `GET/POST /api/watches`, `GET/PATCH/DELETE /api/watches/{id}`, `POST /api/watches/{id}/run` | Watches |
| `GET /api/watches/changes`, `POST /api/watches/{id}/changes/{change_id}/feedback` | Recent changes and alert feedback |
| `GET/PUT /api/settings`, `PUT /api/settings/keys`, `GET /api/usage` | Settings, own keys and usage |

## Roadmap

- Slack and Microsoft Teams delivery for Watch alerts
- An exportable audit pack: claims, quotes, source snapshots and status history in one file
- A draft checker: upload a report and verify every citation in it
- Curated source packs for regulatory, health and competitive-intelligence research
- A larger public benchmark with independently labeled cases
- Shared team ledgers, and an API for verifying claims from other tools

## Contributing

Issues and pull requests are welcome. Please run `pytest` before submitting, and include a test with any
change to the verification logic. Labeled real-world examples of syndicated or independent sources are
especially useful for improving the benchmark.

## License

[MIT](LICENSE) © 2026 Atharv Kulkarni
