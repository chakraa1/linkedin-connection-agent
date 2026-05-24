# LinkedIn Connection Agent — CLAUDE.md

## Project Overview

A multi-agent CrewAI pipeline that discovers LinkedIn profiles matching an ICP, scrapes and analyses them, generates personalized connection request messages (≤300 chars), and sends them — with explicit human approval at every step before anything reaches LinkedIn.

**Requester profile:** https://www.linkedin.com/in/chakraa1/
**LinkedIn Developer App:** https://www.linkedin.com/developers/apps/249660102/

**Stack:** Python 3.11+, CrewAI, Anthropic Claude API, Playwright browser automation, SQLite, Click CLI, Rich terminal UI.

## Architecture

### 7-Phase Pipeline (`src/linkedin_connection_agent/crew.py`)

1. **Boolean Search Generation** — `boolean_search_agent` (Claude Sonnet) generates 6–8 LinkedIn Boolean search strings for the ICP: seniority × industry × technology keyword combinations.
2. **Profile Discovery** — Playwright searches LinkedIn with each string and saves unique profile URLs to SQLite.
3. **Profile Analysis** — `profile_analyzer_agent` extracts the top 3 conversation hooks from the scraped profile and downloaded PDF.
4. **Post Analysis** — `post_analyzer_agent` identifies the best conversation entry point from the target's 3 most recent posts.
5. **Message Generation** — `message_writer_agent` writes a ≤300-character connection note using the **Specific Observation → Insight/Tension → Curious Question** structure.
6. **Message Validation** — `message_validator_agent` + local `MessageValidator` enforce 8 rules (A–H). Auto-revises up to 2× before surfacing to human.
7. **Human Review** — Interactive Rich CLI. The user approves, edits, skips, or rejects each message.
8. **Send** — Playwright sends the connection request with the approved note only after `python main.py send` + confirmation prompt.

### Outreach Psychology

Messages must reframe the sender: **applicant → thoughtful builder**. Three triggers drive high acceptance:
- **Peer tone** — sounds like an intellectual equal, not a candidate
- **Specific observation** — references something concrete *from their* profile or post
- **Curiosity question** — about *them*, not about opportunities for the sender

See `config/outreach_config.yaml` for forbidden phrases and message structure examples.

### Key Files

| File | Role |
|------|------|
| `main.py` | Click CLI — all user commands |
| `src/linkedin_connection_agent/crew.py` | Main orchestrator, 7-phase pipeline |
| `src/linkedin_connection_agent/utils/message_validator.py` | 8-rule quality gate (A–H) |
| `src/linkedin_connection_agent/utils/scheduler.py` | SQLite state machine for profile lifecycle |
| `src/linkedin_connection_agent/tools/browser_tool.py` | Playwright browser automation |
| `src/linkedin_connection_agent/tools/pdf_tool.py` | LinkedIn profile PDF text extraction |
| `src/linkedin_connection_agent/tools/search_tool.py` | CrewAI tool for Boolean search generation |
| `src/linkedin_connection_agent/tools/linkedin_tool.py` | OAuth 2.0 + LinkedIn API v2 (identity only) |
| `src/linkedin_connection_agent/utils/llm_factory.py` | Multi-provider LLM factory (reads YAML) |
| `run_headless.py` | Non-interactive runner → JSON output |

### Config Files (`config/`)

| File | Purpose |
|------|---------|
| `agents.yaml` | 6 agent definitions (role, goal, backstory) |
| `tasks.yaml` | CrewAI task descriptions and expected outputs |
| `llm_config.yaml` | Provider list + per-agent model mapping |
| `icp_config.yaml` | ICP 1 definition — target roles, industries, keywords, daily limit |
| `outreach_config.yaml` | Forbidden phrases, message structure, quality rules |

### Profile Lifecycle (SQLite State Machine)

```
discovered → analyzed → message_drafted → approved → sent
                                        → rejected  (human rejected)
                                                   → failed  (send error)
```

`outputs/scheduler.db` — profile URLs are unique; the same person cannot enter twice.

## Common Commands

### Setup
```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
playwright install chromium

copy .env.example .env
# Edit .env with API keys and LinkedIn credentials

python main.py auth             # Browser login + OAuth token
```

### Run Pipeline
```bash
# Full pipeline in one shot
python main.py run --icp icp1 --limit 10

# Step by step
python main.py discover --icp icp1 --max-per-query 15
python main.py analyze --limit 10
python main.py generate-messages --limit 10
python main.py review
python main.py send --limit 20        # prompts for confirmation
```

