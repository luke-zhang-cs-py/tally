"""Recording an expense, and the numbers that come straight back.

Two things carry most of the weight here.

**The client id.** Entries can be made while the server is unreachable and
sent when it returns, so a queue flushed twice -- a retry, a second tab, a
refresh mid-send -- must not double every expense in it.

**`usual`.** It is what makes this two taps instead of typing, so what does
and does not earn a button is worth pinning: a habit is a category *and* an
amount, because "Coffee" is no shortcut if it is sometimes 2.50 and sometimes
18.
"""
import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db        # noqa: E402
import entries   # noqa: E402
import money     # noqa: E402


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("TALLY_DATA", str(tmp_path))
    connection = db.connect(str(tmp_path))
    yield connection
    connection.close()


def spend(conn, amount, category, day="2026-09-08", note="", currency=None,
          client_id=None):
    return entries.add(conn, amount, category, note=note, currency=currency,
                       spent_on=day, client_id=client_id)


# ------------------------------------------------------------- recording

def test_an_expense_is_recorded(conn):
    entry_id, how = spend(conn, "3.50", "Coffee")
    assert how == "added"
    row = entries.recent(conn)[0]
    assert row["id"] == entry_id
    assert row["amount"] == 350
    assert row["category"] == "Coffee"
    assert row["currency"] == "EUR"
    assert row["amount_text"] == "€3.50"


def test_the_date_defaults_to_today(conn):
    entries.add(conn, "3.50", "Coffee")
    assert entries.recent(conn)[0]["spent_on"] == entries.today().isoformat()


def test_a_category_is_required(conn):
    """Without one the entry cannot be grouped, charted or exported
    usefully -- it is a number with no meaning attached."""
    for bad in ("", "   ", None):
        with pytest.raises(entries.EntryError):
            spend(conn, "3.50", bad)


def test_a_future_date_is_refused(conn):
    """It has not been spent. Recording it would put money in today's total
    that has not left."""
    tomorrow = (entries.today() + dt.timedelta(days=1)).isoformat()
    with pytest.raises(entries.EntryError):
        spend(conn, "3.50", "Coffee", day=tomorrow)


def test_today_itself_is_fine(conn):
    """The boundary. An off-by-one here would refuse every entry made today,
    which is all of them."""
    assert spend(conn, "3.50", "Coffee",
                 day=entries.today().isoformat())[1] == "added"


def test_an_unreadable_date_is_refused(conn):
    with pytest.raises(entries.EntryError):
        spend(conn, "3.50", "Coffee", day="not-a-date")


def test_an_unreadable_amount_is_refused(conn):
    with pytest.raises(money.MoneyError):
        spend(conn, "abc", "Coffee")


def test_a_note_is_optional_and_trimmed(conn):
    spend(conn, "3.50", "Coffee", note="  Cafe Nero  ")
    assert entries.recent(conn)[0]["note"] == "Cafe Nero"
    spend(conn, "3.50", "Lunch")
    assert entries.recent(conn)[0]["note"] == ""


# ------------------------------------------------------- the offline queue

def test_the_same_queued_entry_sent_twice_lands_once(conn):
    """The reason there is a client id at all."""
    first = spend(conn, "3.50", "Coffee", client_id="abc")
    second = spend(conn, "3.50", "Coffee", client_id="abc")
    assert first[1] == "added"
    assert second == (first[0], "duplicate")
    assert len(entries.recent(conn)) == 1


def test_two_identical_coffees_are_two_entries(conn):
    """Different ids, so the bank-statement problem does not apply here: the
    browser knows these were two separate taps and says so. This is where a
    quick-capture app can be certain and an importer cannot."""
    spend(conn, "3.50", "Coffee", client_id="one")
    spend(conn, "3.50", "Coffee", client_id="two")
    assert len(entries.recent(conn)) == 2


def test_an_entry_without_an_id_gets_one(conn):
    """A manual call, or a page too old to send one. It still has to be
    storable, and the column is NOT NULL."""
    spend(conn, "3.50", "Coffee")
    spend(conn, "3.50", "Coffee")
    rows = entries.recent(conn)
    assert len(rows) == 2
    assert rows[0]["client_id"] != rows[1]["client_id"]


def test_ids_are_unique_across_calls():
    assert entries.new_client_id() != entries.new_client_id()


# --------------------------------------------------------------- deleting

def test_an_entry_can_be_removed(conn):
    entry_id, _ = spend(conn, "3.50", "Coffee")
    assert entries.remove(conn, entry_id) is True
    assert entries.recent(conn) == []


def test_removing_something_that_is_not_there_says_so(conn):
    """The page shows an undo and needs to know whether it did anything."""
    assert entries.remove(conn, 999) is False


# ------------------------------------------------------------ the shortcuts

def test_a_repeated_expense_becomes_a_one_tap_button(conn):
    for n, day in enumerate(("2026-09-05", "2026-09-07", "2026-09-08"), 1):
        spend(conn, "3.50", "Coffee", day=day, note="Cafe Nero",
              client_id=f"c{n}")
    shortcuts = entries.usual(conn, on="2026-09-08")
    assert len(shortcuts) == 1
    assert shortcuts[0]["category"] == "Coffee"
    assert shortcuts[0]["amount"] == 350
    assert shortcuts[0]["times"] == 3
    assert shortcuts[0]["amount_text"] == "€3.50"


