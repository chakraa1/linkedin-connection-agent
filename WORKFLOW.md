# Workflow — LinkedIn Connection Agent

End-to-end guide for running the pipeline from a clean state to sent connection requests.

---

## Prerequisites

- Python 3.11+, Playwright Chromium installed
- `.env` file configured (see README)
- `python main.py auth` completed — LinkedIn session and OAuth token saved

---

## Step 0 — Configure your ICP (first time only)

Edit `config/icp_config.yaml` to match who you want to reach:

```yaml
icp1:
  target_roles:
    - "Head of Engineering"
    - "VP of Engineering"
    - "Director of Engineering"
    - "CTO"
  industries:
    - "Banking"
    - "Fintech"
    - "Technology"
  locations:
    - "India"           # default region
  keywords:
    - "AWS"
    - "platform engineering"
    - "Kubernetes"
  daily_connection_limit: 20
```

The `locations` list sets the default region. Override per run with `--location "United Kingdom"`.

---

## Step 1 — Discover profiles

```bash
python main.py discover --icp icp1 --max-per-query 15
```

**What happens:**

1. Checks `config/search_strings.yaml` for curated Boolean queries
   - If found → loads directly (zero LLM cost)
   - If not found → generates 6–8 queries via Claude using `icp_config.yaml` data, auto-saves to `search_strings.yaml` for future runs
2. Playwright opens Chrome and logs into LinkedIn
3. Runs each Boolean search query, collects profile URLs
4. Filters by title: rejects juniors (Associate, Engineer, PM, Analyst), accepts Director+ / VP / CTO / MD / Head of X
5. For each senior profile: scrapes headline, About, and experience
6. Runs ICP fit check against target roles, industries, and keywords:
   - **STRONG** — explicit role match or 2+ tech keywords → saved to DB
   - **WEAK** — domain word in headline only → saved to DB
   - **MISMATCH** — no ICP signals → skipped
7. Exports initial Excel snapshot

**Skipped profiles are logged** — you see counts for junior, mismatch, and already-in-pipeline.

**Output:** Profiles with status `discovered` in `outputs/scheduler.db`

---

## Step 2 — Generate messages

```bash
python main.py generate-messages --limit 20
```

**What happens:**

1. Reads scraped profile data (name, headline, about, experience) from DB
2. For each profile, sends a single cached Claude Sonnet API call with:
   - System prompt: 1,100-token writing instructions (cached — paid once per 5-minute window)
   - User turn: scraped profile data
3. Receives a 250–300 word personalised message
4. Runs 8-rule quality check (single cached Sonnet call):
   - Pre-computes facts (word count, avg sentence length, detected filler phrases, achievement claims, em-dashes)
   - LLM evaluates all 8 rules in one shot using pre-computed facts as ground truth
5. If any rule fails → auto-revises (up to 2 attempts)
6. Saves message to DB with status `message_drafted`
7. Exports 4-column Excel: **Name | LinkedIn URL | Message | Shortlisted**

**Console output per profile:**
```
  Writing for: Rahul Sharma
    Quality issues: C, G — revising...
    Revision 1 passed all rules.
    263 words
```

**Output:** Excel at `outputs/YYYY-MM-DD/profiles_review_*.xlsx`

---

## Step 3 — Review Excel

Open the Excel file (it opens automatically in `run-pipeline`).

**Columns:**
- **Name** — profile display name
- **LinkedIn URL** — clickable link to the profile
- **Generated Message** — 250–300 word outreach message, ready to use
- **Shortlisted (Yes / No)** — fill this column

**Actions:**
- Set `Yes` to approve a profile for sending
- Set `No` to reject (saved to DB as rejected)
- Edit the message text directly in the cell if you want to refine it
- Leave blank to skip (status unchanged)

**Save and close the file.**

---

## Step 4 — Import review decisions

```bash
python main.py import-review
```

Reads your Yes/No decisions from the most recent Excel and syncs them to the DB:
- `Yes` → status: `approved`
- `No` → status: `rejected`
- Blank → unchanged

Updates and re-exports the Excel to reflect final statuses.

---

## Step 5 — Send (explicit, always confirmed)

```bash
python main.py send
```

- Prompts for confirmation before sending anything
- Opens Chrome (headful — never headless for sends)
- Sends connection requests for all `approved` profiles
- Respects daily limit from `icp_config.yaml` (default: 20)
- Random 2–4 second delays between sends
- Updates status to `sent` or `failed`
- Re-exports Excel

---

## One-command pipeline

```bash
python main.py run-pipeline --icp icp1 --discover-limit 15
```

Runs Steps 1–4 automatically. Pauses at Step 3 for you to fill the Excel. Does not send (add `--send` to enable, still asks for confirmation).

---

## Inspect pipeline state

```bash
# Counts by status
python main.py stats

# List profiles by status
python main.py list --status discovered
python main.py list --status approved
python main.py list --status sent

# Re-export Excel at any time
python main.py export
```

---

## Refresh search queries

The Boolean search queries in `config/search_strings.yaml` are reused every run. To update them:

**Option A — Regenerate from ICP config:**
```bash
rm config/search_strings.yaml          # or delete manually
python main.py discover --icp icp1     # LLM generates new queries + auto-saves
```

**Option B — Seed from a reviewed Excel:**
```bash
python main.py create-search-config "outputs/YYYY-MM-DD/stage1_search_strings_*.xlsx"
```

**Option C — Edit manually:**
Open `config/search_strings.yaml` and edit the `query` fields directly. Format:

```yaml
search_strings:
  - segment: "SEGMENT 1 — Peer Competitor"
    query: '(title:"SVP Engineering" OR title:"Head of Platform") AND ("Kubernetes" OR "platform engineering") AND ("Banking" OR "Fintech")'
    rationale: "Targets lateral peers in financial services"
```

---

## Reset and start fresh

```bash
python main.py reset    # wipes scheduler.db — all profiles and messages deleted
```

Prompts for confirmation. Config files and Excel history are not deleted.

---

## Key design decisions

**Why no separate analyze step?**
The `analyze` command (post scraping + AI hook extraction) is available as a standalone but is not part of the default pipeline. Claude generates personalised messages directly from the scraped headline + About + Experience, which is sufficient for most senior profiles. Run `analyze` manually if you want deeper hook extraction for a specific batch.

**Why Playwright instead of LinkedIn API?**
LinkedIn's standard API does not support people search, profile scraping, or sending connection invitations without partnership-level access. Playwright handles all LinkedIn interactions through a real browser session.

**Why cached prompts?**
The message writer system prompt (~1,100 tokens) and quality-gate system prompt (~1,250 tokens) are cached via Anthropic's prompt caching feature. After the first profile in a run, subsequent calls pay ~10% of the input token cost for the stable system prompt. For a 20-profile run this saves approximately 40,000 tokens.

**Why not send automatically?**
LinkedIn can restrict or ban accounts that send too many requests too quickly or in automated patterns. Human review before sending is both a safety mechanism and a quality gate — you only send messages you have read and approved.
