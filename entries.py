"""
entries.py
----------
Adding an expense, and the numbers you want back immediately.

The point of this app is the seconds between deciding to record something and
having recorded it. Everything here serves that:

  * `add` takes what a keypad produces and nothing else -- no importer, no
    column mapping, no rate lookup, so there is nothing that can be slow or
    fail.
  * `usual` is what makes it two taps instead of typing. The things you buy
    repeatedly become buttons, ranked by how often you actually buy them, so
    the common case is one press.
  * `totals` answers "how much today" without a second request, because that
    is the question that makes somebody open it again tomorrow.

Nothing converts between currencies. An entry is stored in the currency it was
spent in, and a total is reported per currency rather than summed across them
-- adding euros to dollars and printing one figure would be a made-up number,
and this app has no rate to make it with.
"""
import datetime as dt
import uuid

import db
import money

# How far back `usual` looks. Long enough that a monthly habit shows up, short
# enough that last spring's holiday coffees are not still on the keypad.
USUAL_WINDOW_DAYS = 90

# How many one-tap buttons to offer. More than fits a phone screen is not a
# shortcut, it is a search.
USUAL_LIMIT = 8

# A repeat has to have happened at least this often to earn a button. Twice is
# a coincidence.
USUAL_MIN_COUNT = 3


class EntryError(ValueError):
    """The entry cannot be recorded, with a reason worth showing."""


def new_client_id():
    """An id for an entry made in the browser.

    Minted per entry rather than per request, so a retry of the same entry
    carries the same id and lands once. Server-side too, because a manual
    call needs one and requiring the caller to invent it would mean every
    caller inventing it differently.
    """
    return uuid.uuid4().hex


def today():
    return dt.date.today()


def as_date(value):
    """A date from what the page sends, or today.

    Only ISO, because the only producer is this app's own date input. The
    wallet app accepts eight formats because it reads other people's files;
    accepting formats nothing sends would be code with no caller.
    """
    if value in (None, ""):
        return today()
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value).strip())
    except ValueError:
        raise EntryError(f"unreadable date: {value!r}")


def add(connection, amount, category, note="", currency=None, spent_on=None,
        client_id=None):
    """Record one expense. Returns (id, "added" | "duplicate").

    A duplicate is a normal outcome, not an error: a queued entry can be sent
    twice by a retry or a second tab, and the useful answer is that it is
    already recorded rather than a failure the page has to interpret.
    """
    cents = money.parse(amount)
    category = (category or "").strip()
    if not category:
        raise EntryError("an expense needs a category")

    on = as_date(spent_on)
    if on > today():
        # A future expense has not happened. Refusing beats recording it and
        # having today's total include something that has not been spent.
        raise EntryError("that date is in the future")

    mark = (client_id or "").strip() or new_client_id()
    existing = connection.execute(
        "SELECT id FROM entries WHERE client_id = ?", (mark,)).fetchone()
    if existing:
        return existing["id"], "duplicate"

    cursor = connection.execute(
        "INSERT INTO entries (spent_on, amount, currency, category, note, "
        "created_at, client_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (on.isoformat(), cents, money.known(currency), category,
         (note or "").strip(),
         dt.datetime.now().isoformat(timespec="seconds"), mark))
    connection.commit()
    return cursor.lastrowid, "added"


def remove(connection, entry_id):
    """Delete one. Returns whether there was anything to delete.

    Reported rather than silent, because the page shows an undo and needs to
    know if it did anything.
    """
    cursor = connection.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
    connection.commit()
    return cursor.rowcount > 0


def recent(connection, limit=25, day=None):
    """The latest entries, newest first, for the list under the keypad."""
    clauses, args = [], []
    if day is not None:
        clauses.append("spent_on = ?")
        args.append(as_date(day).isoformat())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    args.append(int(limit))
    return [_shown(row) for row in connection.execute(
        f"SELECT * FROM entries {where} "
        f"ORDER BY spent_on DESC, id DESC LIMIT ?", args)]


def _shown(row):
    """One entry as the page wants it, with the figure already formatted.

    Formatted here rather than in the browser so there is one implementation
    of the rounding. Two would drift, and the one that drifts is the one that
    makes a total disagree with the rows above it.
    """
    out = dict(row)
    out["amount_text"] = money.format(row["amount"], row["currency"])
    out["plain"] = money.plain(row["amount"])
    return out