def test_twice_is_a_coincidence(conn):
    """A shortcut you have used twice is not yet a habit, and a keypad full
    of one-offs is a search rather than a shortcut."""
    spend(conn, "3.50", "Coffee", day="2026-09-07", client_id="a")
    spend(conn, "3.50", "Coffee", day="2026-09-08", client_id="b")
    assert entries.usual(conn, on="2026-09-08") == []


def test_the_same_category_at_different_amounts_is_not_one_habit(conn):
    """"Coffee" is no shortcut if tapping it still leaves an amount to type,
    which is the work this is meant to remove."""
    for n, amount in enumerate(("2.50", "3.50", "18.00"), 1):
        spend(conn, amount, "Coffee", day="2026-09-0%d" % (n + 4),
              client_id=f"v{n}")
    assert entries.usual(conn, on="2026-09-08") == []


def test_the_shortcuts_are_ranked_by_how_often(conn):
    for n in range(5):
        spend(conn, "3.50", "Coffee", day="2026-09-08", client_id=f"c{n}")
    for n in range(3):
        spend(conn, "12.40", "Lunch", day="2026-09-08", client_id=f"l{n}")
    order = [s["category"] for s in entries.usual(conn, on="2026-09-08")]
    assert order == ["Coffee", "Lunch"]


def test_an_old_habit_falls_off_the_keypad(conn):
    """Last spring's holiday coffees are not still a shortcut."""
    for n in range(4):
        spend(conn, "3.50", "Coffee", day="2026-01-0%d" % (n + 1),
              client_id=f"o{n}")
    assert entries.usual(conn, on="2026-09-08") == []
    assert entries.usual(conn, on="2026-01-05")


def test_only_so_many_buttons_are_offered(conn):
    """More than fits a screen is not a shortcut."""
    for kind in range(12):
        for n in range(3):
            spend(conn, f"{kind + 1}.00", f"Thing {kind}", day="2026-09-08",
                  client_id=f"k{kind}-{n}")
    assert len(entries.usual(conn, on="2026-09-08")) == entries.USUAL_LIMIT


# ------------------------------------------------------------- categories

def test_categories_are_offered_before_anything_is_recorded(conn):
    """An empty set of chips on first run is the fastest way to lose
    somebody."""
    assert entries.categories(conn) == list(db.DEFAULT_CATEGORIES)


def test_the_ones_you_use_come_first(conn):
    """The chip you want is nearly always one you have used, and
    alphabetical order buries it."""
    for n in range(4):
        spend(conn, "3.50", "Barber", client_id=f"b{n}")
    assert entries.categories(conn)[0] == "Barber"


def test_a_new_category_joins_the_list(conn):
    spend(conn, "3.50", "Parking")
    assert "Parking" in entries.categories(conn)


# ----------------------------------------------------------------- totals

def test_today_this_week_and_this_month(conn):
    # 2026-09-08 is a Tuesday; the 7th is its Monday, the 6th the Sunday
    # before, which is last week and this month.
    spend(conn, "3.50", "Coffee", day="2026-09-08", client_id="a")
    spend(conn, "12.40", "Lunch", day="2026-09-08", client_id="b")
    spend(conn, "5.00", "Snacks", day="2026-09-07", client_id="c")
    spend(conn, "9.00", "Drinks", day="2026-09-06", client_id="d")

    out = entries.totals(conn, "2026-09-08")
    assert out["today"][0]["cents"] == 1590
    assert out["week"][0]["cents"] == 2090
    assert out["month"][0]["cents"] == 2990


def test_currencies_are_reported_apart_not_summed(conn):
    """Adding euros to dollars needs a rate this app does not have, and
    printing the sum anyway would be a figure nobody could reproduce."""
    spend(conn, "10.00", "Lunch", client_id="e", currency="EUR")
    spend(conn, "10.00", "Lunch", client_id="c", currency="CAD")
    # The day the helper spends on, not the real today -- otherwise this
    # passes only while the two happen to coincide.
    today = entries.totals(conn, "2026-09-08")["today"]
    assert {row["currency"] for row in today} == {"EUR", "CAD"}
    assert all(row["cents"] == 1000 for row in today)


def test_a_day_with_nothing_reports_nothing_rather_than_a_zero_row(conn):
    assert entries.totals(conn, "2026-09-08")["today"] == []


def test_the_breakdown_groups_by_category(conn):
    spend(conn, "3.50", "Coffee", client_id="a")
    spend(conn, "3.50", "Coffee", client_id="b")
    spend(conn, "12.40", "Lunch", client_id="c")
    rows = entries.by_category(conn)
    assert rows[0]["category"] == "Lunch"          # largest first
    assert rows[1]["category"] == "Coffee"
    assert rows[1]["cents"] == 700
    assert rows[1]["entries"] == 2


