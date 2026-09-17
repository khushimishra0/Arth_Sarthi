"""Rupee formatting, Indian conventions.

The design rules in the plan are explicit: amounts use Indian comma grouping
(Rs 1,00,000 — not 100,000), and large numbers are said in "lakh" and "crore",
never "100K" or "0.1M". A user who reads "0.1M" learns nothing.
"""

from __future__ import annotations

__all__ = ["format_inr", "in_words", "indian_commas"]

LAKH = 100_000
CRORE = 10_000_000


def indian_commas(amount: float | int, decimals: int = 0) -> str:
    """Group digits the Indian way: last three, then pairs.

    >>> indian_commas(100000)
    '1,00,000'
    >>> indian_commas(1234567.891, decimals=2)
    '12,34,567.89'
    """
    sign = "-" if amount < 0 else ""
    fixed = f"{abs(amount):.{decimals}f}"
    whole, _, frac = fixed.partition(".")

    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        # Pairs from the right, e.g. "1234" -> "12,34"
        pairs = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        if head:
            pairs.insert(0, head)
        whole = ",".join([*pairs, tail])

    return f"{sign}{whole}.{frac}" if frac else f"{sign}{whole}"


def format_inr(amount: float | int, decimals: int = 0) -> str:
    """Rupee string with the symbol attached.

    >>> format_inr(97050)
    '₹97,050'
    """
    return f"₹{indian_commas(amount, decimals)}"


def in_words(amount: float | int) -> str:
    """Round figure in the units people actually speak.

    >>> in_words(750000)
    '₹7.5 lakh'
    >>> in_words(12000)
    '₹12,000'
    """
    magnitude = abs(amount)
    if magnitude >= CRORE:
        value = amount / CRORE
        unit = "crore"
    elif magnitude >= LAKH:
        value = amount / LAKH
        unit = "lakh"
    else:
        return format_inr(amount)

    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"₹{text} {unit}"
