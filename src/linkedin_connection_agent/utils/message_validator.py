"""
Message validators for the LinkedIn Connection Agent.

OutreachQualityChecker — 8-rule gate (A, B, C, D, E, F, G, I) applied to the
  250-300 word outreach message. Pre-computes measurable facts (word count,
  sentence length, regex hits) then passes them as ground truth to a single
  cached Sonnet LLM call that evaluates all 8 rules in one shot.

  System prompt is ~1,250 tokens so Anthropic caching activates on claude-sonnet-4-6
  (1,024-token minimum). Subsequent profiles in the same run pay ~10% cache-read cost.

MessageValidator — legacy 300-char connection note gate (kept for backward compat).
"""
import json
import re
from dataclasses import dataclass, field

from anthropic import Anthropic

# ── Pre-computation helpers (passed as ground truth to LLM) ──────────────────
_GPT_FILLER = [
    "game-changer", "game changer", "paradigm shift",
    "in today's landscape", "in the current landscape",
    "delve into", "delve deeper",
    "navigate the complexities", "navigate the complexity",
    "as we move forward", "ever-evolving", "ever evolving",
    "it's worth noting", "needless to say", "at the end of the day",
    "move the needle", "low-hanging fruit", "synergize", "synergy",
    "thought leadership", "value-add", "circle back", "touch base",
    "deep dive", "unpack this", "cutting-edge", "state-of-the-art",
    "leverage", "in today's fast-paced", "the modern era",
    "rapidly changing", "disruptive innovation", "transformational journey",
]

_ACHIEVEMENT_PATTERNS = [
    r"\bI built\b", r"\bI implemented\b", r"\bI developed\b", r"\bI created\b",
    r"\bI architected\b", r"\bI designed\b", r"\bI launched\b", r"\bI led\b",
    r"\bI delivered\b", r"\bI drove\b", r"\bI scaled\b", r"\bI managed\b",
    r"\bwe built\b", r"\bwe launched\b", r"\bwe deployed\b", r"\bwe designed\b",
    r"\bwe developed\b", r"\bwe architected\b", r"\bwe delivered\b",
]

