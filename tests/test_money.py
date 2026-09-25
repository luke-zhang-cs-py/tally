"""Amounts.

Much smaller than the wallet app's equivalent, and the tests say why: every
amount here comes from this app's own keypad, so there is one convention to
read rather than a bank's. What is kept is the integer-cents discipline and
the refusal to turn nonsense into a zero.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain import money  # noqa: E402


@pytest.mark.parametrize("text,cents", [
    ("3.50", 350),
    ("3,50", 350),          # a European phone keyboard offers a comma
    ("12", 1200),
    ("0.05", 5),
    (".5", 50),
    ("1234.56", 123456),
    ("€3.50", 350),
    (" 3.50 ", 350),
    (7, 700),
])
def test_what_a_keypad_produces(text, cents):
    assert money.parse(text) == cents


@pytest.mark.parametrize("text", ["", "   ", None, "abc", ".", ",", "..",
                                  "1.2.3", "€"])
def test_nonsense_is_refused_rather_than_becoming_zero(text):
    """An entry of nothing looks exactly like one somebody meant to make."""
    with pytest.raises(money.MoneyError):
        money.parse(text)


@pytest.mark.parametrize("text", ["0", "0.00", "-5", "-0.01"])
def test_an_expense_has_to_be_more_than_nothing(text):
    """Zero and negative are both refused. This records what you spent, so a
    refund is not an entry -- it is the absence of one, or a correction."""
    with pytest.raises(money.MoneyError):
        money.parse(text)


def test_more_precision_than_the_currency_has_is_refused():
    """"3.505" is not an amount anybody can pay, and silently rounding it
    would make the stored figure differ from what was typed."""
    with pytest.raises(money.MoneyError):
        money.parse("3.505")


def test_a_boolean_is_not_an_amount():
    """True is an int in Python, and would quietly become one cent."""
    with pytest.raises(money.MoneyError):
        money.parse(True)


@pytest.mark.parametrize("cents,currency,shown", [
    (350, "EUR", "€3.50"),
    (350, "CAD", "CA$3.50"),
    (350, "USD", "US$3.50"),
    (123456, "EUR", "€1,234.56"),
    (5, "EUR", "€0.05"),
])
def test_formatting(cents, currency, shown):
    assert money.format(cents, currency) == shown


def test_the_csv_form_has_no_symbol_and_no_grouping():
    """"1,234.56" is two fields to anything that splits on commas."""
    assert money.plain(123456) == "1234.56"
    assert money.plain(-350) == "-3.50"


def test_the_formatters_take_a_number_and_say_so_if_they_do_not():
    """Both used to answer "" for None. That was written for a null column
    this schema cannot produce, and it turned a missing figure into a blank
    space on the page -- the quietest kind of wrong. The obligation it
    creates instead is tested in test_entries: no query here returns null.
    """
    for call in (money.format, money.plain):
        with pytest.raises(TypeError):
            call(None)


def test_formatting_never_truncates_the_cents():
    for cents in (300, 350, 5, 100000):
        assert money.format(cents, "EUR").split(".")[1].__len__() == 2


def test_totals_are_exact_at_scale():
    """The property integer cents exists to protect: the entries add up to
    the total printed under them."""
    entries = [money.parse("0.10"), money.parse("0.20"), money.parse("3.50")]
    assert money.total(entries) == 380
    assert money.format(money.total(entries)) == "€3.80"


def test_a_round_trip_survives():
    for text in ("3.50", "1234.56", "0.05"):
        assert money.parse(money.plain(money.parse(text))) == money.parse(text)


@pytest.mark.parametrize("given,expected", [
    ("eur", "EUR"), ("CAD", "CAD"), (" usd ", "USD"),
    ("", "EUR"), (None, "EUR"), ("GBP", "EUR"), ("nonsense", "EUR"),
])
def test_an_unknown_currency_falls_back_rather_than_raising(given, expected):
    """A stale tab posting a currency this version no longer offers should
    still record the expense. Losing the entry is worse than filing it in
    euros and letting it be corrected."""
    assert money.known(given) == expected


def test_the_scale_is_named_not_written_out():
    assert money.MINOR_UNITS == 2
    assert money._SCALE == 100
