"""The file, and whether the other app can still read it.

The two apps do different halves: this one captures at the moment you spend,
the wallet app reconciles against the bank and converts at the rate that
applied on the day. So the export exists to be read by that one, and the
column names are a contract rather than a preference -- which is worth a test,
because nothing else would notice the day one end renames a column.
"""
import ast
import csv
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db        # noqa: E402
from domain import entries   # noqa: E402
from domain import export    # noqa: E402


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("TALLY_DATA", str(tmp_path))
    connection = db.connect(str(tmp_path))
    yield connection
    connection.close()


def spend(conn, amount, category, day="2026-09-08", **kwargs):
    return entries.add(conn, amount, category, spent_on=day, **kwargs)


def rows_of(text):
    return list(csv.DictReader(io.StringIO(text)))


# ---------------------------------------------------------------- the shape

def test_the_columns_are_the_ones_the_other_app_recognises(conn):
    """A contract, not a preference. Any other spelling means somebody has to
    correct a column mapping by hand at the far end."""
    text = export.as_csv(conn, "2026-09-01", "2026-09-30")
    assert text.splitlines()[0] == "Date,Description,Amount,Currency"


# Where the wallet app keeps its header lists: ingest/ since it grouped its
# root modules into packages, the root before that. Both, because a checkout
# of either age is a wallet app -- and looking only at the old place made
# the contract test skip, which reads exactly like the wallet app being
# absent, for as long as nobody counted the skips.
WALLET_LAYOUTS = (("ingest", "layout.py"), ("layout.py",))


def _wallet_root():
    return os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "wallet-fx-budget")


def _wallet_layout():
    for parts in WALLET_LAYOUTS:
        source = os.path.join(_wallet_root(), *parts)
        if os.path.isfile(source):
            return source
    return None


def _wallet_name_lists():
    """The header-name tuples from the wallet app's layout.py.

    Read with ast rather than imported, and rather than grepped.

    Not imported, because both apps have a `db`, a `money` and a `paths`, so
    putting the other on sys.path hands its modules whichever copy was
    imported first.

    Not grepped, because the first version of this test did exactly that and
    was worthless: searching the whole file for "currency" finds the word in
    the FIELDS tuple, in a function call and in prose, so it passed with the
    column removed from the list it was supposed to be checking. Reading the
    assignments is the only version of this that can fail.
    """
    source = _wallet_layout()
    if source is None:
        return None

    tree = ast.parse(io.open(source, encoding="utf-8").read())
    found = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.endswith("_NAMES"):
                try:
                    found[target.id] = ast.literal_eval(node.value)
                except ValueError:              # pragma: no cover
                    pass
    return found


def test_the_column_names_match_the_wallet_apps_own_list():
    """Checked against that app's own lists, so this fails if either side
    renames a column. Skipped when the other app is not checked out; these
    are separate repositories and either can stand alone."""
    lists = _wallet_name_lists()
    if not lists:
        pytest.skip("the wallet app is not checked out beside this one")

    wanted = {"Date": "DATE_NAMES", "Description": "DESCRIPTION_NAMES",
              "Amount": "AMOUNT_NAMES", "Currency": "CURRENCY_NAMES"}
    for column, which in wanted.items():
        assert column.lower() in lists[which], (
            f"the wallet importer no longer recognises a {column!r} column")


def test_that_check_would_notice_a_rename():
    """The negative control, kept rather than run once by hand.

    Three structural tests I have written this month passed unconditionally.
    A guard is worth nothing until it has been seen to fail, so this holds a
    list with the name removed and asserts the check rejects it.
    """
    lists = _wallet_name_lists()
    if not lists:
        pytest.skip("the wallet app is not checked out beside this one")

    without = tuple(n for n in lists["CURRENCY_NAMES"] if n != "currency")
    assert "currency" not in without, "the fixture did not remove it"
    assert "currency" in lists["CURRENCY_NAMES"], "the real list should have it"


def test_expenses_are_written_negative(conn):
    """The far end reads a negative as money out, and this is a spending
    tracker on both sides."""
    spend(conn, "3.50", "Coffee")
    row = rows_of(export.as_csv(conn, "2026-09-01", "2026-09-30"))[0]
    assert row["Amount"] == "-3.50"


def test_amounts_have_no_symbol_and_no_grouping(conn):
    spend(conn, "1234.56", "Rent")
    row = rows_of(export.as_csv(conn, "2026-09-01", "2026-09-30"))[0]
    assert row["Amount"] == "-1234.56"
    assert "," not in row["Amount"]


def test_the_file_is_plain_ascii(conn):
    """It exists to be opened by other programs, one of which is Excel on
    Windows -- which will re-save it in the local codepage and mangle
    anything outside ASCII. An em-dash here cost nothing and risked that."""
    spend(conn, "3.50", "Coffee", note="Cafe Nero")
    assert export.as_csv(conn, "2026-09-01", "2026-09-30").isascii()


def test_the_note_is_kept_beside_the_category(conn):
    """The category alone is too coarse to recognise a purchase in a
    statement six weeks later, and recognising it is the point."""
    spend(conn, "3.50", "Coffee", note="Cafe Nero")
    row = rows_of(export.as_csv(conn, "2026-09-01", "2026-09-30"))[0]
    assert row["Description"] == "Coffee - Cafe Nero"