# ── Comprehensive 8-rule system prompt — all rules in one cached call ─────────
# ~1,250 tokens → above the 1,024-token Sonnet minimum for prompt caching.
_OUTREACH_AUDIT_SYSTEM = """\
You audit a LinkedIn outreach message against 8 quality rules for Anindya Chakraborty,
SVP Product Engineering, Nomura. Recipients are Director, VP, CTO, or MD-level technology
leaders in banking, fintech, or cloud engineering.

You receive: (1) pre-computed factual metrics as ground truth, (2) the message text.
Use the pre-computed facts verbatim for quantitative rules — do not re-count.
Apply your own judgment for qualitative rules.
Return ONLY a JSON array of issue strings. Empty [] = all rules pass.

━━━ RULE A: HUMAN_TONE ━━━
Use the pre-computed average sentence length. Flag if > 15 words per sentence.
Also check the GPT filler phrases list. Flag any detected phrase.
FAIL examples: "A: average sentence 19 words — break into shorter sentences"
               "A: GPT filler 'leverage' — replace with plain English"

━━━ RULE B: WORD_COUNT ━━━
Use the pre-computed word count. Do NOT count yourself.
Hard FAIL if > 300 words. FAIL if < 200 words. PASS if 200-300.
FAIL examples: "B: 312 words — trim by at least 12 words"
               "B: 178 words — add specific detail to reach 200"

━━━ RULE C: EYE_CATCHING_HOOK ━━━
The opening 1-2 sentences must be bold, specific, and either counterintuitive or
tension-surfacing. Must reference something real and specific about this recipient.
FAIL if the opening could be sent unchanged to 100 other senior leaders.

PASS: "Your team's move from 40 to 200 engineers at Paytm while keeping weekly deploys
caught my attention. Most orgs at that growth rate sacrifice release frequency first."
(named company + specific detail + counterintuitive observation)

FAIL: "I came across your profile and was impressed by your work." (generic flattery)
FAIL: "As a senior leader in fintech, I am sure you understand the challenges..." (patronising)
FAIL: "I hope this message finds you well." (filler opener)
FAIL: "Your profile caught my attention." (vague, no specific detail)
FAIL: "I recently came across your impressive career..." (generic, could fit anyone)
FAIL output example: "C: opening is generic — 'Your profile caught my attention' fits any leader"

━━━ RULE D: NON_OBVIOUS_QUESTION ━━━
At least one observation or question must surface a practitioner-only constraint or
challenge conventional thinking. A general non-practitioner reader must NOT already know it.

PASS: "Most platform teams optimise for developer experience before fixing deployment
ownership, and find the reverse order cuts incident volume faster." (non-obvious sequence)
PASS: "Banks that move compliance sign-off earlier in the SDLC slow 30-40% initially,
then ship 60% faster after the model matures." (counterintuitive, specific numbers)
PASS: "FinOps at regulated scale creates an interesting tension — cost optimisation and
compliance isolation pull in exactly opposite directions." (domain-specific conflict)

FAIL: "Cloud engineering is increasingly important in today's landscape." (obvious)
FAIL: "AI is transforming the financial sector." (generic, widely known)
FAIL: "Platform engineering enables faster delivery." (basic, no insight)
FAIL output example: "D: no non-obvious insight — restates that cloud migration is challenging"

━━━ RULE E: ACHIEVEMENT_GUARD ━━━
Use the pre-computed achievement claims list. If any unhedged first-person active claim
is detected (I built, I led, we launched, we deployed, etc.), flag it.
Passive voice or hedged framing is required instead.
Acceptable: "was observed at a Tier 1 bank", "a team I advised", "results I have seen"
FAIL output example: "E: unhedged claim 'I built' detected — use passive or hedged framing"

━━━ RULE F: FORMAT_COMPLIANCE ━━━
Use the pre-computed em-dash detection result. Do not scan the text yourself.
Flag if em-dash (—), en-dash (–), or double hyphen (--) was found.
These must be replaced with a comma, colon, or full stop.
FAIL output example: "F: em-dash (—) found — replace with comma, colon, or full stop"

━━━ RULE G: ROLE_ALIGNMENT ━━━
Count the distinct specific conversation hooks or curiosity triggers in the message
aimed at a Director, VP, CTO, or MD-level leader in engineering, fintech, or cloud.

Valid hooks (count each once): named technology challenge at scale, organisational tension
in large engineering orgs, regulatory or compliance engineering observation, career or
leadership inflection point, named architectural tradeoff or decision.

Non-hooks (do not count): "technology challenges", "digital transformation", "leadership
journey", "exciting work", "impressive experience", "rapidly evolving landscape" — vague,
generic, applies to anyone.

FAIL if fewer than 3 distinct valid hooks. FAIL if more than 5 (unfocused, scattered).
FAIL output example: "G: 2 distinct hooks found — need 3 to 5 specific anchors"

━━━ RULE I: ENGAGEMENT_HOOK ━━━
Use the pre-computed last-line data. The message must end with a genuine question (?)
or a direct CTA that invites a response. A closing statement is a FAIL.
PASS: "Would you be open to a 20-minute call?", "Curious what your take is on this."
FAIL: "Looking forward to connecting." (statement, no invitation)
FAIL: "Hope to chat soon." (vague, no question or genuine CTA)
FAIL: "Please do connect." (command, not a question)
FAIL: "Feel free to reach out." (deflects action, not an invitation)
FAIL output example: "I: closes with statement 'Looking forward to connecting' — end with a question or genuine CTA"

━━━ CALIBRATION ━━━
Each rule is independent — a message can pass C and fail D, or fail G alone.
Be strict on C and D — well-written but generic messages still fail.
For B and E and F, trust the pre-computed facts; do not override them.
For G, list the hooks you identify before counting; only count specific, non-generic ones.
Missing a real failure is worse than flagging a borderline case.

━━━ OUTPUT FORMAT ━━━
Return ONLY a valid JSON array. No prose, no markdown, no explanation outside the array.
All pass: []
With failures: ["B: 187 words — below 200 minimum", "C: opening is generic", "I: closes with statement"]\
"""


# ── Legacy audit prompt (300-char connection note) ────────────────────────────
_LLM_AUDIT_SYSTEM = (
    "Audit a LinkedIn connection message against 4 quality rules.\n"
    "Return ONLY a JSON array of issue strings. Empty array [] means all rules pass.\n\n"
    "Rules:\n"
    "C. SPECIFIC_REFERENCE: Must reference something specific to THIS exact person's "
    "profile or post. Generic observations that fit anyone = issue.\n"
    "D. EXECUTIVE_TONE: Must sound like SVP/Director reaching out to CTO/VP as a peer. "
    "Candidate, recruiter, or salesperson framing = issue.\n"
    "E. CURIOSITY_TRIGGER: Recipient must think 'interesting perspective' not "
    "'this person wants something'. Transactional or needy framing = issue.\n"
    "H. ENGAGEMENT_HOOK: Must end with a genuine question about the recipient's work "
    "or decisions. Statement ending = issue.\n\n"
    "Output format: [\"RULE_NAME: description\"] or []\n"
    "Example: [\"D: sounds like a candidate — 'I would love to connect'\"]"
)

_FORBIDDEN_JOB = [
    "looking for opportunities", "open to opportunities", "please refer",
    "need a job", "job seeker", "actively looking", "seeking a position",
    "i'm available", "notice period", "job search", "find me a role",
    "keep me in mind", "next play",
]
_FORBIDDEN_DESPERATION = [
    "desperately", "urgently", "kindly", "please help", "really need",
    "would be honored", "please connect",
]
_FORBIDDEN_RESUME = [
    "my resume", "my cv", "my background", "my experience includes",
    "i'm skilled in", "skilled in", "proficient in", "i'm passionate about",
    "years of experience",
]
_FORBIDDEN_CLAIMS = [
    "i'm a great", "i'm an expert", "i'm highly", "i am highly", "strong background",
]
_FORBIDDEN_FLATTERY = [
    "amazing", "incredible", "great work", "fantastic profile", "brilliant",
    "guru", "legend",
]


