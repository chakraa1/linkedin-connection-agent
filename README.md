# LinkedIn Connection Agent

A streamlined pipeline that discovers senior LinkedIn profiles matching your ICP, writes personalised 250–300 word outreach messages, and lets you review everything in Excel before a single connection request is sent.

---

## What it does

| Step | Action |
|------|--------|
| **Discover** | Runs curated Boolean searches on LinkedIn via Playwright, filters by seniority and ICP fit, scrapes name / headline / about / experience |
| **Generate** | Writes a 250–300 word personalised message per profile using a cached Claude Sonnet prompt, enforces 8 quality rules (A–I), auto-revises up to 2× |
| **Review** | Exports a 4-column Excel — you mark Shortlisted = Yes / No, edit messages if needed |
| **Send** | Sends only approved requests via Playwright, always with explicit confirmation |

Nothing is sent automatically. Sending requires `python main.py send` or `--send` flag with a confirmation prompt.

---

## Tech stack

- **Python 3.11+**
- **CrewAI** — Boolean search string generation only
- **Anthropic Claude API** — message writing + 8-rule quality gate (cached Sonnet prompts)
- **Playwright** — LinkedIn browser automation (search, scrape, send)
- **SQLite** via SQLAlchemy — profile lifecycle state machine
- **Click + Rich** — CLI and terminal UI
- **openpyxl** — Excel export

---

## Setup

```bash
# 1. Clone and install
git clone https://github.com/chakraa1/linkedin-connection-agent.git
cd linkedin-connection-agent
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
playwright install chromium

# 2. Configure environment
copy .env.example .env
# Edit .env — add ANTHROPIC_API_KEY, LINKEDIN_EMAIL, LINKEDIN_PASSWORD,
# LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET

# 3. Authenticate with LinkedIn
python main.py auth             # opens browser for login + OAuth token exchange
```

---

## Run the pipeline

```bash
# Full pipeline — one command
python main.py run-pipeline --icp icp1 --discover-limit 15

# Step by step
python main.py discover --icp icp1 --max-per-query 15
python main.py generate-messages --limit 20
python main.py send                                    # only after reviewing Excel
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--icp` | `icp1` | ICP key from `config/icp_config.yaml` |
| `--discover-limit` | `15` | Max profiles scraped per search query |
| `--message-limit` | `20` | Max messages generated per run |
| `--location` | India | Override region — e.g. `--location "United Kingdom"` |
| `--send` / `--no-send` | no-send | Auto-send approved requests after review |

---

## Configuration files

| File | Purpose |
|------|---------|
| `config/icp_config.yaml` | ICP definition — target roles, industries, keywords, locations, daily limit |
| `config/search_strings.yaml` | Curated Boolean search queries (auto-generated from ICP on first run, edit to refine) |
| `config/agents.yaml` | CrewAI agent definition for Boolean search generation |
| `config/tasks.yaml` | CrewAI task prompt for Boolean search generation |

`search_strings.yaml` is the source of truth for search queries. Delete it to regenerate from `icp_config.yaml`. Seed it from a reviewed Excel with:

```bash
python main.py create-search-config "path/to/stage1_search_strings.xlsx"
```

---

## Message quality rules (A–I)

Every generated message is evaluated by a single cached Claude Sonnet call. Pre-computed facts (word count, sentence length, detected phrases) are passed as ground truth.

| Rule | Type | Check |
|------|------|-------|
| A | LLM | Average sentence ≤ 15 words; no GPT filler phrases |
| B | LLM | 200–300 words (hard reject outside range) |
| C | LLM | Opening line is specific and counterintuitive — not generic |
| D | LLM | Contains a non-obvious, practitioner-level insight |
| E | LLM | No unhedged first-person claims ("I built", "we launched") |
| F | LLM | No em-dashes (—), en-dashes (–), or double hyphens |
| G | LLM | 3–5 distinct conversation hooks for senior leadership audience |
| I | LLM | Ends with a genuine question or clear CTA |

Failed rules trigger automatic revision (up to 2×). Remaining issues are flagged in the terminal for manual review.

---

## Excel output

The review file (`outputs/YYYY-MM-DD/profiles_review_*.xlsx`) has four columns:

| Column | Description |
|--------|-------------|
| Name | Profile display name |
| LinkedIn URL | Clickable hyperlink to the profile |
| Generated Message | 250–300 word personalised outreach message |
| Shortlisted (Yes / No) | **You fill this** — drives the import-review command |

After filling the Excel, run:

```bash
python main.py import-review    # syncs Yes/No decisions to the DB
```

---

## Senior profile filter

Discovery filters profiles by title **before** scraping, using two layers:

1. **Exclusion list** — rejects: Associate, Software Engineer, Project Manager, Program Manager, Business/Data Analyst, Intern
2. **Inclusion list** — accepts: Director and above, Senior Manager, General Manager, VP/SVP/EVP, CTO/CIO/CXO, Head of X, Managing Director, Partner, Founder

After scraping, a second **ICP fit check** reads the headline + About section and scores STRONG / WEAK / MISMATCH against the ICP's target roles, industries, and keywords. MISMATCH profiles are skipped before saving to DB.

---

## Useful commands

```bash
python main.py list                        # all profiles in pipeline
python main.py list --status approved      # approved profiles only
python main.py stats                       # counts by pipeline status
python main.py export                      # re-export Excel at any time
python main.py import-review               # sync Excel decisions to DB
python main.py reset                       # wipe DB and start fresh
```

---

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API — message writing and validation |
| `LINKEDIN_EMAIL` | Yes | Browser login |
| `LINKEDIN_PASSWORD` | Yes | Browser login |
| `LINKEDIN_CLIENT_ID` | Yes | LinkedIn developer app OAuth |
| `LINKEDIN_CLIENT_SECRET` | Yes | LinkedIn developer app OAuth |
| `LINKEDIN_ACCESS_TOKEN` | Auto | Set by `python main.py auth` |
| `LINKEDIN_PERSON_URN` | Auto | Set by `python main.py auth` |
| `PYTHONUTF8` | Windows | Set to `1` — fixes Rich/Click encoding on Windows |
| `OPENAI_API_KEY` | Optional | GPT-4o fallback (not used by default) |

---

## Safety notes

- **Daily limit**: 20 connections (set in `icp_config.yaml`) — conservative to avoid LinkedIn restrictions
- **Random delays**: 2–4 seconds between all Playwright interactions
- **Headful browser only**: never switch to headless for sending — LinkedIn detects it
- **Human review is mandatory**: no messages are sent without the `send` command + confirmation
- **No credentials in repo**: `outputs/` is fully gitignored (contains scraped data, session cookies, DB)
