# LinkedIn Connection Agent — Workflow

## Overview

A 7-phase multi-agent pipeline that discovers LinkedIn profiles across three ICP segments, analyses them with AI, generates personalized ≤300-character connection notes, and sends them — with explicit human approval before anything reaches LinkedIn.

**Sender:** Anindya Chakraborty — SVP, Product Engineering at Nomura  
**Goal:** Build a warm executive network across peers, senior leaders, and hiring decision-makers before opportunities arise.

---

## Pipeline at a Glance

```
Phase 1        Phase 2        Phase 3           Phase 4         Phase 5          Phase 6        Phase 7
─────────      ──────────     ──────────        ─────────       ──────────       ───────────    ──────────
Boolean   →    Profile   →    Profile +    →    Message   →    Message    →    Human      →    Send
Search         Discovery      Post Analysis     Generation      Validation       Review         Connection
(CrewAI)       (Playwright)   (CrewAI ×2)       (CrewAI)        (Local+LLM)      (Rich CLI)     (Playwright)
```

---

## Phase 1 — Boolean Search Generation

**Agent:** `boolean_search_agent` (Claude Sonnet)  
**Trigger:** `python main.py discover` or `python main.py run`

The agent generates **6–8 LinkedIn Boolean search strings** distributed across three segments:

### Segment 1 — Peer Competitors (2 strings)
> Same-level leaders navigating identical architectural and team-scale challenges.

- **Roles:** SVP Engineering, Director of Engineering, Head of Platform, Head of Cloud, Head of Infrastructure
- **Industries:** Global Investment Banks, Fintech, Product Companies
- **Purpose:** Knowledge exchange and market awareness — what are peers at Goldman, Morgan Stanley, Razorpay, PhonePe building?

### Segment 2 — Senior Leaders Ahead on Career Path (3 strings)
> Leaders who have already solved the problems Anindya is currently navigating.

- **Roles:** CTO, CIO, VP Engineering, EVP Technology, MD Technology
- **Industries:** Investment Banking, Fintech, Global Product Companies, Cloud Infra
- **Purpose:** Career mentorship, thought leadership, future sponsorship. Priority: active posters and conference speakers.

### Segment 3 — Hiring Decision Makers (2–3 strings)
> People who can refer, recommend, or directly open doors.

- **Roles:** Engineering Directors, VPs, CTOs, Senior Hiring Managers, Technical Talent Partners
- **Industries:** Global Investment Banks, Tier-1 Fintech, Product-Led Tech, Series B+ Cloud/Infra Startups
- **Purpose:** Build warm presence before opportunities arise — warm network contacts, not cold applications.

### Output Format
```json
[
  {
    "segment": "SEGMENT 2 — Senior Leader",
    "query": "(title:\"CTO\" OR title:\"VP Engineering\") AND (\"AWS\" OR \"Platform\") AND (\"Fintech\" OR \"Banking\")",
    "rationale": "Targets C-suite tech leaders in financial services with platform/cloud expertise"
  }
]
```

---

## Phase 2 — Profile Discovery

**Tool:** Playwright browser automation (`browser_tool.py`)  
**Storage:** SQLite via `ConnectionScheduler`

For each Boolean search string:
1. Opens LinkedIn People Search (targets 1st + 2nd degree connections)
2. Extracts `{name, headline, url}` from result cards
3. Paginates until `max_per_query` limit is reached
4. Deduplicates by profile URL — same person cannot enter twice
5. Saves each profile as status `discovered` in `outputs/scheduler.db`

**Rate limiting:** 2–4 second random delays between actions. Daily limit: 20 connections (configurable in `icp_config.yaml`).

---

## Phase 3 — Profile + Post Analysis

**Agents:** `profile_analyzer_agent` + `post_analyzer_agent` (sequential CrewAI crew)  
**Trigger:** `python main.py analyze`

For each `discovered` profile:

### 3a. Profile Scraping (Playwright)
Visits the profile page and extracts:
- Name, headline, About section
- Top 3 experience entries
- Top 5 skills
- Attempts PDF download via **More → Save to PDF**

### 3b. Profile Analysis (Claude Sonnet)
`profile_analyzer_agent` extracts the **top 3 executive conversation hooks** — non-obvious signals worth a peer conversation:

| What counts as a hook | What doesn't |
|----------------------|--------------|
| Specific platform modernization they led | Their job title |
| Career shift: legacy banking → high-growth fintech | "impressive career" |
| Migrated 1000+ databases to AWS | Generic compliments |
| Architectural decision revealing systems thinking | Anything findable in 5 seconds |
| Transition from IC to engineering leader at scale | Current company name |