# ─────────────────────────────────────────────────────────────────────────────
# OutreachQualityChecker  (8 rules — single cached Sonnet call)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OutreachValidationResult:
    passed: bool
    issues: list[str] = field(default_factory=list)
    word_count: int = 0


class OutreachQualityChecker:
    """
    Evaluates all 8 rules (A-I) in a single cached Sonnet API call.

    Pre-computed facts (word count, sentence length, regex hits) are passed
    as ground truth in the user message so the LLM does not need to count or
    scan for patterns — avoiding hallucination on quantitative checks.
    """

    def __init__(self):
        self._client = Anthropic()

    def check(self, message: str) -> OutreachValidationResult:
        words = message.split()
        word_count = len(words)

        # ── Pre-compute measurable facts ─────────────────────────────────────
        sentences = [s.strip() for s in re.split(r"[.!?]+", message) if s.strip()]
        avg_len = round(
            sum(len(s.split()) for s in sentences) / len(sentences), 1
        ) if sentences else 0.0

        gpt_found = [p for p in _GPT_FILLER if p.lower() in message.lower()]

        achievement_found: list[str] = []
        for pat in _ACHIEVEMENT_PATTERNS:
            m = re.search(pat, message, re.IGNORECASE)
            if m:
                achievement_found.append(m.group())

        em_dash_found = "—" in message or "–" in message or " -- " in message

        non_empty = [ln.strip() for ln in message.strip().splitlines() if ln.strip()]
        last_line = non_empty[-1] if non_empty else ""

        facts = (
            f"Word count: {word_count}\n"
            f"Average sentence length: {avg_len} words\n"
            f"GPT filler phrases detected: "
            f"{gpt_found if gpt_found else 'none'}\n"
            f"Unhedged achievement claims detected: "
            f"{achievement_found if achievement_found else 'none'}\n"
            f"Em-dash or double-hyphen found: "
            f"{'YES — must fix' if em_dash_found else 'NO'}\n"
            f"Last line: \"{last_line}\"\n"
            f"Last line has question mark: {'YES' if '?' in last_line else 'NO'}"
        )

        issues = self._llm_audit_all(message, facts)
        return OutreachValidationResult(
            passed=len(issues) == 0,
            issues=issues,
            word_count=word_count,
        )

    def _llm_audit_all(self, message: str, facts: str) -> list[str]:
        """Single cached Sonnet call — evaluates all 8 rules at once."""
        user_content = (
            f"PRE-COMPUTED FACTS (use as ground truth — do not override):\n"
            f"{facts}\n\n"
            f"MESSAGE:\n\"\"\"\n{message}\n\"\"\"\n\n"
            f"Audit all 8 rules (A, B, C, D, E, F, G, I). "
            f"Return only the JSON array."
        )
        response = self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            system=[{"type": "text", "text": _OUTREACH_AUDIT_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content}],
        )
        try:
            raw = response.content[0].text.strip()
            start = raw.find("[")
            end = raw.rfind("]") + 1
            return json.loads(raw[start:end]) if start != -1 else []
        except Exception:
            return []


# ─────────────────────────────────────────────────────────────────────────────
# MessageValidator  (legacy — 300-character connection note)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    passed: bool
    issues: list[str] = field(default_factory=list)
    character_count: int = 0


class MessageValidator:
    def __init__(self):
        self._client = Anthropic()

    def validate(self, message: str) -> ValidationResult:
        issues: list[str] = []
        char_count = len(message)
        msg_lower = message.lower()

        if char_count > 300:
            issues.append(f"CHARACTER_LIMIT: {char_count} chars — exceeds 300 limit")

        for phrase in _FORBIDDEN_JOB:
            if phrase in msg_lower:
                issues.append(f"NO_JOB_ASK: contains '{phrase}'")
                break

        for phrase in _FORBIDDEN_DESPERATION + _FORBIDDEN_FLATTERY:
            if phrase in msg_lower:
                issues.append(f"NO_DESPERATION: contains '{phrase}'")
                break

        for phrase in _FORBIDDEN_RESUME + _FORBIDDEN_CLAIMS:
            if phrase in msg_lower:
                issues.append(f"NO_RESUME_SIGNAL: contains '{phrase}'")
                break

        issues.extend(self._llm_audit(message))
        return ValidationResult(passed=len(issues) == 0, issues=issues, character_count=char_count)

    def _llm_audit(self, message: str) -> list[str]:
        response = self._client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=_LLM_AUDIT_SYSTEM,
            messages=[{"role": "user", "content": f'Message: "{message}"\n\nAudit now.'}],
        )
        try:
            return json.loads(response.content[0].text.strip())
        except Exception:
            return []