# ------------------------------------------------------------ the day chart

def test_the_chart_covers_every_day_including_empty_ones(conn):
    """A gap in a bar chart reads as missing data. A day you spent nothing
    is a fact worth seeing."""
    spend(conn, "3.50", "Coffee", day="2026-09-08", client_id="a")
    days = entries.days(conn, limit=5, on="2026-09-08")
    assert len(days) == 5
    assert [d["date"] for d in days] == ["2026-09-04", "2026-09-05",
                                         "2026-09-06", "2026-09-07",
                                         "2026-09-08"]
    assert days[-1]["cents"] == 350
    assert days[0]["cents"] == 0
    assert days[0]["text"] == "€0.00"


def test_the_chart_runs_oldest_to_newest(conn):
    days = entries.days(conn, limit=3, on="2026-09-08")
    assert [d["date"] for d in days] == sorted(d["date"] for d in days)


# -------------------------------------------------------------- listing

def test_the_recent_list_is_newest_first(conn):
    spend(conn, "1.00", "A", day="2026-09-05", client_id="a")
    spend(conn, "2.00", "B", day="2026-09-08", client_id="b")
    assert [r["category"] for r in entries.recent(conn)] == ["B", "A"]


def test_the_list_can_be_narrowed_to_one_day(conn):
    spend(conn, "1.00", "A", day="2026-09-05", client_id="a")
    spend(conn, "2.00", "B", day="2026-09-08", client_id="b")
    assert [r["category"] for r in
            entries.recent(conn, day="2026-09-08")] == ["B"]


def test_the_list_can_be_limited(conn):
    for n in range(5):
        spend(conn, "1.00", f"C{n}", client_id=f"c{n}")
    assert len(entries.recent(conn, limit=2)) == 2


# ------------------------------------------------------------------ dates

def test_a_datetime_is_cut_down_to_its_date(conn):
    """`dt.datetime` is a *subclass* of `dt.date`, so an isinstance check
    against date alone lets one through untouched -- and it then reaches
    spent_on as "2026-09-30T14:30:00".

    Which is not merely untidy. spent_on is compared as text, so that string
    is greater than "2026-08-31": an expense recorded on the last day of a
    month falls outside its own month's range and disappears from the export
    and from the month total, while every other day of the month is fine.

    A past month, because a future date is refused before it reaches any of
    this -- the first version of this test used the end of the current one
    and was rejected as future-dated, which is the guard below working.
    """
    stamp = dt.datetime(2026, 8, 31, 14, 30)
    assert entries.as_date(stamp) == dt.date(2026, 8, 31)
    assert not isinstance(entries.as_date(stamp), dt.datetime)

    entries.add(conn, "3.50", "Coffee", spent_on=stamp, client_id="a")
    row = entries.recent(conn)[0]
    assert row["spent_on"] == "2026-08-31"
    assert "2026-08-01" <= row["spent_on"] <= "2026-08-31", (
        "the last day of the month has to fall inside the month")
    assert not ("2026-08-01" <= stamp.isoformat() <= "2026-08-31"), (
        "the uncoerced string would not, which is what this guards")


def test_the_month_total_and_the_export_both_see_that_entry(conn):
    """What the coercion is for, at the level a person notices: the figure on
    the screen and the row in the file."""
    entries.add(conn, "3.50", "Coffee", spent_on=dt.datetime(2026, 8, 31, 23,
                                                             59),
                client_id="a")
    month = entries.totals(conn, on="2026-08-31")["month"]
    assert [row["cents"] for row in month] == [350]
    assert [row["cents"] for row in
            entries.by_category(conn, "2026-08-01", "2026-08-31")] == [350]


def test_a_plain_date_is_left_alone(conn):
    assert entries.as_date(dt.date(2026, 8, 31)) == dt.date(2026, 8, 31)


# ----------------------------------------------------------------- nulls

def test_no_total_comes_back_null(conn):
    """The obligation created by money.format no longer guarding against
    None. It formats what these queries return, so the queries are what has
    to be checked -- and a bare `SUM(amount)` over no rows returns NULL, not
    zero. Every grouped sum here is safe by construction and the ungrouped
    ones COALESCE; this fails the day one is added that does neither.
    """
    spend(conn, "3.50", "Coffee", client_id="a")
    spend(conn, "8.00", "Drinks", currency="CAD", client_id="b")

    figures = []
    for rows in entries.totals(conn, on="2026-09-08").values():
        if isinstance(rows, list):
            figures += [row["cents"] for row in rows]
    figures += [row["cents"] for row in entries.by_category(
        conn, "2026-09-01", "2026-09-30")]
    figures += [row["cents"] for row in entries.days(conn, on="2026-09-08")]

    assert figures, "nothing was checked"
    for cents in figures:
        assert cents is not None
        assert money.format(cents)


def test_the_empty_case_is_zero_not_null(conn):
    """Where a null would actually arrive: a range with nothing in it."""
    for rows in entries.totals(conn).values():
        if isinstance(rows, list):
            assert rows == []
    assert [row["cents"] for row in entries.days(conn, limit=3)] == [0, 0, 0]