def test_without_a_note_the_category_stands_alone(conn):
    spend(conn, "3.50", "Coffee")
    row = rows_of(export.as_csv(conn, "2026-09-01", "2026-09-30"))[0]
    assert row["Description"] == "Coffee"


def test_the_currency_travels_with_the_row(conn):
    """Nothing is converted on the way out either. The far end knows the
    rate for the day; this app does not."""
    spend(conn, "8.00", "Drinks", currency="CAD", client_id="c")
    row = rows_of(export.as_csv(conn, "2026-09-01", "2026-09-30"))[0]
    assert row["Currency"] == "CAD"


def test_rows_come_out_oldest_first(conn):
    spend(conn, "1.00", "A", day="2026-09-08", client_id="a")
    spend(conn, "2.00", "B", day="2026-09-05", client_id="b")
    dates = [r["Date"] for r in rows_of(
        export.as_csv(conn, "2026-09-01", "2026-09-30"))]
    assert dates == sorted(dates)


def test_an_empty_range_still_writes_a_header(conn):
    """A file with no header is not a CSV, and the far end will choke on it
    rather than report nothing to report."""
    text = export.as_csv(conn, "2026-09-01", "2026-09-30")
    assert text.strip() == "Date,Description,Amount,Currency"


# ------------------------------------------------------------- the range

def test_the_range_is_inclusive_at_both_ends(conn):
    spend(conn, "1.00", "First", day="2026-08-01", client_id="a")
    spend(conn, "2.00", "Last", day="2026-08-31", client_id="b")
    spend(conn, "3.00", "Outside", day="2026-07-31", client_id="c")
    got = [r["Description"] for r in
           rows_of(export.as_csv(conn, "2026-08-01", "2026-08-31"))]
    assert got == ["First", "Last"]


def test_the_range_defaults_to_this_month(conn):
    day = entries.today()
    spend(conn, "1.00", "ThisMonth", day=day.isoformat(), client_id="a")
    assert len(rows_of(export.as_csv(conn))) == 1


# ----------------------------------------------------------- the filename

def test_the_filename_says_what_it_holds(conn):
    assert export.filename("2026-09-01", "2026-09-30") == \
        "tally-2026-09-01-to-2026-09-30.csv"
    assert export.filename("2026-09-08", "2026-09-08") == "tally-2026-09-08.csv"


# ---------------------------------------------------------- does it add up

def test_the_file_adds_up_to_what_is_stored(conn):
    """The property a reader most needs before handing the file to another
    program."""
    spend(conn, "3.50", "Coffee", client_id="a")
    spend(conn, "12.40", "Lunch", client_id="b")
    agrees, from_file, stored = export.reconciles(conn, "2026-09-01",
                                                  "2026-09-30")
    assert agrees
    assert from_file == stored == 1590


def test_it_adds_up_over_an_empty_range(conn):
    agrees, from_file, stored = export.reconciles(conn, "2026-09-01",
                                                  "2026-09-30")
    assert agrees
    assert from_file == stored == 0


def test_the_summary_describes_the_export(conn):
    spend(conn, "3.50", "Coffee", client_id="a")
    out = export.summary(conn, "2026-09-01", "2026-09-30")
    assert out["entries"] == 1
    assert out["cents"] == 350
    assert out["text"] == "€3.50"
    assert out["filename"].endswith(".csv")


def test_the_summary_of_nothing_is_zero_not_an_error(conn):
    out = export.summary(conn, "2026-09-01", "2026-09-30")
    assert out["entries"] == 0
    assert out["cents"] == 0


def test_a_checked_out_wallet_app_is_never_mistaken_for_a_missing_one():
    """The contract test above skips when the wallet app is absent. It
    skipped for a week with the wallet app right there, because layout.py
    had moved into ingest/ -- so a skip has to mean the checkout is missing,
    not that this file lost track of it."""
    if not os.path.isdir(_wallet_root()):
        pytest.skip("the wallet app is not checked out beside this one")
    assert _wallet_layout(), (
        "wallet-fx-budget is here but none of %s is -- its header lists "
        "moved again" % ", ".join("/".join(p) for p in WALLET_LAYOUTS))


# ------------------------------------------------------ formula injection

@pytest.mark.parametrize("category", ["=HYPERLINK(\"http://x\",\"a\")",
                                      "+1+1", "-2+3", "@SUM(A1)",
                                      "=1+1"])
def test_a_description_is_never_a_formula(conn, category):
    """A category is any string, and with HOST=0.0.0.0 and no login anyone
    on the network can type one. Opened in Excel, "=HYPERLINK(...)" in the
    Description column is a live formula, not text. A leading apostrophe
    is how a spreadsheet is told a cell is text; the wallet app imports it
    as one more character of description."""
    spend(conn, "3.50", category)
    row = rows_of(export.as_csv(conn, "2026-09-01", "2026-09-30"))[0]
    assert row["Description"] == "'" + category
    assert export.reconciles(conn, "2026-09-01", "2026-09-30")[0]


def test_an_ordinary_description_is_left_alone(conn):
    spend(conn, "3.50", "Coffee", note="=not a formula here")
    row = rows_of(export.as_csv(conn, "2026-09-01", "2026-09-30"))[0]
    assert row["Description"] == "Coffee - =not a formula here"
