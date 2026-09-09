# Tally — record what you spent, in the seconds after you spend it

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.12-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-134%20passing-brightgreen.svg)](tests/)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen.svg)](.coveragerc)

A phone-first expense tracker. Open it, tap an amount on a keypad, tap a
category, save. Two taps if it is something you buy often.

Everything stays on your machine. No account, no bank connection, no API key.

```bash
pip install -r requirements.txt
python app.py            # http://127.0.0.1:5005
```

To use it from your phone, run it on your laptop and set `HOST=0.0.0.0`, then
open `http://<your-laptop-ip>:5005`. **There is no login** — anyone on that
network can read and add expenses, and the app prints that warning on startup.
A home network is fine; a café is not.

## Why this exists next to the wallet app

[Wallet](https://github.com/luke-zhang-cs-py/Budgeting-EU-to-CAD-USD-Automatic)
does the other half: it reconciles against a bank export and converts each
purchase at the rate that applied on the day. But a bank export arrives days
late, and by then you no longer remember what the €14.20 was for.

So this app captures the thing a statement cannot: **what a purchase was,
while you still know.** It converts nothing and reconciles nothing — the other
app knows the rates, this one does not.

The `/export.csv` file uses exactly the four column names the wallet importer
recognises, so it imports without a column mapping:

```
Date,Description,Amount,Currency
2026-09-08,Coffee - Cafe Nero,-3.50,EUR
```

Expenses are written negative, because the far end reads a negative as money
out. `tests/test_export.py` reads the wallet app's own header lists with `ast`
and fails if either side renames a column — the two repositories are
independent, and nothing else would notice.

## The design decisions worth knowing

**A keypad, not a text field.** Digits shift in from the right the way a till
works: `3`, `5`, `0` gives `3.50`. There is no decimal point to place and none
to get wrong, and no keyboard to wait for.

**Two taps for a habit.** Anything bought three or more times in the last 90
days becomes a one-press button carrying its own amount, category and note. A
habit is a category *and* an amount — "Coffee" is no shortcut if it is
sometimes 2.50 and sometimes 18.

**Nothing is lost to a bad connection.** An entry is written to
`localStorage` *before* the network is touched, and cleared only once the
server confirms it. Close the tab mid-send, or tap save with the laptop
asleep, and it sends when you are back. Each entry carries an id the server
dedupes on, so a queue flushed twice — a retry, a second tab — lands once.

**Currencies are never added together.** A day with €12 and CA$8 in it shows
both, stacked, not a single number. Summing them needs a rate, and this app
does not have one; inventing a total would be worse than showing two.

**Money is an integer number of cents.** `0.1 + 0.2` is
`0.30000000000000004`, so a float total disagrees with the entries printed
above it — and a reader who notices that stops trusting every other figure on
the screen.

**Dates are dates, not timestamps.** `datetime` is a subclass of `date` in
Python, so a timestamp passes an `isinstance(x, date)` check untouched and
reaches storage as `2026-08-31T14:30:00`. That string sorts *after*
`2026-08-31`, so an expense recorded on the last day of a month would vanish
from its own month's total while every other day looked fine. It is coerced,
and `tests/test_entries.py` pins it.

## What it does

- A keypad, category chips, an optional note, and a back-date field
- Today / this week / this month, per currency
- One-press shortcuts for repeat purchases
- The last two weeks as a bar chart, and this month by category
- CSV export for any date range, defaulting to this month
- A reconcile check: does the file add up to what is stored

### Endpoints

| Route | Does |
|---|---|
| `GET /` | the page |
| `GET /api/overview` | everything the page needs, in one request |
| `GET /api/entries` | recent entries; `?on=` for one day, `?limit=` |
| `POST /api/entry` | record one |
| `POST /api/entries` | flush a queue; per-entry results, max 200 |
| `DELETE /api/entry/<id>` | remove one |
| `GET /export.csv` | the file; `?first=` and `?last=` |
| `GET /api/reconciles` | does the file agree with the database |

A rejected entry in a flushed queue does not fail the batch — the others were
real expenses, and refusing all of them to reject one loses them.

## Your data

`data/tally.db`, a SQLite file, and nothing else. It is a record of what you
spend, so `data/`, `*.db` and `*.csv` are all in `.gitignore`. If you add
anything that writes something derived from an entry, add it there in the same
commit.

Point it somewhere else with `TALLY_DATA=/path/to/folder`.

## Tests

```bash
pytest -q                                   # 134 passed
pytest -q --cov=. --cov-report=term-missing # 100% of 304 statements
```

The suite is not there for the number. Everything it asserts is something
that was got wrong first — a minus sign that silently recorded a positive
expense, a formatter that answered `""` for a null and printed a blank where a
figure belonged, a queue that doubled on retry. Where a test guards a
structural property, it has been run against a deliberately broken copy of the
code to confirm it fails; a guard nobody has watched fail is worth nothing.

All 134 run when the wallet app is checked out beside this one. On CI, and on
a machine with only this repository, the two cross-repo column checks skip
instead — `pytest -q -rs` says so rather than passing quietly. They are the
ones that would catch a renamed CSV column, so they matter most on the machine
where a rename would actually be made, which is a local one.

## License

MIT. See [LICENSE](LICENSE).
