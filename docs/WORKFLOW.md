# LinkedIn Connection Agent — Workflow

## Overview

A pipeline that discovers senior LinkedIn profiles across 5 ICP segments, generates personalised 200–300 word outreach messages using cached Claude Sonnet prompts, and sends connection requests — with explicit human approval before anything reaches LinkedIn.

**Sender:** Anindya Chakraborty — SVP, Product Engineering at Nomura  
**Goal:** Build a warm executive network across C-suite leaders, elite MBA graduates, top consulting firm leaders, and LinkedIn Top Voice thought leaders.

---

## Pipeline at a Glance

```
Phase 1          Phase 2           Phase 3             Phase 4        Phase 5
─────────        ──────────        ──────────          ─────────      ──────────
Boolean     →    Profile      →    Message         →   Human     →   Send
Search           Discovery         Generation          Review         Connection
(YAML or         (Playwright       (Claude Sonnet       (Excel)        (Playwright)
 CrewAI)          + body-text       cached, 8-rule
                  scraping)         quality gate)
```

---

## Phase 1 — Boolean Search Loading / Generation

**Trigger:** `python main.py discover`

### Priority loading

1. If `config/search_strings.yaml` exists → load directly (zero LLM cost, zero latency)
2. If missing → generate via `boolean_search_agent` (Claude Sonnet + CrewAI), auto-save to YAML

The YAML file is the source of truth. Delete it to regenerate from `icp_config.yaml`.

### Current ICP Segments (9 queries across 5 segments)

| Segment | Target | Purpose |
|---------|--------|---------|
| **1 — C-Suite Leaders** | CTO, CDO, MD, EVP, Group Head in banking/fintech | Career mentorship, sponsorship, board-level visibility |
| **2 — Elite MBA + Tech** | ISB, IIM-A/B/C, INSEAD, Wharton, Harvard grads in senior tech roles | Shared credential = natural conversation opener, higher acceptance rate |
| **3 — Top Consulting** | McKinsey, BCG, Bain, Deloitte, EY, KPMG, PwC Partners / MDs / Principals | Cross-firm architecture patterns, regulated-environment perspective |
| **4 — Thought Leaders** | LinkedIn Top Voice badge holders + Keynote Speakers / Authors | High-follower accounts already engaged with ideas — more likely to respond |
| **5 — Tier-1 Banks** | Engineering heads at Goldman, Morgan Stanley, JPMorgan, HSBC, Barclays | Warm presence before career opportunities arise |

**Keywords excluded from all queries:** DevOps, SRE, GCP, Azure, Software Engineer, Lead Engineer — these narrow to execution roles rather than leadership.

**Follower count limitation:** LinkedIn's boolean search has no follower-count filter. "LinkedIn Top Voice" keyword is the only reliable proxy (LinkedIn awards it to accounts with 10k+ engaged followers).

---

## Phase 2 — Profile Discovery

**Tool:** Playwright browser automation (`browser_tool.py`)  
**Storage:** SQLite via `ConnectionScheduler`

### How profiles are selected

For each Boolean search string:

1. **Fetch 2× candidates from LinkedIn** (capped at 15 per query) — fetching more than `max-per-query` ensures enough candidates to pick the best from.
2. **Parse headline from search card** — LinkedIn's search results use a fallback link selector. `_parse_card_text()` extracts name and headline from the combined card text (format: "Name • 2nd\n\nHeadline\n\nLocation").
3. **Seniority pre-filter (Phase 1):** If a non-empty headline is found, check against include/exclude lists using word-boundary regex. Empty headlines pass through to Phase 2 scraping.
4. **Visit each profile, scrape full headline** — LinkedIn removed stable CSS class names. The agent parses the page body text using `_extract_headline_from_body()`, which finds the headline by its position after the person's name in the rendered page content. Works regardless of LinkedIn's DOM/CSS changes.
5. **Seniority re-check (Phase 2):** Apply include/exclude list to the scraped headline.
6. **ICP scoring:** Score STRONG / WEAK / MISMATCH against `icp_config.yaml` (roles, industries, keywords including ISB/IIM/consulting firm names).
7. **Select top matches:** STRONG profiles saved first, WEAK fill remaining slots up to `max-per-query × number of queries`. MISMATCH profiles discarded.
8. **Dedup by profile URL** — same person cannot enter the pipeline twice.

### Seniority keywords

**Include (senior):** Director and above, VP/SVP/EVP/CXO, CTO/CIO/CDO, Head of X, Managing Director, Partner, Principal, Associate Partner, Founder, General Manager, Regional/Country Manager, President.

**Exclude (junior):** Junior, Software Engineer, Software Developer, Project/Program/Product Manager, Business Analyst, Data Analyst, Intern, Trainee, Associate (standalone).

