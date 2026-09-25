"""
export.py
---------
A CSV the wallet app can import without being told anything.

The two apps do different halves of the job. This one captures at the moment
you spend; the wallet app reconciles against what the bank eventually says and
converts at the rate that applied on the day. So the useful thing this can
produce is a file the other one already knows how to read.

Which fixes the column names rather than leaving them to taste. The wallet
app's importer matches headers against a list it keeps, so `Date`,
`Description`, `Amount` and `Currency` are chosen because they are the names
it recognises with no mapping to correct -- and a test here asserts that,
rather than trusting that both ends still agree.

Expenses are written negative. The wallet app treats a negative amount as
money out, and it is a spending tracker on both sides.
"""
import csv
import datetime as dt
import io

from domain import entries
from domain import money

# Exactly the names the wallet app's importer looks for. Not decoration: any
# other spelling means somebody has to correct the column mapping by hand.
COLUMNS = ["Date", "Description", "Amount", "Currency"]


def as_csv(connection, first=None, last=None):
    """Every entry in the range, as a string."""
    day = entries.today()
    first = entries.as_date(first or day.replace(day=1))
    last = entries.as_date(last or day)

    rows = connection.execute(
        "SELECT spent_on, amount, currency, category, note FROM entries "
        "WHERE spent_on BETWEEN ? AND ? ORDER BY spent_on, id",
        (first.isoformat(), last.isoformat())).fetchall()

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({
            "Date": row["spent_on"],
            "Description": _description(row["category"], row["note"]),
            # Negative: the other side reads a negative as money out.
            "Amount": money.plain(-row["amount"]),
            "Currency": row["currency"],
        })
    return buffer.getvalue()


def _description(category, note):
    """One description from the category and the note.

    Both, when there is a note, because the category alone is too coarse to
    recognise a purchase in a statement six weeks later -- and recognising it
    is the whole point of exporting into something that reconciles.
    """
    note = (note or "").strip()
    # A plain hyphen, not an em-dash. The file is written UTF-8 and read
    # UTF-8, so a dash would survive the round trip -- but a CSV exists to be
    # opened by other programs, and one of them is Excel on Windows, which
    # will re-save it in the local codepage and mangle anything outside ASCII.
    # Typographic niceness is not worth a class of encoding bug in a file
    # nobody reads for pleasure.
    return f"{category} - {note}" if note else category


def filename(first=None, last=None):
    """A name that sorts and says what it holds."""
    day = entries.today()
    first = entries.as_date(first or day.replace(day=1))
    last = entries.as_date(last or day)
    if first == last:
        return f"tally-{first.isoformat()}.csv"
    return f"tally-{first.isoformat()}-to-{last.isoformat()}.csv"


def reconciles(connection, first=None, last=None):
    """Does the file add up to the entries it came from.

    Cheap, and it is the thing a reader most needs to be able to trust before
    handing the file to another program. Available from the page.
    """
    text = as_csv(connection, first, last)
    rows = list(csv.DictReader(io.StringIO(text)))
    from_file = money.total(money.parse(row["Amount"].lstrip("-"))
                            for row in rows if row["Amount"])

    day = entries.today()
    lo = entries.as_date(first or day.replace(day=1)).isoformat()
    hi = entries.as_date(last or day).isoformat()
    stored = connection.execute(
        "SELECT COALESCE(SUM(amount), 0) AS cents FROM entries "
        "WHERE spent_on BETWEEN ? AND ?", (lo, hi)).fetchone()["cents"]
    return from_file == stored, from_file, stored


def summary(connection, first=None, last=None):
    """What the export would contain, for the button's label."""
    day = entries.today()
    lo = entries.as_date(first or day.replace(day=1)).isoformat()
    hi = entries.as_date(last or day).isoformat()
    row = connection.execute(
        "SELECT COUNT(*) AS entries, COALESCE(SUM(amount), 0) AS cents "
        "FROM entries WHERE spent_on BETWEEN ? AND ?", (lo, hi)).fetchone()
    return {"first": lo, "last": hi, "entries": row["entries"],
            "cents": row["cents"],
            "text": money.format(row["cents"], money.DEFAULT_CURRENCY),
            "filename": filename(first, last)}


def month_of(value=None):
    """(first, last) of the month a date falls in, for the default range."""
    day = entries.as_date(value) if value else entries.today()
    first = day.replace(day=1)
    if first.month == 12:
        nxt = first.replace(year=first.year + 1, month=1)
    else:
        nxt = first.replace(month=first.month + 1)
    return first, nxt - dt.timedelta(days=1)
