"""User-question policy before the agent spends tools or tokens.

Checks plain text plus de-obfuscated candidates (spacing, leet, base64, reverse,
alnum-flattened signatures) so PyRIT-style converter mutations still hit the gate.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class InputVerdict:
    allowed: bool
    reason: str = ""
    flags: tuple[str, ...] = ()
    # Soft flags do not block; they travel into STM/gaps for audit.
    soft: bool = False


# High-signal jailbreak / instruction-override attempts on the *user* channel.
# Retrieved docs are handled separately via DATA framing — do not conflate.
_BLOCK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "instruction_override",
        re.compile(
            r"(?i)\b("
            r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+instructions?"
            r"|disregard\s+(all\s+)?(previous|prior|system)\s+(instructions?|prompts?)"
            r"|forget\s+(all\s+)?(previous|prior|your)\s+(instructions?|rules?|prompts?)"
            r"|override\s+(the\s+)?(system\s+)?prompt"
            r"|you\s+are\s+now\s+(dan|unrestricted|jailbroken|evil)"
            r"|developer\s+mode\s+(enabled|on)"
            r"|jailbreak\s*(mode)?"
            r"|do\s+anything\s+now"
            r"|new\s+system\s+prompt\s*:"
            r"|enter\s+(dan|unrestricted)\s+mode"
            r")\b"
        ),
    ),
    (
        "secret_exfiltration",
        re.compile(
            r"(?i)\b("
            r"dump\s+(all\s+)?(secrets?|api\s*keys?|passwords?|tokens?|jwt|credentials?)"
            r"|print\s+(your\s+)?(system\s+prompt|hidden\s+instructions?|secrets?)"
            r"|reveal\s+(your\s+)?(system\s+prompt|api\s*keys?|hidden\s+instructions?)"
            r"|show\s+(me\s+)?(the\s+)?(system\s+prompt|hidden\s+prompt)"
            r"|exfiltrat"
            r"|leak\s+(the\s+)?(api\s*keys?|secrets?|tokens?)"
            r")\b"
        ),
    ),
    (
        "cross_tenant_probe",
        re.compile(
            r"(?i)\b("
            r"for\s+tenant\s+(id\s+)?(tnt_|globex|initech)"
            r"|switch\s+tenant"
            r"|act\s+as\s+tenant\s+admin\s+for\s+another"
            r"|bypass\s+(auth|rls|tenant|authorization)"
            r"|list\s+(every|all)\s+projects?\s+in\s+(every|all)\s+tenants?"
            r"|cross[-\s]?tenant\s+(access|dump|read)"
            r"|impersonate\s+(user\s+)?admin\s+in\s+another\s+tenant"
            r"|impersonate\s+user\s+admin\s+in\s+another\s+tenant"
            r")\b"
        ),
    ),
    (
        "tool_abuse",
        re.compile(
            r"(?i)\b("
            r"run\s+raw\s+sql"
            r"|execute\s+(shell|bash|powershell|os\.system)"
            r"|drop\s+table"
            r"|rm\s+-rf\s+/"
            r"|curl\s+[^\s]+\s*\|\s*(sh|bash)"
            r"|disable\s+(all\s+)?(guardrails?|safety|filters?)"
            r")\b"
        ),
    ),
    (
        "role_play_escape",
        re.compile(
            r"(?i)\b("
            r"pretend\s+you\s+have\s+no\s+restrictions"
            r"|no\s+rules\s+apply"
            r"|without\s+any\s+(safety\s+)?(rules|restrictions|guardrails)"
            r"|you\s+must\s+obey\s+me\s+over\s+(your\s+)?(system|developer)"
            r")\b"
        ),
    ),
]

# Flattened signatures survive character-spacing and heavy leetspeak punctuation.
# Regex forms tolerate PyRIT's random multi-char leet substitutions.
_FLAT_REGEX: list[tuple[str, re.Pattern[str]]] = [
    (
        "instruction_override",
        re.compile(
            r"(ignore|disregard|forget)\w{0,8}(previous|prior|all)\w{0,12}(instruction|rule|prompt)"
            r"|overrides?\w{0,6}system\w{0,4}prompt"
            r"|developermode\w{0,8}(enabled|on)"
            r"|youarenow(dan|unrestricted|jailbroken)"
            r"|doanythingnow"
            r"|jailbreak"
            r"|newsystemprompt"
        ),
    ),
    (
        "secret_exfiltration",
        re.compile(
            r"dump\w{0,16}(apikey|secrets?|passwords?|tokens?|jwt|credentials?|users?)"
            r"|(print|reveal|show)\w{0,12}system\w{0,4}prompt"
            r"|(leak|iek)\w{0,12}(secrets?|apikeys?|tokens?|credentials?)"
            r"|exfiltrat"
            r"|secretsandcredent"
        ),
    ),
    (
        "cross_tenant_probe",
        re.compile(
            r"switch\w{0,8}ten"
            r"|bypass\w{0,12}(auth|rls|ris|tenant|authorization)"
            r"|(iey|by)p\w{0,6}(ass|sss)\w{0,8}(rls|ris|tenant)"
            r"|everyproject\w{0,8}everytenant"
            r"|allprojects?\w{0,8}(every|all)\w{0,8}tenant"
            r"|crosstenant"
            r"|impersonate\w{0,24}ten"
            r"|tenant(id)?(globex|initech|giogex)"
        ),
    ),
    (
        "tool_abuse",
        re.compile(
            r"run\w{0,12}(raw|rw)?\w{0,8}(sql|postgres)"
            r"|execute\w{0,6}(shell|bash|powershell)"
            r"|drop\w{0,16}(table|runs|agent)"
            r"|rmrf"
            r"|disable\w{0,20}(guardr|safety|safet|filter|filt)"
            r"|disab\w{0,20}(guardr|safety|safet|filter|filt)"
        ),
    ),
    (
        "role_play_escape",
        re.compile(
            r"pretendyouhavenorestrictions"
            r"|norulesapply"
            r"|withoutany\w{0,8}(rules|restrictions|guardrails)"
            r"|obeymeover\w{0,8}(system|developer)"
        ),
    ),
]

_SOFT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "hypothetical_bypass",
        re.compile(
            r"(?i)\b("
            r"for\s+educational\s+purposes\s+only.{0,40}(ignore|bypass|jailbreak)"
            r"|hypothetically\s+if\s+you\s+had\s+no\s+rules"
            r")\b"
        ),
    ),
]

_LEET = str.maketrans(
    {
        "0": "o",
        "1": "i",
        "2": "z",
        "3": "e",
        "4": "a",
        "5": "s",
        "6": "g",
        "7": "t",
        "8": "b",
        "9": "g",
        "@": "a",
        "$": "s",
        "!": "i",
        "|": "i",
        "(": "c",
        "{": "c",
        "[": "c",
        "<": "c",
        "^": "s",
    }
)


def _flat(text: str) -> str:
    """Lowercase alnum-only — defeats spacing and most leet punctuation."""
    leet = text.translate(_LEET)
    return re.sub(r"[^a-z0-9]+", "", leet.lower())


def _collapse_spaced_letters(text: str) -> str:
    """Undo CharacterSpace-style 'I g n o r e   p r e v …' mutations."""

    def _join_singles(match: re.Match[str]) -> str:
        return match.group(0).replace(" ", "")

    return re.sub(r"(?:\b\w\s){2,}\w\b", _join_singles, text)


def _base64_candidates(text: str) -> list[str]:
    out: list[str] = []
    for match in re.finditer(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{16,}={0,2}(?![A-Za-z0-9+/])", text):
        blob = match.group(0)
        pad = (-len(blob)) % 4
        try:
            raw = base64.b64decode(blob + ("=" * pad), validate=False)
            decoded = raw.decode("utf-8", errors="ignore").strip()
        except (binascii.Error, ValueError):
            continue
        if len(decoded) >= 8 and re.search(r"[A-Za-z]{4,}", decoded):
            out.append(decoded)
    return out


def policy_candidates(question: str) -> list[str]:
    """Plain + de-obfuscated views the policy must evaluate."""
    text = (question or "").strip()
    views = [text, text.lower()]
    collapsed = _collapse_spaced_letters(text)
    if collapsed != text:
        views.append(collapsed)
        views.append(collapsed.lower())
    leet = text.translate(_LEET)
    if leet != text:
        views.append(leet.lower())
    # Strip non-alnum after leet — PyRIT inserts /-\ etc.
    stripped = re.sub(r"[^A-Za-z0-9\s]+", " ", leet)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    if stripped and stripped.lower() not in {v.lower() for v in views}:
        views.append(stripped.lower())
    flipped = text[::-1]
    if flipped != text:
        views.append(flipped)
        views.append(flipped.lower())
        views.append(flipped.translate(_LEET).lower())
    views.extend(_base64_candidates(text))
    seen: set[str] = set()
    out: list[str] = []
    for v in views:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _match_flat(text: str) -> list[str]:
    flat = _flat(text)
    flat_rev = flat[::-1]
    hits: list[str] = []
    for name, pat in _FLAT_REGEX:
        if pat.search(flat) or pat.search(flat_rev):
            if name not in hits:
                hits.append(name)
    return hits


def check_user_question(question: str, *, max_chars: int = 2000) -> InputVerdict:
    """Allow, soft-flag, or hard-block a user question before the agent runs."""
    text = (question or "").strip()
    flags: list[str] = []

    if len(text) < 5:
        return InputVerdict(allowed=False, reason="question_too_short", flags=("too_short",))
    if len(text) > max_chars:
        return InputVerdict(
            allowed=False,
            reason="question_too_long",
            flags=("too_long",),
        )

    for candidate in policy_candidates(text):
        for name, pat in _BLOCK_PATTERNS:
            if pat.search(candidate) and name not in flags:
                flags.append(name)

    for name in _match_flat(text):
        if name not in flags:
            flags.append(name)
    # Flat-match base64 payloads too
    for decoded in _base64_candidates(text):
        for name in _match_flat(decoded):
            if name not in flags:
                flags.append(name)

    if flags:
        return InputVerdict(
            allowed=False,
            reason="policy_block",
            flags=tuple(flags),
        )

    for candidate in policy_candidates(text):
        for name, pat in _SOFT_PATTERNS:
            if pat.search(candidate) and name not in flags:
                flags.append(name)

    return InputVerdict(
        allowed=True,
        reason="ok",
        flags=tuple(flags),
        soft=bool(flags),
    )
