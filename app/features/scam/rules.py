"""The red-flag engine. Deterministic, auditable, and the reason a score is explainable.

Every rule can fire two ways. Most fire on a **pattern** — a regex over the
transcribed text, in English, Hindi and the Hinglish people actually type. A few
fire on a **check** — a Python predicate, because "promises more than 15% a year"
and "claims to be an adviser but shows no registration number" are comparisons
against fields and a *missing* value, which no regex can express.

Both routes produce the same thing: a `FiredRule` carrying its weight, the text
that triggered it, and the sentence the user reads. Nothing scores without being
able to say why.

Exclusions run before patterns. A real bank SMS saying "never share your OTP with
anyone" contains every word the OTP rule looks for, and must score zero on it —
see the long note at the top of `data/scam_rules.yaml`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from app.features.scam.schema import ExtractedMessage

__all__ = [
    "FiredRule",
    "Rule",
    "RULES_PATH",
    "evaluate",
    "load_rules",
    "rule_score",
    "searchable_text",
]

RULES_PATH = Path(__file__).resolve().parents[3] / "data" / "scam_rules.yaml"

MAX_RULE_SCORE = 100

# A promised return above this, annualised, is not an investment product.
# A good Indian equity fund does about 12% a year.
IMPLAUSIBLE_ANNUAL_RETURN_PCT = 15.0

_FLAGS = re.IGNORECASE | re.UNICODE


@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    weight: int
    title: str
    sentence: str
    patterns: tuple[re.Pattern[str], ...] = ()
    excludes: tuple[re.Pattern[str], ...] = ()
    check: str | None = None


@dataclass(frozen=True, slots=True)
class FiredRule:
    """One red flag, with the evidence that produced it."""

    id: str
    title: str
    weight: int
    evidence: str
    sentence: str

    @property
    def user_sentence(self) -> str:
        """The rule's explanation with `{match}` filled in from the evidence."""
        if "{match}" not in self.sentence:
            return self.sentence
        quoted = f'"{self.evidence}"' if self.evidence else "this"
        return self.sentence.replace("{match}", quoted)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


@lru_cache
def load_rules(path: Path | None = None) -> tuple[Rule, ...]:
    """Parse and compile the YAML. Cached; call `load_rules.cache_clear()` after edits."""
    source = path or RULES_PATH
    document = yaml.safe_load(source.read_text(encoding="utf-8"))

    rules: list[Rule] = []
    for entry in document["rules"]:
        rules.append(
            Rule(
                id=entry["id"],
                weight=int(entry["weight"]),
                title=entry["title"],
                sentence=" ".join(entry["sentence"].split()),
                patterns=tuple(re.compile(p, _FLAGS) for p in entry.get("patterns", [])),
                excludes=tuple(re.compile(p, _FLAGS) for p in entry.get("exclude", [])),
                check=entry.get("check"),
            )
        )
    return tuple(rules)


# ---------------------------------------------------------------------------
# Field checks — the logic a regex cannot express
# ---------------------------------------------------------------------------


def _check_implausible_return(message: ExtractedMessage) -> str | None:
    """Any return promise that beats reality, annualised over the stated period."""
    pct = message.claimed_return_pct
    if pct is None or pct <= 0:
        return None

    days = message.claimed_period_days
    if days is None or days <= 0:
        # No period stated: treat the figure as annual, which is the charitable read.
        return f"{pct:g}% return" if pct > IMPLAUSIBLE_ANNUAL_RETURN_PCT else None

    annualised = pct * (365.0 / days)
    if annualised <= IMPLAUSIBLE_ANNUAL_RETURN_PCT:
        return None

    if days <= 1:
        period = "a day"
    elif days <= 7:
        period = f"{days} days"
    elif days <= 31:
        period = "a month"
    else:
        period = f"{days} days"
    return f"{pct:g}% in {period} — that is {annualised:,.0f}% a year"


_ADVISORY_CLAIM = re.compile(
    r"\b(SEBI|RBI|IRDAI|AMFI)\b|\b(registered|certified|licensed|authorised|authorized)\b"
    r".{0,20}\b(advisor|adviser|analyst|broker|expert|consultant|planner|company|firm)\b"
    r"|\b(investment|trading|portfolio|wealth)\s+(advisor|adviser|expert|manager|consultant)\b",
    _FLAGS,
)