Word-boundary matching prevents false exclusions: "Director of Software Engineering" is not excluded by the "software engineer" exclude keyword.

---

## Phase 3 — Profile Analysis (optional)

**Trigger:** `python main.py analyze`

The `analyze` command scrapes recent posts and runs AI analysis on profiles in `discovered` status. It is **optional** — `generate-messages` works directly from `discovered` profiles using the About + Experience scraped during discovery.

When run:
- **Post scraping:** Visits `linkedin.com/in/{profile}/recent-activity/` and extracts recent posts.
- **Profile hook analysis:** Claude Sonnet identifies the top 3 executive conversation hooks (specific, non-obvious signals worth a peer conversation — named migrations, team scaling inflections, architectural decisions).
- **Post hook analysis:** Claude Sonnet identifies the single best conversation entry point from recent posts. Returns `NO_POSTS_AVAILABLE` if no posts found.

Analysis output is stored in `recent_posts` column and passed to the message writer in Phase 4.

---

## Phase 4 — Message Generation

**Trigger:** `python main.py generate-messages`  
**Model:** Claude Sonnet (cached system prompt)

Writes a **200–300 word** LinkedIn outreach message. Works directly from `discovered` profiles — the `analyze` step is optional but improves message quality.

### Profile context passed to the message writer

```
LinkedIn URL: https://www.linkedin.com/in/...
Name: [name]
Headline: [headline]
About: [first 600 chars]
Experience: [top 4 roles, first 400 chars]

AI Analysis (hooks and post data):
[analyzed hooks + post hook if analyze was run]
  OR
[No post analysis — recipient has no public posts. Do not reference any posts.]
```

### Message structure (four short paragraphs, no bullets or headers)

| Para | Purpose |
|------|---------|
| **1 — Hook** | One specific observation about this person — cannot be sent unchanged to 100 other leaders |
| **2 — Who + Why** | One sentence on who Anindya is; one sentence on the shared domain or problem |
| **3 — Curiosity** | A specific tension, tradeoff, or challenge relevant to their background — shows intellectual depth and eagerness to learn |
| **4 — Ask** | Simple, direct ask ending with a genuine question (?) or CTA |

### What the message must never do

- Reference posts if no `Post Hook` section exists in the AI Analysis
- Use em-dashes (—), en-dashes (–), or double hyphens (--)
- Mention jobs, referrals, resumes, or certifications
- Use GPT filler: "leverage", "synergize", "touch base", "thought leadership", "game-changer", "deep dive"
- Use hollow flattery: "amazing work", "impressive profile", "brilliant", "legend"
- Make unhedged first-person claims: "I built", "I led", "we launched"

---

## Phase 5 — Message Validation (8-rule quality gate)

**Model:** Claude Sonnet (cached system prompt, ~1,250 tokens)  
**Auto-revision:** up to 2× using Claude Haiku (cheaper for mechanical fixes)

Pre-computed facts (word count, sentence length, regex hits) are passed as ground truth so the LLM evaluates rules without re-counting.

| Rule | Name | Method | Check |
|------|------|--------|-------|
| A | HUMAN_TONE | LLM | Avg sentence ≤ 15 words; no GPT filler phrases |
| B | WORD_COUNT | Pre-computed | 200–300 words — hard reject outside range |
| C | EYE_CATCHING_HOOK | LLM | Opening is specific — cannot fit any other leader |
| D | NON_OBVIOUS_QUESTION | LLM | Contains a practitioner-only insight or observation |
| E | ACHIEVEMENT_GUARD | Pre-computed | No unhedged "I built / I led / we launched" |
| F | FORMAT_COMPLIANCE | Pre-computed | No em-dashes, en-dashes, or double hyphens |
| G | ROLE_ALIGNMENT | LLM | 3–5 distinct hooks for senior tech leadership audience |
| I | ENGAGEMENT_HOOK | Pre-computed + LLM | Last sentence is a genuine question or direct CTA |

On FAIL: issues are passed to Claude Haiku which revises the message. Retried once. If still failing after 2 attempts, saved as `message_drafted` with issues flagged in terminal for human review.

---

## Phase 6 — Human Review (Excel)

**Trigger:** `python main.py export` → review Excel → `python main.py import-review`

The Excel file (`outputs/YYYY-MM-DD/profiles_review_*.xlsx`) has four columns:

| Column | Description |
|--------|-------------|
| Name | Profile display name |
| LinkedIn URL | Clickable hyperlink |
| Generated Message | 200–300 word outreach |
| Shortlisted (Yes / No) | You fill this — drives import-review |

- **Yes** → status `approved`, optional message edit in the cell is preserved
- **No** → status `rejected`
- **Blank** → unchanged