def usual(connection, limit=USUAL_LIMIT, window=USUAL_WINDOW_DAYS,
          on=None):
    """The expenses worth a one-tap button.

    Grouped by what identifies a habit -- the category, the amount and the
    note -- and ranked by how often it has happened. That triple rather than
    the category alone, because "Coffee" is not a shortcut if it is sometimes
    2.50 and sometimes 18: tapping it would still leave an amount to type,
    which is the work this is meant to remove.
    """
    since = (as_date(on) - dt.timedelta(days=window)).isoformat()
    rows = connection.execute(
        "SELECT category, amount, currency, note, COUNT(*) AS times, "
        "       MAX(spent_on) AS last_on "
        "FROM entries WHERE spent_on >= ? "
        "GROUP BY category, amount, currency, note "
        "HAVING times >= ? "
        "ORDER BY times DESC, last_on DESC LIMIT ?",
        (since, USUAL_MIN_COUNT, int(limit))).fetchall()

    return [{
        "category": row["category"],
        "amount": row["amount"],
        "amount_text": money.format(row["amount"], row["currency"]),
        "currency": row["currency"],
        "note": row["note"],
        "times": row["times"],
        "last_on": row["last_on"],
    } for row in rows]


def categories(connection):
    """Categories in use, most-used first, then the defaults.

    Most-used first because the chip you want is nearly always one you have
    used before, and alphabetical order buries it.
    """
    used = [row["category"] for row in connection.execute(
        "SELECT category, COUNT(*) AS times FROM entries "
        "GROUP BY category ORDER BY times DESC, category")]
    for name in db.DEFAULT_CATEGORIES:
        if name not in used:
            used.append(name)
    return used


def totals(connection, on=None):
    """Today, this week and this month -- per currency.

    Per currency rather than one figure, because summing euros and dollars
    needs a rate this app does not have, and printing the sum anyway would be
    a number nobody could reproduce.
    """
    day = as_date(on)
    monday = day - dt.timedelta(days=day.weekday())
    first = day.replace(day=1)

    return {
        "day": day.isoformat(),
        "today": _by_currency(connection, day.isoformat(), day.isoformat()),
        "week": _by_currency(connection, monday.isoformat(), day.isoformat()),
        "month": _by_currency(connection, first.isoformat(), day.isoformat()),
    }


def _by_currency(connection, first, last):
    """[{currency, cents, text}] over an inclusive date range."""
    rows = connection.execute(
        "SELECT currency, SUM(amount) AS cents, COUNT(*) AS entries "
        "FROM entries WHERE spent_on BETWEEN ? AND ? "
        "GROUP BY currency ORDER BY cents DESC", (first, last)).fetchall()
    return [{"currency": row["currency"], "cents": row["cents"],
             "entries": row["entries"],
             "text": money.format(row["cents"], row["currency"])}
            for row in rows]


def by_category(connection, first=None, last=None):
    """[{category, currency, cents, text}] for the breakdown."""
    day = today()
    first = as_date(first or day.replace(day=1)).isoformat()
    last = as_date(last or day).isoformat()
    rows = connection.execute(
        "SELECT category, currency, SUM(amount) AS cents, COUNT(*) AS entries "
        "FROM entries WHERE spent_on BETWEEN ? AND ? "
        "GROUP BY category, currency ORDER BY cents DESC", (first, last))
    return [{"category": row["category"], "currency": row["currency"],
             "cents": row["cents"], "entries": row["entries"],
             "text": money.format(row["cents"], row["currency"])}
            for row in rows]


def days(connection, limit=14, on=None):
    """[{date, cents, text}] per day, oldest first, for the little chart.

    Days with nothing spent are included as zero rather than omitted: a gap
    in a bar chart reads as missing data, and a day you spent nothing is a
    fact worth seeing.
    """
    end = as_date(on)
    start = end - dt.timedelta(days=int(limit) - 1)
    found = {row["spent_on"]: row["cents"] for row in connection.execute(
        "SELECT spent_on, SUM(amount) AS cents FROM entries "
        "WHERE spent_on BETWEEN ? AND ? GROUP BY spent_on",
        (start.isoformat(), end.isoformat()))}

    out = []
    for offset in range(int(limit)):
        day = (start + dt.timedelta(days=offset)).isoformat()
        cents = found.get(day, 0)
        out.append({"date": day, "cents": cents,
                    "text": money.format(cents, money.DEFAULT_CURRENCY)})
    return out