def _check_no_registration(message: ExtractedMessage) -> str | None:
    """Claims regulatory standing, shows no number. Silence is the finding."""
    if message.registration_number:
        return None
    haystack = f"{message.claimed_entity or ''} {message.message_text}"
    found = _ADVISORY_CLAIM.search(haystack)
    if not found:
        return None
    return (message.claimed_entity or found.group(0)).strip()


# A UPI handle whose local part looks like a person: a name, or a name plus digits,
# rather than a business name. Deliberately conservative — a false positive here
# accuses a real merchant.
_PERSONAL_UPI = re.compile(
    r"^(?P<local>[a-z]+(?:[._-][a-z]+)?\d{0,4})@(?P<psp>okaxis|okhdfcbank|okicici|oksbi|ybl|paytm|apl|upi|axl|ibl)$",
    re.IGNORECASE,
)
_BUSINESS_WORDS = re.compile(
    r"(pvt|ltd|limited|llp|enterprise|traders?|store|shop|mart|services?|solutions?"
    r"|india|official|merchant|payee|billdesk|razorpay|payu)",
    _FLAGS,
)

# A bank telling you money has *already moved* names the other party's VPA as a
# matter of record. That is not a request for payment, and the rule is about being
# asked to pay a stranger. Found live: a genuine SBI debit alert quoting
# "merchant@okhdfcbank" fired this rule and put a 🚩 on the user's own bank message.
_COMPLETED_TRANSACTION = re.compile(
    r"\b(debited|credited|withdrawn|deposited|has\s+been\s+(paid|transferred|sent)"
    r"|UPI\s*Ref|txn\s*(id|ref)|transaction\s+(id|ref|successful)|statement|balance)\b",
    _FLAGS,
)

# Being asked to send money — the thing this rule actually looks for.
_PAYMENT_REQUEST = re.compile(
    r"\b(pay|send|transfer|deposit|remit|scan|भेज|भुगतान|जमा)\w*\b",
    _FLAGS,
)


def _check_personal_payment_handle(message: ExtractedMessage) -> str | None:
    """A personal UPI handle you are being asked to *pay*.

    Both halves are required. A handle alone is not a red flag — every transaction
    alert in India contains one.
    """
    text = message.message_text or ""
    if _COMPLETED_TRANSACTION.search(text) and not _PAYMENT_REQUEST.search(text):
        return None

    for handle in message.payment_handles:
        cleaned = handle.strip()
        match = _PERSONAL_UPI.match(cleaned)
        if match and not _BUSINESS_WORDS.search(match.group("local")):
            return cleaned
    return None


_SHORTENERS = frozenset(
    {
        "bit.ly",
        "tinyurl.com",
        "goo.gl",
        "t.co",
        "is.gd",
        "cutt.ly",
        "rb.gy",
        "shorturl.at",
        "rebrand.ly",
        "tiny.cc",
        "ow.ly",
        "surl.li",
        "clck.ru",
    }
)

# Domains a lookalike would imitate. Not exhaustive — it does not need to be, it
# needs to cover what a scam actually impersonates in India.
_PROTECTED_DOMAINS = (
    "sbi.co.in",
    "onlinesbi.sbi",
    "hdfcbank.com",
    "icicibank.com",
    "axisbank.com",
    "kotak.com",
    "pnbindia.in",
    "bankofbaroda.in",
    "canarabank.com",
    "unionbankofindia.co.in",
    "paytm.com",
    "phonepe.com",
    "zerodha.com",
    "groww.in",
    "upstox.com",
    "angelone.in",
    "rbi.org.in",
    "sebi.gov.in",
    "npci.org.in",
)