Alternatively, use the interactive Rich CLI for real-time review:

```bash
python main.py review    # approve / edit / skip / reject per profile
```

---

## Phase 7 — Send Connection Request

**Trigger:** `python main.py send --limit 20` (prompts for confirmation)  
**Tool:** Playwright browser (headful — never headless)

For each `approved` profile:
1. Navigate to profile URL
2. Click **Connect** (checks main CTA, then More → Connect dropdown)
3. Click **Add a note**
4. Fill in the approved message
5. Click **Send**
6. Success → status `sent` | Failure → status `failed` with error logged

---

## Profile Lifecycle

```
          ┌─────────────┐
          │  discovered │  ← save_discovered() — profile URL found via search + scraped
          └──────┬──────┘
                 │  (optional) python main.py analyze
          ┌──────▼──────┐
          │   analyzed  │  ← save_analyzed() — hooks + post data extracted
          └──────┬──────┘
                 │  python main.py generate-messages
        ┌────────▼────────┐
        │ message_drafted │  ← save_message() — message ready for human review
        └────────┬────────┘
          ┌──────┴──────┐
          │             │
   ┌──────▼──────┐  ┌───▼──────┐
   │  approved   │  │ rejected │  ← human decision (Excel or CLI)
   └──────┬──────┘  └──────────┘
          │  python main.py send
   ┌──────▼──────┐
   │    sent     │  ← mark_sent()
   └─────────────┘
   ┌─────────────┐
   │   failed    │  ← mark_failed() — browser automation error
   └─────────────┘
```

---

## CLI Reference

```bash
# Setup
python main.py auth                                    # browser login + OAuth

# Discovery
python main.py discover --icp icp1 --max-per-query 5   # find top-match profiles
python main.py discover --icp icp1 --max-per-query 5 --fresh  # wipe DB first
python main.py discover --icp icp1 --location "United Kingdom"  # different region

# Analysis (optional — improves message quality)
python main.py analyze --limit 10

# Message generation
python main.py generate-messages --limit 20

# Review
python main.py export              # export Excel for review
python main.py import-review       # sync Excel decisions back to DB
python main.py review              # interactive Rich CLI review

# Send
python main.py send --limit 20     # sends approved (confirmation required)

# Full pipeline
python main.py run-pipeline --icp icp1 --discover-limit 5 --message-limit 20
python main.py run-pipeline --icp icp1 --discover-limit 5 --send  # auto-send after review

# Inspect
python main.py list                # all profiles
python main.py list --status approved
python main.py stats               # counts by status

# Reset
python main.py reset               # wipe DB + run ID
python main.py reset --all         # also wipe PDFs and old Excel files
```

---

## Token Cost Design

All LLM calls use prompt caching to minimise cost:

| Call | Model | Cache threshold | When cached |
|------|-------|----------------|-------------|
| Profile analysis | Claude Sonnet | 1,024 tokens | All calls after first in a 5-min window |
| Post analysis | Claude Sonnet | 1,024 tokens | All calls after first |
| Message writing | Claude Sonnet | 1,024 tokens | All calls after first |
| Quality gate (8 rules) | Claude Sonnet | 1,024 tokens | All calls after first |
| Message revision | Claude Haiku | — | No caching (revisions are rare) |

Cache write: 1.25× input cost. Cache read: 0.1× input cost.  
For a 20-profile run on the quality gate alone (~1,250-token prompt), caching saves approximately 85% of input token cost from the second profile onward.

---

## Key Design Constraints

| Constraint | Reason |
|---|---|
| No silent sending — `send` always prompts | Connection requests are irreversible |
| Human review before send | AI tone is subtly imperfect; a human catches what the validator misses |
| Headful browser for sending | LinkedIn's bot detection is aggressive against headless Chromium |
| 20 connections/day limit | Conservative default to avoid LinkedIn account restrictions |
| Body-text headline parsing | LinkedIn removed stable CSS class names — DOM-agnostic extraction |
| Word-boundary seniority matching | "Director of Software Engineering" must not be excluded by "software engineer" |
| STRONG-first profile selection | Best ICP matches saved first; WEAK only fill remaining slots |
| Post hallucination guard | Message writer explicitly told when no posts exist — cannot invent post references |

---

## Output Files

```
outputs/
├── scheduler.db                    # SQLite — full profile lifecycle history
├── .current_run_id                 # tracks active Excel file across pipeline steps
├── linkedin_session.json           # Playwright cookies — gitignored
├── linkedin_tokens.json            # OAuth access token — gitignored
└── YYYY-MM-DD/
    └── profiles_review_<id>.xlsx   # Excel review file for this run
```
