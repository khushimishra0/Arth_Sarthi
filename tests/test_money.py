"""Rupee formatting tests. Small module, but it touches every user-facing number."""

import pytest

from app.utils.money import format_inr, in_words, indian_commas


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (0, "0"),
        (500, "500"),
        (1_000, "1,000"),
        (99_999, "99,999"),
        (100_000, "1,00,000"),  # not 100,000
        (127_992, "1,27,992"),
        (1_234_567, "12,34,567"),
        (10_000_000, "1,00,00,000"),
        (-5_000, "-5,000"),
        (-100_000, "-1,00,000"),
    ],
)
def test_indian_comma_grouping(amount, expected):
    assert indian_commas(amount) == expected


def test_decimals_are_grouped_correctly():
    assert indian_commas(1_234_567.891, decimals=2) == "12,34,567.89"


def test_format_inr_attaches_the_symbol():
    assert format_inr(97_050) == "₹97,050"


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (12_000, "₹12,000"),
        (99_999, "₹99,999"),
        (100_000, "₹1 lakh"),
        (144_000, "₹1.44 lakh"),
        (750_000, "₹7.5 lakh"),
        (10_000_000, "₹1 crore"),
        (25_000_000, "₹2.5 crore"),
    ],
)
def test_lakh_and_crore_never_k_or_m(amount, expected):
    assert in_words(amount) == expected


def test_no_output_ever_uses_western_shorthand():
    for amount in (100_000, 750_000, 10_000_000):
        rendered = in_words(amount)
        assert "K" not in rendered and "M" not in rendered