def _levenshtein(a: str, b: str) -> int:
    """Edit distance. Small enough to write, and it avoids a dependency."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb))
            )
        previous = current
    return previous[-1]


def _host_of(url: str) -> str:
    stripped = re.sub(r"^[a-z]+://", "", url.strip(), flags=re.IGNORECASE)
    host = stripped.split("/", 1)[0].split("?", 1)[0].lower()
    return host.removeprefix("www.")


def _check_suspicious_link(message: ExtractedMessage) -> str | None:
    """A shortener hides the destination; a lookalike domain impersonates one."""
    for url in message.links:
        host = _host_of(url)
        if not host:
            continue
        if host in _SHORTENERS:
            return host
        for protected in _PROTECTED_DOMAINS:
            if host == protected:
                break
            if 0 < _levenshtein(host, protected) <= 2:
                return f"{host} (a near-copy of {protected})"
    return None


_AUTHORITY_CLAIM = re.compile(
    # "registered" belongs to `no_registration`, not here — see the note in the YAML.
    r"\b(RBI|SEBI|IRDAI|NPCI)\s*(approved|certified|authorised|authorized|backed)\b"
    r"|\b(govt|government)\s+of\s+india\b|\bsarkari\b|\bसरकारी\b",
    _FLAGS,
)
_CHECKABLE_SOURCE = re.compile(r"\b[\w.-]+\.(gov\.in|nic\.in|org\.in)\b", _FLAGS)


def _check_fake_authority(message: ExtractedMessage) -> str | None:
    """An authority claim with nothing checkable behind it."""
    found = _AUTHORITY_CLAIM.search(message.message_text)
    if not found:
        return None
    if message.registration_number:
        return None
    haystack = message.message_text + " " + " ".join(message.links)
    if _CHECKABLE_SOURCE.search(haystack):
        return None
    return found.group(0).strip()


def _check_unsolicited_contact(message: ExtractedMessage) -> str | None:
    """Only fires with a *named* sender absent or a group add — never on its own.

    Kept narrow on purpose. "Unknown sender" describes every legitimate transaction
    alert ever sent, and this rule's 8 points are not worth teaching a user to
    distrust their own bank.
    """
    if message.sender_is_group and not message.sender_name:
        return "in a group you were added to"
    return None


CHECKS: dict[str, Callable[[ExtractedMessage], str | None]] = {
    "implausible_return": _check_implausible_return,
    "no_registration": _check_no_registration,
    "personal_payment_handle": _check_personal_payment_handle,
    "suspicious_link": _check_suspicious_link,
    "fake_authority": _check_fake_authority,
    "unsolicited_contact": _check_unsolicited_contact,
}


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _excluded(rule: Rule, text: str) -> bool:
    return any(pattern.search(text) for pattern in rule.excludes)


def _matched_text(rule: Rule, text: str) -> str | None:
    for pattern in rule.patterns:
        found = pattern.search(text)
        if found:
            return " ".join(found.group(0).split())[:120]
    return None


def searchable_text(message: ExtractedMessage) -> str:
    """The text rules are matched against: the message *and* its links.

    Links are part of what a message says. Leaving them out meant a genuine
    "Government of India scheme" notice could not be cleared by the `pmkisan.gov.in`
    link sitting right next to it, because the exclusion had nothing to see.
    """
    if not message.links:
        return message.message_text or ""
    return (message.message_text or "") + "\n" + " ".join(message.links)


def evaluate(message: ExtractedMessage, rules: tuple[Rule, ...] | None = None) -> list[FiredRule]:
    """Run every rule. Returns what fired, heaviest first.

    A rule fires at most once and contributes its weight once, however many of its
    patterns match — twenty ways of saying "guaranteed" is still one red flag.
    """
    active = rules if rules is not None else load_rules()
    text = searchable_text(message)

    fired: list[FiredRule] = []
    for rule in active:
        if _excluded(rule, text):
            continue

        evidence = _matched_text(rule, text)
        if evidence is None and rule.check:
            evidence = CHECKS[rule.check](message)
        if evidence is None:
            continue

        fired.append(
            FiredRule(
                id=rule.id,
                title=rule.title,
                weight=rule.weight,
                evidence=evidence,
                sentence=rule.sentence,
            )
        )

    fired.sort(key=lambda f: f.weight, reverse=True)
    return fired


def rule_score(fired: list[FiredRule]) -> int:
    """`min(100, Σ weights)` — §4, exactly."""
    return min(MAX_RULE_SCORE, sum(f.weight for f in fired))