### 3c. Post Analysis (Claude Haiku)
`post_analyzer_agent` visits `linkedin.com/in/{profile}/recent-activity/shares/` and identifies the **single best conversation entry point** from the last 3 posts:
- An architectural opinion or technology stance
- A leadership tension surfaced ("managing tech debt vs delivery")
- A macro prediction or contrarian take
- A question posed to their audience

Returns `NO_POSTS_AVAILABLE` if the profile has no recent activity.

**Profile saved** as status `analyzed` with raw `profile_data` (JSON), `recent_posts` (JSON), and `pdf_path`.

---

## Phase 4 — Message Generation

**Agent:** `message_writer_agent` (Claude Sonnet)  
**Trigger:** `python main.py generate-messages`

Writes a **≤300-character** LinkedIn connection note following this architecture:

```
Specific observation → Insight/tension → Curious question → Soft continuation
```

### What the message MUST do
- Reference something **specific to this one person** — their scale, a decision, a technology choice, or a post
- Sound like an **SVP reaching out to a CTO/VP** — peer to peer
- Make the message about **their work**, not the sender's background
- End with a **genuine question** about their perspective or decision-making
- Imply capability through insight — never claim "I have X years of experience"

### What the message must NEVER do
- Mention looking for roles, opportunities, or referrals
- List sender's skills, tech stack, or years of experience
- Use flattery ("amazing profile", "incredible work")
- Show desperation ("kindly", "please help", "urgently")
- Use a generic opener that could apply to 100 other profiles

### Example output
> Saw your team's migration at scale across legacy and cloud. The runbook debt problem seems harder long-term than the schema work itself. Curious how you're handling rollback confidence when both environments are live?

---

## Phase 5 — Message Validation

**Two-layer gate:** local deterministic rules + LLM-as-judge  
**Auto-revise:** up to 2× before surfacing to human review

### 8 Quality Rules (A–H)

| Rule | Name | Method | Test |
|------|------|--------|------|
| A | CHARACTER_LIMIT | Deterministic | ≤ 300 chars — hard reject |
| B | NO_JOB_ASK | Deterministic (regex) | No: "looking for opportunities", "please refer", "keep me in mind", "next play", "notice period" |
| C | SPECIFIC_REFERENCE | LLM judge | Must reference something unique to this person — not applicable to 100 others |
| D | EXECUTIVE_TONE | LLM judge | Must sound like SVP→CTO/VP peer outreach — not a junior candidate, recruiter, or salesperson |
| E | CURIOSITY_TRIGGER | LLM judge | Recipient must think "interesting perspective", not "this person wants something" |
| F | NO_DESPERATION | Deterministic (regex) | No: "desperately", "would be honored", "please connect", "kindly", "guru", "legend" |
| G | NO_RESUME_SIGNAL | Deterministic (regex) | No sender skills, resume, CV, or years of experience — message is 100% about the recipient |
| H | ENGAGEMENT_HOOK | LLM judge | Must end with a genuine question about recipient's work or decisions |

On FAIL: Claude Sonnet revises the message with the issue list as feedback. Retried once. If still failing after 2 attempts, saved as `message_drafted` for human to fix during review.

---

## Phase 6 — Human Review

**Interface:** Interactive Rich CLI  
**Trigger:** `python main.py review`

For each `message_drafted` profile, displays:

```
════════════════════════════════════════════════════
┌─ Profile ────────────────────────────────────────┐
│ Jane Smith                                        │
│ CTO at FinBank | Cloud Architecture | AWS         │
│ linkedin.com/in/janesmith                         │
└──────────────────────────────────────────────────┘
┌─ Most Recent Post (excerpt) ─────────────────────┐
│ "We migrated 400 microservices to EKS. The        │
│  networking layer broke first, not the app..."    │
└──────────────────────────────────────────────────┘
┌─ Proposed Message ───────────────────────────────┐
│ Your EKS migration post surfaced something I've  │
│ been sitting with — networking always breaks      │
│ before the app in high-tenant environments.       │
│ Curious whether CNI choice changed after that?   │
│                                                   │
│ 218 / 300 characters                             │
└──────────────────────────────────────────────────┘
Action [approve/edit/skip/reject]:
```

| Action | Outcome |
|--------|---------|
| `approve` | Status → `approved`, ready to send |
| `edit` | Human types revised message (re-validated for 300-char limit), status → `approved` |
| `skip` | Stays as `message_drafted`, shown again next review session |
| `reject` | Status → `rejected`, human enters optional reason |

---

## Phase 7 — Send Connection Request

