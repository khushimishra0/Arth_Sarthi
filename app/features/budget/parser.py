"""Reading a month's budget out of whatever the user typed.

Two paths, one output. The guided flow asks one category at a time and each answer
is a bare number; the free-text path takes `income 25000 rent 8000 food 4000` in any
order, in Hinglish, with `8k` for 8000. Both produce a `Budget`.

No AI here on purpose. This is pattern matching over a closed set of eight
categories, and a model would add latency, cost and a way to be creatively wrong
about somebody's rent. §7 asks for a parser; a parser is what this is.

**When it is not sure, it says so.** `parse` returns what it understood *and* what it
could not place, and the channel falls back to the guided flow rather than acting on
a half-read line. Silently dropping a number the user typed would produce a chart
that does not match their month, and they would have no way to tell.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.features.budget.schema import Budget, load_benchmarks

__all__ = ["ParseResult", "looks_like_budget_input", "parse", "parse_amount"]

# What people actually type for each category, in English, Hindi and Hinglish.
# Order matters within a category only for the evidence string; matching is by
# longest alias first so "phone bill" beats "phone".
_ALIASES: dict[str, tuple[str, ...]] = {
    "income": (
        "income", "salary", "kamai", "kamaai", "earning", "earnings", "pay",
        "tankha", "tankha", "आय", "वेतन", "कमाई",
    ),
    "rent": ("rent", "kiraya", "kiraaya", "किराया", "house", "housing", "room"),
    "food": (
        "food", "khana", "khaana", "ration", "grocery", "groceries", "sabzi",
        "खाना", "राशन", "किराना",
    ),
    "travel": (
        "travel", "transport", "petrol", "diesel", "bus", "auto", "train",
        "fuel", "commute", "yatra", "यात्रा", "पेट्रोल", "किराया-भाड़ा",
    ),
    "phone": (
        "phone", "mobile", "recharge", "data", "internet", "sim",
        "फोन", "मोबाइल", "रिचार्ज",
    ),
    "medical": (
        "medical", "medicine", "dawai", "davai", "doctor", "hospital", "health",
        "दवाई", "दवा", "इलाज",
    ),
    "education": (
        "education", "school", "fees", "fee", "tuition", "college", "padhai",
        "स्कूल", "फीस", "पढ़ाई", "शिक्षा",
    ),
    "emi": (
        "emi", "loan", "instalment", "installment", "qist", "kist", "karza",
        "karz", "ईएमआई", "कर्ज", "किस्त",
    ),
    "other": ("other", "others", "misc", "miscellaneous", "baaki", "बाकी", "अन्य"),
}

# Longest first, so "phone bill" is not matched as "phone" and "bill" left dangling.
_ALIAS_TO_KEY: tuple[tuple[str, str], ...] = tuple(
    sorted(
        ((alias, key) for key, aliases in _ALIASES.items() for alias in aliases),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )
)

_LABEL_PATTERN = "|".join(re.escape(alias) for alias, _ in _ALIAS_TO_KEY)

# label ... amount   e.g. "rent 8000", "rent: ₹8,000", "rent = 8k", "kiraya - 8 hazaar"
_PAIR = re.compile(
    rf"(?P<label>{_LABEL_PATTERN})"
    r"\s*(?:is|:|=|-|–|—|ka|के|है)?\s*"
    # The suffix needs a trailing `\b` for the same reason as `_LOOSE_NUMBER`:
    # "rent 2 kids" must not become ₹2,000 of rent.
    r"(?P<amount>₹?\s*\d[\d,]*(?:\.\d+)?(?:\s*(?:k|K|hazaar|hazar|हज़ार|हजार|lakh|लाख)\b)?)",
    re.IGNORECASE | re.UNICODE,
)

# A bare number, for the guided flow where the category is already known.
_BARE_AMOUNT = re.compile(
    r"^\s*₹?\s*(\d[\d,]*(?:\.\d+)?)\s*(k|K|hazaar|hazar|हज़ार|हजार|lakh|लाख)?\s*$",
    re.UNICODE,
)

_SKIP_WORDS = frozenset(
    {"skip", "none", "no", "nil", "zero", "0", "nahi", "nahin", "नहीं", "कुछ नहीं", "-"}
)

_MULTIPLIERS: dict[str, int] = {
    "k": 1_000,
    "hazaar": 1_000,
    "hazar": 1_000,
    "हज़ार": 1_000,
    "हजार": 1_000,
    "lakh": 100_000,
    "लाख": 100_000,
}


@dataclass(frozen=True, slots=True)
class ParseResult:
    """What was understood, and honestly what was not."""

    budget: Budget
    matched: dict[str, float] = field(default_factory=dict)
    unrecognised_numbers: tuple[str, ...] = ()
    duplicate_labels: tuple[str, ...] = ()

    @property
    def has_income(self) -> bool:
        return self.budget.income > 0

    @property
    def is_confident(self) -> bool:
        """Whether the channel may act on this without asking.

        Requires an income, at least one expense, and nothing left over that looked
        like a number we could not place. §7: falls back to the guided flow if
        parsing is ambiguous.
        """
        return (
            self.has_income
            and bool(self.budget.categories)
            and not self.unrecognised_numbers
            and not self.duplicate_labels
        )

    @property
    def ambiguity(self) -> str:
        """One sentence naming what stopped it being confident."""
        if not self.has_income:
            return "I could not find your monthly income in that."
        if self.duplicate_labels:
            listed = ", ".join(self.duplicate_labels)
            return f"You gave two different figures for: {listed}."
        # Reported before "no expenses": unlabelled numbers are the actionable
        # detail, and "I found no expenses" is confusing when the user plainly
        # typed some.
        if self.unrecognised_numbers:
            listed = ", ".join(self.unrecognised_numbers)
            return f"I could not tell what these numbers were for: {listed}."
        if not self.budget.categories:
            return "I found your income but no expenses."
        return ""


def parse_amount(text: str) -> float | None:
    """`8000`, `8,000`, `₹8000`, `8k`, `8 hazaar`, `1.5 lakh` → a number.

    Returns `None` for anything that is not an amount, including the skip words —
    the caller decides what skipping means.
    """
    if not text:
        return None
    cleaned = text.strip().lower()
    if cleaned in _SKIP_WORDS:
        return None

    found = _BARE_AMOUNT.match(cleaned)
    if not found:
        return None

    value = float(found.group(1).replace(",", ""))
    suffix = (found.group(2) or "").lower()
    return value * _MULTIPLIERS.get(suffix, 1)


def _amount_from_fragment(fragment: str) -> float | None:
    """The amount half of a matched pair, which may carry ₹ and a multiplier."""
    return parse_amount(fragment.replace("₹", "").strip())


def _key_for_label(label: str) -> str:
    lowered = label.lower()
    for alias, key in _ALIAS_TO_KEY:
        if alias.lower() == lowered:
            return key
    return "other"


def parse(text: str) -> ParseResult:
    """Read a whole budget from one free-text line.

    >>> r = parse("income 25000 rent 8000 food 4000 travel 3000")
    >>> r.budget.income, r.budget.categories["rent"], r.is_confident
    (25000.0, 8000.0, True)
    """
    benchmarks = load_benchmarks()
    known = set(benchmarks.known_categories())

    income = 0.0
    categories: dict[str, float] = {}
    matched: dict[str, float] = {}
    duplicates: list[str] = []

    consumed: list[tuple[int, int]] = []

    for found in _PAIR.finditer(text or ""):
        amount = _amount_from_fragment(found.group("amount"))
        if amount is None:
            continue

        key = _key_for_label(found.group("label"))
        consumed.append(found.span())

        if key == "income":
            if income and abs(income - amount) > 0.01:
                duplicates.append("income")
            income = amount
            matched["income"] = amount
            continue

        if key not in known:
            key = "other"

        if key in categories and abs(categories[key] - amount) > 0.01:
            duplicates.append(key)
        # Same category twice with the same figure is a repetition, not a conflict.
        categories[key] = amount
        matched[key] = amount

    unrecognised = _numbers_outside(text or "", consumed)

    return ParseResult(
        budget=Budget(income=income, categories=categories),
        matched=matched,
        unrecognised_numbers=unrecognised,
        duplicate_labels=tuple(dict.fromkeys(duplicates)),
    )


# A number with no label attached to it. Deliberately narrow: four digits or more,
# or a k/lakh suffix — so "2 kids", "3 months" and a phone number's area code do not
# each become a mystery expense.
_LOOSE_NUMBER = re.compile(
    # `\b` after the suffix so "2 kids" is not read as "2 k" → ₹2,000. Without it,
    # any word starting with k, and "lakhon", become mystery expenses.
    r"(?<![\d.,])(?:₹\s*)?(\d{4,}|\d+\s*(?:k|K|hazaar|हज़ार|lakh|लाख)\b)(?![\d.,])",
    re.UNICODE,
)


def _numbers_outside(text: str, consumed: list[tuple[int, int]]) -> tuple[str, ...]:
    """Numbers the parser could not attach to any category.

    These are what trigger the fallback to the guided flow. A user who typed
    "income 25000 8000 4000" meant something by those two figures, and guessing
    which categories they were would produce a chart of a month they did not have.
    """
    leftovers: list[str] = []
    for found in _LOOSE_NUMBER.finditer(text):
        start, end = found.span()
        if any(c_start <= start and end <= c_end for c_start, c_end in consumed):
            continue
        leftovers.append(found.group(0).strip())
    return tuple(leftovers)


def looks_like_budget_input(text: str) -> bool:
    """Whether this line is worth handing to `parse` at all.

    Stricter than `channels.base.looks_like_budget_text`, which only decides whether
    to *offer* the budget coach. This decides whether to run it.
    """
    if not text:
        return False
    result = parse(text)
    return result.has_income or len(result.budget.categories) >= 2
