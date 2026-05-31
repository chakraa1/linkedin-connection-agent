# LinkedIn Connection Agent

A fully reusable pipeline that discovers senior LinkedIn profiles matching any persona, writes short personalised outreach messages, and lets you review everything in Excel before a single connection request is sent.

**One command to configure it for any user** — content marketer, startup founder, engineering director, recruiter, job seeker. Swap `config/persona.yaml` and the entire pipeline adapts: search queries, message tone, sender identity, and outreach goal all change automatically.

---

## What it does

| Step | Action |
|------|--------|
| **Configure** | Fill in `config/persona.yaml` (or run `python main.py init-persona`) with your name, role, goal, and target audience |
| **Discover** | Generates Boolean search queries from your persona via Claude, **auto-tests each query live against LinkedIn** (drops any that return zero results), then scrapes top-matching profiles |
| **Generate** | Writes an 80–150 word personalised message per profile in your voice for your goal, enforces 8 quality rules (A–I), auto-revises up to 2× |
| **Review** | Exports a 4-column Excel — you mark Shortlisted = Yes / No, edit messages if needed |
| **Send** | Sends only approved requests via Playwright, always with explicit confirmation |

Nothing is sent automatically. Sending requires `python main.py send` or `--send` flag with a confirmation prompt.

---

## ICP Segments (search_strings.yaml)

Five targeting segments — 9 curated Boolean queries with NO junior tech keyword noise:

| Segment | Who | Example titles |
|---------|-----|---------------|
| **1 — C-Suite Leaders** | CTO, CDO, MD, EVP, Group Head in banking/fintech | CTO, Chief Technology Officer, Managing Director |
| **2 — Elite MBA + Tech** | ISB, IIM-A/B/C, INSEAD, Wharton, Harvard grads in senior tech roles | Head of Engineering, CIO, EVP Technology |
| **3 — Top Consulting** | McKinsey, BCG, Bain, Deloitte, EY, KPMG, PwC technology leaders | Partner, Managing Director, Principal |
| **4 — Thought Leaders** | LinkedIn Top Voice badge holders + Keynote Speakers / Authors | CTO, Head of Engineering, Managing Director |
| **5 — Tier-1 Banks** | Engineering heads at Goldman, Morgan Stanley, JPMorgan, HSBC, Barclays | Head of Engineering, Engineering Director |

Keywords excluded from all queries: DevOps, SRE, GCP, Azure, Software Engineer, Lead Engineer.

---

## Tech stack

- **Python 3.11+**
- **CrewAI** — Boolean search string generation only (skipped when `search_strings.yaml` exists)
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

## Configure your persona (required for first use)

The entire pipeline — search queries, message tone, sender identity, outreach framing — is driven by a single file: **`config/persona.yaml`**.

### Option A — Interactive prompt (recommended for new users)

```bash
python main.py init-persona
```

Answer 7 questions and the file is created automatically:

```
Your full name: Jane Smith
Your current role / title: Head of Content Strategy
Your company: NarrativeAI (Stealth)
Your background: 10 years building B2B SaaS content programs from 0-to-1 at three startups
Your LinkedIn URL: https://www.linkedin.com/in/janesmith/
Outreach goal [PEER_COLLABORATION/CLIENT_ACQUISITION/TALENT_ACQUISITION/JOB_HUNTING/INVESTOR_OUTREACH]: CLIENT_ACQUISITION
Target location [India]: United States

→ Persona saved to config/persona.yaml
```

Then open `config/persona.yaml` and fill in your target audience:

```yaml
target:
  roles:
    - "Founder"
    - "Co-Founder"
    - "CEO"
    - "Head of Marketing"
  industries:
    - "SaaS"
    - "Artificial Intelligence"
    - "B2B Technology"
  keywords:
    - "stealth startup"
    - "early stage"
    - "go-to-market"
    - "Series A"
```

### Option B — Edit `config/persona.yaml` directly

Below is a complete example for a **senior engineering leader** targeting peer collaboration:

```yaml
sender:
  name: "Alex Johnson"
  role: "VP Engineering"
  company: "FinTech Corp"
  background: >
    15 years in banking technology and cloud infrastructure. Currently leading
    a 120-person engineering org focused on platform modernisation, cloud migration,
    and regulatory compliance engineering at a Tier-1 investment bank.
  linkedin_url: "https://www.linkedin.com/in/alexjohnson/"

goal: "PEER_COLLABORATION"
# Options: PEER_COLLABORATION | CLIENT_ACQUISITION | TALENT_ACQUISITION
#          JOB_HUNTING | INVESTOR_OUTREACH

goal_description: >
  Building a warm peer network of senior engineering leaders navigating the same
  platform modernisation and infrastructure scaling challenges. Messages should
  show genuine intellectual curiosity — no job seeking, no sales pitch.

outreach_tone: "peer"
# Options: peer | consultant | job_seeker | recruiter

message_context: >
  Alex brings perspective from regulated banking: cloud-native migration under
  compliance constraints, platform reliability at investment-bank scale, and
  engineering culture in a governed environment. Messages should surface a
  specific tension relevant to the recipient's domain.

target:
  description: >
    Senior technology leaders — CTOs, MDs, Heads of Engineering — in banking,
    fintech, and technology consulting.
  roles:
    - "CTO"
    - "Chief Technology Officer"
    - "Managing Director"
    - "Head of Engineering"
    - "VP of Engineering"
  industries:
    - "Financial Services"
    - "Banking"
    - "Fintech"
    - "Technology"
  keywords:
    - "ISB"
    - "IIM"
    - "McKinsey"
    - "platform engineering"
    - "cloud architecture"
  locations:
    - "India"
  connection_degree: "2nd"
  daily_connection_limit: 20

segments:
  - name: "Peer Competitors"
    description: "Same-level leaders navigating similar challenges"
    priority: 2
  - name: "Senior Leaders Ahead"
    description: "Leaders 1-2 steps ahead on the CTO path"
    priority: 3
  - name: "Hiring Decision Makers"
    description: "Engineering heads at target organisations"
    priority: 2
```

### Outreach goal reference

| Goal | Tone | Message framing |
|------|------|----------------|
| `PEER_COLLABORATION` | peer | Intellectual curiosity, knowledge exchange, no agenda |
| `CLIENT_ACQUISITION` | consultant | Curious about their challenge first, no pitch |
| `TALENT_ACQUISITION` | recruiter | Respectful, specific about why you reached out |
| `JOB_HUNTING` | job_seeker | Genuine interest in their work, never ask for a job |
| `INVESTOR_OUTREACH` | peer | Share a perspective relevant to their thesis |

### After setting your persona

```bash
# Delete search_strings.yaml if switching persona (forces regeneration + auto-test)
del config\search_strings.yaml

# Discover profiles — generates queries, tests them live, keeps only working ones
python main.py discover --fresh
```

---

## Run the pipeline

```bash
# Full pipeline — one command (discover → generate → Excel review → [send])
python main.py run-pipeline --icp icp1 --discover-limit 5

# Step by step
python main.py discover --icp icp1 --max-per-query 5    # find top-match profiles
python main.py generate-messages --limit 20              # write personalised messages
# Open outputs/YYYY-MM-DD/profiles_review_*.xlsx, set Shortlisted = Yes / No
python main.py import-review                             # sync decisions to DB
python main.py send                                      # send approved (confirms first)

# Start completely fresh
python main.py discover --icp icp1 --max-per-query 5 --fresh
# or
python main.py reset --all    # also wipes PDFs and old Excel files
```

### Key flags

| Flag | Default | Description |
|------|---------|-------------|
| `--icp` | `icp1` | ICP key from `config/icp_config.yaml` |
| `--max-per-query` | `10` | Max top-match profiles kept per search query (fetches 2× from LinkedIn, keeps STRONG fits first) |
| `--location` | India | Override region — e.g. `--location "United Kingdom"` |
| `--fresh` | off | Wipe DB before discovering — clean slate |
| `--send` / `--no-send` | no-send | Auto-send approved requests after Excel review |
| `--all` | off | With `reset` — also deletes PDFs and old Excel files |

---

## How discovery works

`discover` runs in two phases per search string:

**Phase 1 — Search (LinkedIn):** Fetches `2 × max-per-query` candidates from LinkedIn (capped at 15) so there are enough to filter.

**Phase 2 — Scrape + Score:** Visits each profile, extracts the real headline (LinkedIn removed stable CSS classes — the agent parses the page body text instead), scores against ICP criteria (STRONG / WEAK / MISMATCH). MISMATCH profiles are dropped. STRONG profiles are saved first; WEAK fill remaining slots up to `max-per-query × number of queries`.

**Seniority gate:** Headlines are checked against an include/exclude keyword list with word-boundary matching. A profile titled "Director of Software Engineering" correctly passes (not excluded despite "software engineer" being in the exclusion list).