**Tool:** Playwright browser automation  
**Trigger:** `python main.py send --limit 20` (prompts for confirmation)

For each `approved` profile:
1. Navigates to profile URL
2. Clicks **Connect** button (checks main CTA, then More → Connect dropdown)
3. Clicks **Add a note**
4. Fills in the approved message
5. Clicks **Send**
6. On success: status → `sent`  
7. On failure: status → `failed` with error message logged

> **Important:** Browser runs headful (non-headless) — LinkedIn detects headless Chromium more aggressively. Never switch to `headless=True` for the send phase.

---

## Profile Lifecycle (State Machine)

```
          ┌─────────────┐
          │  discovered │  ← save_discovered() — profile URL found via search
          └──────┬──────┘
                 │ scrape + AI analysis
          ┌──────▼──────┐
          │   analyzed  │  ← save_analyzed() — profile data + posts extracted
          └──────┬──────┘
                 │ message generated + validated
        ┌────────▼────────┐
        │ message_drafted │  ← save_message() — message ready for human review
        └────────┬────────┘
          ┌──────┴──────┐
          │             │
   ┌──────▼──────┐  ┌───▼──────┐
   │  approved   │  │ rejected │  ← human decision
   └──────┬──────┘  └──────────┘
          │ browser sends request
   ┌──────▼──────┐
   │    sent     │  ← mark_sent() — connection request dispatched
   └─────────────┘
          (LinkedIn response: pending / accepted / declined — tracked externally)

   ┌─────────────┐
   │   failed    │  ← mark_failed() — browser automation error
   └─────────────┘
```

---

## CLI Command Reference

```bash
# One-time setup
python main.py auth                          # Browser login + OAuth token exchange

# Full pipeline
python main.py run --icp icp1 --limit 10    # All phases end-to-end

# Step-by-step
python main.py discover --icp icp1 --max-per-query 15   # Phase 1 + 2
python main.py analyze --limit 10                        # Phase 3
python main.py generate-messages --limit 10              # Phase 4 + 5
python main.py review                                    # Phase 6
python main.py send --limit 20                           # Phase 7 (confirms)

# Headless (no interactive review, outputs JSON)
python run_headless.py --icp icp1 --limit 5

# Inspect pipeline
python main.py list                          # All profiles, all statuses
python main.py list --status approved        # Filter by status
python main.py stats                         # Count by status (pipeline health)
```

---

## Data Flow

```
icp_config.yaml
    │  target_profile_description, target_roles, industries, keywords
    ▼
boolean_search_agent  ──►  6-8 Boolean search strings (with segment labels)
    │
    ▼
Playwright (LinkedIn People Search)
    │  profile URLs, names, headlines
    ▼
outputs/scheduler.db  [status: discovered]
    │
    ▼
Playwright (profile scrape + PDF download + recent posts)
    │
    ▼
profile_analyzer_agent + post_analyzer_agent
    │  top 3 hooks, best post entry point
    ▼
outputs/scheduler.db  [status: analyzed]
    │
    ▼
message_writer_agent  ──►  ≤300-char connection note draft
    │
    ▼
MessageValidator (8 rules A–H)  ──►  auto-revise ×2 on FAIL
    │
    ▼
outputs/scheduler.db  [status: message_drafted]
    │
    ▼
Human review (Rich CLI)
    │
    ▼
outputs/scheduler.db  [status: approved | rejected]
    │
    ▼
Playwright (Connect + Add note + Send)
    │
    ▼
outputs/scheduler.db  [status: sent | failed]
```

---

## Key Design Constraints

| Constraint | Reason |
|---|---|
| No silent sending — `send` always prompts for confirmation | Connection requests are irreversible; wrong message to wrong person is costly |
| Human review is mandatory between generation and send | AI-generated messages can fail subtly on tone; a human sees nuance the validator misses |
| Headful browser for connection sending | LinkedIn's bot detection is more aggressive against headless Chromium |
| 20 connections/day limit | LinkedIn restricts accounts that send too many requests; conservative default avoids flags |
| SHA-256 dedup on profile URL | Prevents re-discovering and re-messaging the same person across multiple sessions |
| OAuth API is identity-only | LinkedIn's standard developer API does not support search or invitation endpoints — browser automation handles all interaction |

---

## Output Files

```
outputs/
├── scheduler.db                 # SQLite — full profile lifecycle history
├── linkedin_session.json        # Playwright cookies — gitignored
├── linkedin_tokens.json         # OAuth access token — gitignored
└── profiles/
    └── pdfs/
        └── <profile-id>.pdf     # Downloaded LinkedIn profile PDFs
```