### Inspect
```bash
python main.py list                   # All profiles
python main.py list --status approved
python main.py stats                  # Pipeline counts by status
```

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `ANTHROPIC_API_KEY` | Yes | All CrewAI agents |
| `OPENAI_API_KEY` | Optional | GPT-4o fallback |
| `LINKEDIN_EMAIL` | Yes | Playwright browser login |
| `LINKEDIN_PASSWORD` | Yes | Playwright browser login |
| `LINKEDIN_CLIENT_ID` | Yes | OAuth app (app 249660102) |
| `LINKEDIN_CLIENT_SECRET` | Yes | OAuth app secret |
| `LINKEDIN_ACCESS_TOKEN` | Auto | Set by `python main.py auth` |
| `LINKEDIN_PERSON_URN` | Auto | Set by `python main.py auth` |
| `PYTHONUTF8` | Windows | Set to `1` — fixes Rich/Click encoding |

## Message Validation Rules (A–H)

Implemented in `src/linkedin_connection_agent/utils/message_validator.py`:

| Rule | Name | Method |
|------|------|--------|
| A | CHARACTER_LIMIT | Deterministic — ≤300 chars, hard reject |
| B | NO_JOB_ASK | Deterministic — regex on forbidden job-seeking phrases |
| C | SPECIFIC_REFERENCE | LLM judge — must reference something specific |
| D | PEER_TONE | LLM judge — must sound like thoughtful peer |
| E | CURIOSITY_TRIGGER | LLM judge — must create "interesting perspective" reaction |
| F | NO_DESPERATION | Deterministic — regex on desperation/flattery phrases |
| G | NO_RESUME_SIGNAL | Deterministic — no sender skills/resume/experience |
| H | ENGAGEMENT_HOOK | LLM judge — must end with genuine question about recipient |

## ICP Definition (`config/icp_config.yaml`)

**ICP 1 — Peer Competitors & Senior Hiring Leaders**: VPs, Directors, Heads of Engineering, Engineering Managers, Principal/Staff Engineers in cloud and platform engineering, fintech, and banking. Target: 2nd-degree connections. Daily connection limit: 20.

## LinkedIn Rate Limits & Safety

- **Daily connection limit**: 20 (set in `icp_config.yaml` — conservative to avoid restrictions)
- **Random delays**: 2–4 seconds between all browser interactions
- **Session persistence**: `outputs/linkedin_session.json` (gitignored)
- **Headful browser only** for sending — LinkedIn detects headless Chromium more aggressively
- **2FA/CAPTCHA**: handled manually in the browser window during `python main.py auth`

## Why Browser Automation Instead of LinkedIn API

LinkedIn's standard developer API does not support:
- People search (`/v2/search` requires partnership access)
- Sending connection invitations (requires special partner approval)
- Reading other members' profiles or posts

Playwright browser automation handles these. The OAuth API (`linkedin_tool.py`) is used only for identity verification (userinfo endpoint).

## Output Structure

```
outputs/
├── scheduler.db                 # SQLite profile lifecycle DB
├── linkedin_session.json        # Playwright session cookies (gitignored)
├── linkedin_tokens.json         # OAuth tokens (gitignored)
└── profiles/
    └── pdfs/
        └── <profile-id>.pdf     # Downloaded LinkedIn profile PDFs
```

## Multi-Provider LLM Support

All assignments in `config/llm_config.yaml`. Override per-agent with env vars (e.g. `MESSAGE_WRITER_AGENT_MODEL=gpt-4o`).

Default mapping:
- `boolean_search_agent` → `anthropic/claude-sonnet-4-6`
- `profile_analyzer_agent` → `anthropic/claude-sonnet-4-6`
- `post_analyzer_agent` → `anthropic/claude-haiku-4-5-20251001`
- `message_writer_agent` → `anthropic/claude-sonnet-4-6`
- `message_validator_agent` → `anthropic/claude-haiku-4-5-20251001`

## Critical Design Constraints

- **No silent sending.** Connection requests never go out without `python main.py send` + an explicit confirmation prompt. The `connection_agent` only logs outcomes — it does not trigger sends.
- **Browser-first.** LinkedIn's API is identity-only. All LinkedIn interactions (search, scrape, send) go through Playwright.
- **Headful only for sends.** Never switch `headless=True` for connection sending — LinkedIn flags it.
- **Human review is mandatory.** The `review` command is always in the pipeline before `send`. Messages in `message_drafted` status are never sent directly.
- **Windows UTF-8.** Always keep `PYTHONUTF8=1` in `.env`.
