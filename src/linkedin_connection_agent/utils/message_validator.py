"""
Message Validator — 8-rule quality gate for LinkedIn connection request messages.

Rules A/B/F/G are deterministic (regex/string matching).
Rules C/D/E/H are LLM-as-judge via Claude Haiku.
"""
import json
from dataclasses import dataclass, field

from anthropic import Anthropic

_FORBIDDEN_JOB = [
    "looking for opportunities", "open to opportunities", "please refer",
    "need a job", "job seeker", "actively looking", "seeking a position",
    "i'm available", "notice period", "job search", "find me a role",
]
_FORBIDDEN_DESPERATION = [
    "desperately", "urgently", "kindly", "please help", "really need",
]
_FORBIDDEN_RESUME = [
    "my resume", "my cv", "my background", "my experience includes",
    "i'm skilled in", "skilled in", "proficient in", "i'm passionate about",
    "years of experience",
]
_FORBIDDEN_CLAIMS = [
    "i'm a great", "i'm an expert", "i'm highly", "i am highly", "strong background",
]
_FORBIDDEN_FLATTERY = ["amazing", "incredible", "great work", "fantastic profile", "brilliant"]


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

        # A: CHARACTER_LIMIT
        if char_count > 300:
            issues.append(f"CHARACTER_LIMIT: {char_count} chars — exceeds 300 limit")

        # B: NO_JOB_ASK
        for phrase in _FORBIDDEN_JOB:
            if phrase in msg_lower:
                issues.append(f"NO_JOB_ASK: contains '{phrase}'")
                break

        # F: NO_DESPERATION
        for phrase in _FORBIDDEN_DESPERATION + _FORBIDDEN_FLATTERY:
            if phrase in msg_lower:
                issues.append(f"NO_DESPERATION: contains '{phrase}'")
                break

        # G: NO_RESUME_SIGNAL
        for phrase in _FORBIDDEN_RESUME + _FORBIDDEN_CLAIMS:
            if phrase in msg_lower:
                issues.append(f"NO_RESUME_SIGNAL: contains '{phrase}'")
                break

        # C/D/E/H: LLM judge
        issues.extend(self._llm_audit(message))

        return ValidationResult(passed=len(issues) == 0, issues=issues, character_count=char_count)

    def _llm_audit(self, message: str) -> list[str]:
        prompt = f"""Audit this LinkedIn connection request message against 4 rules.
Return ONLY a JSON array of issue strings. Empty array [] means all rules pass.

Message: "{message}"

Rules:
C. SPECIFIC_REFERENCE: Does it reference something specific to a particular person's profile or post? Generic observations that fit anyone = issue.
D. PEER_TONE: Does it sound like a thoughtful peer, not a job seeker? Applicant framing = issue.
E. CURIOSITY_TRIGGER: Would the recipient think "interesting perspective"? Transactional or needy = issue.
H. ENGAGEMENT_HOOK: Does it end with a genuine question about the recipient? Statement ending = issue.

Output format: ["RULE_NAME: description"] or []
Example: ["D: sounds like a candidate — 'I would love to connect'"]
Respond with ONLY the JSON array."""

        response = self._client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            return json.loads(response.content[0].text.strip())
        except Exception:
            return []
