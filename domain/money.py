"""
money.py
--------
Amounts as whole numbers of cents.

Deliberately much smaller than the wallet app's equivalent. That one has to
read a figure out of any bank's CSV, so it carries rules for both decimal
conventions and for accounting negatives. Nothing here reads a bank file:
every amount arrives from a keypad in this app's own page, so it needs one
parser and one formatter, and inventing the rest would be speculative.

What it keeps is the part that matters. Money is an integer here because
floats are the wrong type for it: 0.1 + 0.2 is 0.30000000000000004, so a
day's total disagrees with the entries above it, and a reader who notices
that stops trusting every other figure on the screen.
"""
import re

# The currencies this app offers. Euro first because that is where the
# spending happens; the other two are here so a trip does not need a second
# app. Which one an entry is in is stored per entry -- see db.py on why a
# tracker must not convert on the way in.
CURRENCIES = ("EUR", "CAD", "USD")
DEFAULT_CURRENCY = "EUR"

SYMBOLS = {"EUR": "€", "CAD": "CA$", "USD": "US$"}

# Two decimal places for all three. Named rather than written as 100 so the
# assumption is visible: JPY has none, and this is the line that would have to
# change.
MINOR_UNITS = 2
_SCALE = 10 ** MINOR_UNITS

_ALLOWED = re.compile(r"[^\d.,]")


class MoneyError(ValueError):
    """An amount that could not be read.

    Never a silent zero. An entry of nothing looks exactly like an entry
    somebody meant to make, so it has to be refused at the edge rather than
    stored and puzzled over later.
    """


def parse(text):
    """A typed amount as an integer number of cents.

    One convention, not two: the keypad in this app produces "12.34", and a
    comma is accepted as the same separator because a phone keyboard set to a
    European locale offers one. There is no thousands-grouping rule, because
    nothing here is typed with grouping -- which is the difference between
    this and the wallet app's parser, and the reason that one is four times
    the size.
    """
    if text is None:
        raise MoneyError("no amount given")
    if isinstance(text, int) and not isinstance(text, bool):
        return text * _SCALE

    raw = str(text)
    # A sign has to be caught before the strip, because the strip removes it:
    # "-5" became 5, so typing a minus recorded a five-euro expense instead of
    # refusing. Silently reversing what somebody typed is worse than either
    # accepting or rejecting it, and this app records only what was spent.
    if "-" in raw:
        raise MoneyError("an expense is a positive amount")

    cleaned = _ALLOWED.sub("", raw).replace(",", ".")
    if not any(character.isdigit() for character in cleaned):
        raise MoneyError(f"no digits in {text!r}")
    if cleaned.count(".") > 1:
        raise MoneyError(f"cannot read {text!r}")

    whole, _, fraction = cleaned.partition(".")
    if len(fraction) > MINOR_UNITS:
        raise MoneyError(f"{text!r} has more than {MINOR_UNITS} decimal places")

    cents = int(whole or 0) * _SCALE + int((fraction + "00")[:MINOR_UNITS])
    if cents <= 0:
        raise MoneyError("an amount has to be more than nothing")
    return cents


def format(cents, currency=DEFAULT_CURRENCY):
    """For the screen: a symbol, grouping, always two decimals.

    Takes a number, not None. It had a `symbol=False` flag and a
    `cents is None` guard, and neither had a caller: `plain` is the
    without-a-symbol formatter, and no column or query in this app yields a
    null amount -- `amount` is NOT NULL, every grouped SUM has at least one
    row by definition, and the two ungrouped sums COALESCE in SQL. Returning
    "" for None only meant a blank where a figure belongs, which is a harder
    failure to notice than the TypeError this raises now.
    """
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(int(cents)), _SCALE)
    return (f"{sign}{SYMBOLS.get(currency, currency + ' ')}"
            f"{whole:,}.{fraction:0{MINOR_UNITS}d}")


def plain(cents):
    """The bare figure for a CSV: no symbol and no grouping.

    "1,234.56" is two fields to anything that splits on commas, which is the
    one thing a CSV reader reliably does.
    """
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(int(cents)), _SCALE)
    return f"{sign}{whole}.{fraction:0{MINOR_UNITS}d}"


def total(amounts):
    """Sum of cents. Exists so no caller is tempted to sum floats."""
    return sum(int(a) for a in amounts if a is not None)


def known(currency):
    """The currency, upper-cased, or the default.

    Falls back rather than raising: a stale tab posting a currency this
    version no longer offers should still record the expense, because losing
    the entry is worse than filing it in euros and letting it be corrected.
    """
    code = (currency or "").strip().upper()
    return code if code in CURRENCIES else DEFAULT_CURRENCY