---

## Configuration files

| File | Purpose |
|------|---------|
| `config/icp_config.yaml` | ICP definition — target roles, industries, keywords (ISB/IIM/consulting firms), locations, daily limit |
| `config/search_strings.yaml` | 9 curated Boolean queries across 5 segments — source of truth for search; delete to regenerate from ICP |
| `config/agents.yaml` | CrewAI agent definition for Boolean search generation |
| `config/tasks.yaml` | CrewAI task prompt for Boolean search generation |

`search_strings.yaml` takes priority. Delete it to regenerate from `icp_config.yaml`. Seed it from a reviewed Excel:

```bash
python main.py create-search-config "path/to/stage1_search_strings.xlsx"
```

---

## Message generation

Messages are personalised using:
- The profile's **LinkedIn URL** (writer knows it visited this exact profile)
- **Headline + About + Experience** scraped from the profile page
- **AI analysis** of conversation hooks and post data (from the `analyze` step when run)

**Post hallucination guard:** If the `analyze` step found no public posts, the message writer is explicitly told "recipient has no public posts — do not reference any posts." It cannot generate a statement "based on their posts" when none exist.

**Tone:** Collaborative curiosity — eager to understand their hardest engineering problems and brainstorm solutions, not transactional networking.

---

## Message quality rules (A–I)

Every generated message is evaluated by a single cached Claude Sonnet call. Pre-computed facts (word count, sentence length, detected phrases) are passed as ground truth.

| Rule | Type | Check |
|------|------|-------|
| A | LLM | Average sentence ≤ 15 words; no GPT filler phrases |
| B | LLM | 200–300 words (hard reject outside range) |
| C | LLM | Opening line is specific — cannot be sent unchanged to 100 other people |
| D | LLM | Contains a non-obvious, practitioner-level insight |
| E | LLM | No unhedged first-person claims ("I built", "we launched") |
| F | LLM | No em-dashes (—), en-dashes (–), or double hyphens |
| G | LLM | 3–5 distinct conversation hooks for senior leadership audience |
| I | LLM | Ends with a genuine question or clear CTA |

Failed rules trigger automatic revision using Claude Haiku (up to 2×). Remaining issues are flagged in the terminal for manual review.

---

## Excel output

The review file (`outputs/YYYY-MM-DD/profiles_review_*.xlsx`) has four columns:

| Column | Description |
|--------|-------------|
| Name | Profile display name |
| LinkedIn URL | Clickable hyperlink to the profile |
| Generated Message | 200–300 word personalised outreach message |
| Shortlisted (Yes / No) | **You fill this** — drives the import-review command |

After filling the Excel:

```bash
python main.py import-review    # syncs Yes/No decisions to the DB
python main.py send             # sends approved (confirmation required)
```

---

## Senior profile filter

Profiles pass through two seniority checks:

1. **Search-time (Phase 1):** Headline from search card is checked. Empty headlines are allowed through — scraped headline is checked in Phase 2.
2. **Scrape-time (Phase 2):** Real headline scraped from the profile page using body-text parsing (CSS class–independent, works despite LinkedIn's frequent DOM changes). Word-boundary matching prevents false exclusions (e.g., "Director of Software Engineering" is not excluded by the "software engineer" exclude keyword).

**Include list:** Director and above, Senior Manager, VP/SVP/EVP, CTO/CIO/CDO/CXO, Head of X, Managing Director, Partner, Principal, Associate Partner, Founder.

**Exclude list:** Junior, Associate, Software Engineer, Software Developer, Business/Data Analyst, Project/Program/Product Manager, Intern, Trainee.

After seniority, a second **ICP fit check** scores STRONG / WEAK / MISMATCH against the ICP config. MISMATCH profiles are dropped. STRONG matches (explicit role match, 2+ ICP keywords, or industry + keyword) are saved first.

---

## Useful commands

```bash
python main.py list                        # all profiles in pipeline
python main.py list --status approved      # approved profiles only
python main.py stats                       # counts by pipeline status
python main.py export                      # re-export Excel at any time
python main.py import-review               # sync Excel decisions to DB
python main.py reset                       # wipe DB and run ID
python main.py reset --all                 # also wipe PDFs and old Excel files
python main.py discover --fresh            # wipe DB then start discovering
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
- **No credentials in repo**: `outputs/` is fully gitignored (scraped data, session cookies, DB)
