"""
db.py
-----
One table, no ORM.

An expense is a date, an amount, a currency, a category and a note. There is
nothing relational about that, so the schema is one table and the queries are
short enough to read.

Two decisions in it are worth the comment.

**The currency is stored, not converted.** A tracker that converts on the way
in has thrown away what actually happened: you spent 12 euros, not 17 dollars
at whatever rate that afternoon. Storing the amount as spent means a total can
be presented in any currency later, and recomputed when the rate is known
better -- and it means this app never needs a rate at all, which is why it has
no network dependency.

**A client id, unique.** Entries can be made while the server is unreachable,
queued in the browser, and sent when it comes back. A queue that is flushed
twice -- two tabs, a retry, a refresh mid-send -- would otherwise double every
expense in it. The browser mints an id per entry and the constraint settles it
here, rather than in Python where a second code path could miss it.
"""
import contextlib
import os
import sqlite3
import threading

from core import paths

DB_NAME = "tally.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    spent_on   TEXT    NOT NULL,               -- ISO date, YYYY-MM-DD
    amount     INTEGER NOT NULL CHECK (amount > 0),
    currency   TEXT    NOT NULL,
    category   TEXT    NOT NULL,
    note       TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL,

    -- Minted by the browser, so a queued entry sent twice lands once.
    client_id  TEXT    NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_entries_date     ON entries(spent_on);
CREATE INDEX IF NOT EXISTS idx_entries_category ON entries(category);
"""

# Offered on a fresh install. Not enforced -- a category is any string, so
# nobody has to fight the list -- but an empty set of chips on first run is
# the fastest way to lose somebody.
DEFAULT_CATEGORIES = ("Coffee", "Lunch", "Groceries", "Transport", "Drinks",
                      "Snacks", "Household", "Other")

_lock = threading.Lock()


def db_path(directory=None):
    return os.path.join(paths.data_dir(directory), DB_NAME)


@contextlib.contextmanager
def session(directory=None):
    """A connection that is actually closed when the block ends.

    `with sqlite3.connect(...) as conn:` looks like it closes and does not --
    sqlite3's context manager scopes a transaction and leaves the connection
    open. The wallet app leaked one per request that way, which showed up as
    thirty-six ResourceWarnings a test run and would eventually exhaust a
    long-running server's file handles.
    """
    connection = connect(directory)
    try:
        yield connection
    finally:
        connection.close()


def connect(directory=None):
    """A connection with the schema applied.

    Rows come back as sqlite3.Row so callers read by name; positional access
    to a table that might gain a column is how a value gets read as the wrong
    field.
    """
    path = db_path(directory)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    with _lock:
        connection.executescript(SCHEMA)
        connection.commit()
    return connection
