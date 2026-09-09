# Contributing

## Setup

```bash
pip install -r requirements.txt
pytest -q
python app.py            # http://127.0.0.1:5005
```

Nothing to configure. The database is created on first use.

## The one thing that will confuse you

Two apps in this family have a `db.py`, a `money.py` and a `paths.py`, and
they are **not** the same modules. If you put the wallet app on `sys.path` to
reach something, whichever was imported first wins and the other silently gets
the wrong one.

So the test that holds the two apps' CSV columns together reads the other
repository's source with `ast` instead of importing it, and skips when it is
not checked out. Keep it that way.

## Rules worth keeping

**Money is an integer number of cents.** Not a float, anywhere, ever. If you
find yourself wanting one, you want `money.parse` or `money.total`.

**Nothing converts between currencies.** Not on the way in, not in a total,
not in the export. The rate belongs to the day of the purchase and only the
wallet app knows it. Two currencies on one day are shown as two figures.

**An amount is never a silent zero.** `money.parse` raises rather than
returning `0` for something it could not read, because an entry of nothing
looks exactly like an entry somebody meant to make.

**Everything reaching the DOM goes through `esc()`.** A note is typed by a
person, and the face-recognition app in this family shipped a stored XSS hole
by putting a typed name straight into `innerHTML`.

**Every tap target is at least 44px.** This app is judged on the seconds
between opening it and having recorded something, and a missed tap costs more
than any amount of layout.

**Do not add a parameter without a caller.** `money.format` carried a
`symbol=False` flag and a `None` guard that nothing used. Both were removed,
and one of them had been hiding a real failure mode behind a blank string.

## Tests

`pytest -q` — 133, and coverage is 100%. Both are expected to stay there.

Two things matter more than the number.

**Watch a new guard fail.** Break the code it protects, confirm the test goes
red, then put the code back. Three structural tests written for these projects
passed unconditionally: one compared a value to itself, one grepped a whole
file and found the string it was looking for in a comment, and one contained a
literal backspace byte where an escaped `b` belonged. None of them could ever
have failed.

**Do not lean on state the runner has no copy of.** `data/` is gitignored, so
CI has no database. A test that quietly needs one passes locally and fails
there — which is how the transit project in this family went red for six
commits.
