"""Decision-support wording guard for Cortex-written text (AGENTS.md rule 9, SPEC §9 ⑤ guardrails).

The prompts ask for "possible", "consider", "check"; Phase 8 still saw headlines like "infection likely". `soften`
rewrites firm diagnostic wording into hedged decision-support wording, and `violations` lists what is still not
acceptable afterwards (callers reject the card: the deterministic template is shown instead).

    "infection likely" → "infection possible" · "likely UTI" → "possible UTI" · "is likely due to" → "may be due to"
    "probably" → "possibly" · "has early sepsis" → "may have early sepsis" · "indicates infection" → "could fit possible
    infection" · "a diagnosis of UTI" → "possible UTI" · a bare condition name with no hedge before it ("Sepsis
    risk rising" in a headline) → "Possible sepsis risk rising"

Verbatim care-plan quotes are never passed through here (they must stay verbatim).
"""

from __future__ import annotations

import re

CONDITIONS = (
    r"urosepsis|sepsis|septic|utis?|urinary tract infections?|infections?|pneumonia|delirium|stroke|dehydration|"
    r"chf exacerbation|heart failure exacerbation|fluid overload|pulmonary edema|hypoxia|respiratory failure|aspiration"
)
_COND = rf"(?:(?:early|acute|new|worsening|possible)\s+)?(?:{CONDITIONS})\b"
_HEDGE = re.compile(
    r"\b(possible|possibly|could|may|might|consider|check(?:ing)? for|signs? of|risk of|rule out|history of|hx|"
    r"concern(?:s)? for|watch(?:ing)? for|screen(?:ing)? for|no|not|without|prone to|presenting as|past|prior|recurrent)\b",
    re.IGNORECASE,
)
_HEDGE_AFTER = re.compile(
    r"^\s*(?:is\s+|are\s+)?(possible|possibly|risk|concern|screen|not\b|unlikely|ruled|history|hx\b)", re.I
)
_NEGATED = re.compile(
    r"\b(not|no|without|never|cannot|can't|isn't|is not|does not|doesn't)\b[^.;]{0,25}$", re.IGNORECASE
)

# (pattern, replacement), applied in order. Case of the first letter is preserved by `_sub`.
REWRITES: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"\b(is|are)\s+(?:most\s+|very\s+|highly\s+|quite\s+)?likely\s+(due to|from|caused by|related to|"
            r"secondary to|driven by)\b",
            re.I,
        ),
        r"may be \2",
    ),
    (re.compile(r"\b(is|are)\s+(?:most\s+|very\s+|highly\s+|quite\s+)?likely\s+to\b", re.I), "may"),
    (re.compile(r"\b(is|are)\s+(?:most\s+|very\s+|highly\s+|quite\s+)?likely\b", re.I), r"\1 possible"),
    (re.compile(r"\b(?:most\s+|very\s+|highly\s+|quite\s+)?likely\s+to\b", re.I), "may"),
    (re.compile(r"\b(?:most\s+|very\s+|highly\s+|quite\s+)?likely\b", re.I), "possible"),
    (re.compile(r"\bprobably\b", re.I), "possibly"),
    (re.compile(r"\bprobable\b", re.I), "possible"),
    (re.compile(r"\b(?:definitely|certainly|undoubtedly)\s+", re.I), ""),
    (
        re.compile(r"\b(?:suggests?|supports?|indicates?|confirms?)?\s*an?\s+diagnosis\s+of\b", re.I),
        "could fit possible",
    ),
    (re.compile(r"\bdiagnosis\s+of\b", re.I), "possible"),
    (re.compile(r"\bdiagnosed\s+with\b", re.I), "history of"),
    (
        re.compile(rf"\b(?:confirms?|indicates?|proves?|means?|shows?)\s+(?:an?\s+)?(?=(?:{_COND}))", re.I),
        "could fit possible ",
    ),
    (
        re.compile(rf"\b(?:has|is having|is developing|is in|suffering from)\s+(?=(?:an?\s+)?(?:{_COND}))", re.I),
        "may have ",
    ),
    (re.compile(r"\b(is|are)\s+(?=(?:septic|dehydrated|hypoxic|delirious)\b)", re.I), r"may be "),
]
FORBIDDEN = [
    (re.compile(r"\blikely\b", re.I), "likely"),
    (re.compile(r"\bprobabl[ey]\b", re.I), "probable"),
    (re.compile(r"\bprevents?\s+death", re.I), "prevents death"),
    (re.compile(r"\breplaces?\s+(?:staff|nurses?|aides?)", re.I), "replaces staff"),
]
_DIAG = re.compile(r"\bdiagnos(?:is|es|ed|e|ing)\b", re.I)
_COND_RE = re.compile(_COND, re.I)


def _sub(pattern: re.Pattern, repl: str, text: str) -> str:
    def one(m: re.Match) -> str:
        out = m.expand(repl)
        if m.group(0)[:1].isupper() and out[:1].islower():
            out = out[:1].upper() + out[1:]
        return out

    return pattern.sub(one, text)


def hedge_conditions(text: str) -> str:
    """Put "possible" before a condition name that has no hedge word in the few words before it."""
    out, last = [], 0
    for m in _COND_RE.finditer(text):
        before = text[max(0, m.start() - 30) : m.start()]
        after = text[m.end() : m.end() + 16]
        if _HEDGE.search(before) or _HEDGE_AFTER.search(after) or m.group(0).lower().startswith("possible"):
            continue
        out.append(text[last : m.start()])
        word = "Possible " if (m.start() == 0 or text[m.start() - 2 : m.start()] in (". ", ": ")) else "possible "
        cond = m.group(0)
        out.append(word + (cond[:1].lower() + cond[1:] if word[0] == "P" and not cond[:3].isupper() else cond))
        last = m.end()
    out.append(text[last:])
    return "".join(out)


def soften(text: str, *, hedge: bool = True) -> str:
    """Firm diagnostic wording → decision-support wording (idempotent)."""
    if not text:
        return text
    t = text
    for pattern, repl in REWRITES:
        t = _sub(pattern, repl, t)
    if hedge:
        t = hedge_conditions(t)
    t = re.sub(r"\bpossible\s+possible\b", "possible", t, flags=re.I)
    t = re.sub(r"\bmay have possible\b", "may have", t, flags=re.I)
    return re.sub(r"\s{2,}", " ", t).strip()


def violations(text: str) -> list[str]:
    """Wording that is still not decision-support language."""
    found = [name for pattern, name in FORBIDDEN if pattern.search(text or "")]
    for m in _DIAG.finditer(text or ""):
        if not _NEGATED.search(text[: m.start()]):
            found.append("diagnosis")
            break
    return found
