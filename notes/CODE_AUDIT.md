# Code audit

What the checklist found in this repository, and what was done about it. Same
review the other five projects in this family had; this one was written with it
in hand, so the list is shorter and the findings are mostly things caught
before they shipped rather than after.

## Third pass — 5 October 2026

Baseline: **208 passed, 1 failed, 1 skipped.** After: **212 passed, 1
skipped** (213 collected), flake8 clean at `--max-complexity=10`, and
`tools/build_static.py` agrees with the Python on all three passes (1158
money values, 39 payloads, 56 requests).

### Bugs fixed

1. **A test that started failing on 1 October.**
   `test_the_breakdown_groups_by_category` spent on 2026-09-08 and called
   `by_category` with no range, so "this month" came from the real calendar.
   It passes the helper's September now. That was the baseline failure; it
   was a broken test, not broken code.
   Covered by: the test itself.
2. **Mixed currencies printed as one euro figure, in three places.** The
   module docstring says a total is never summed across currencies, and
   `totals` follows that, but three other figures did not: the export note
   (`export.summary` text), the reconcile footer (`/api/reconciles` text)
   and each day's bar label (`entries.days` text) all summed every
   currency's cents and printed the result in euros. 3.50 EUR + 10.00 USD
   showed as "€13.50". They now read "US$10.00 + €3.50", through one helper,
   `entries.in_each_currency` / `range_text`, and the same change in the
   browser build's `store.js` and `static-api.js`. A day's `cents` is still
   the sum, because it only sets the bar's length.
   Covered by: `test_a_day_in_two_currencies_is_labelled_in_both`,
   `test_the_summary_never_sums_currencies_into_euros`,
   `test_the_reconcile_footer_names_each_currency`. All three were run
   against the old code and failed, and the build's parity pass checks the
   port on its mixed-currency fixture.

`_by_currency` also sorts ties by currency code now, as the JS port already
did. Before, a tie came back in whatever order SQLite chose.

### The checklist

- **Dispensables:** nothing new. No dead code, stale comments or unused
  imports found.
- **Bloaters:** none. No function goes over complexity 10.
- **Abusers:** none. No switch statements, temporary fields or tangled
  conditionals.
- **Couplers:** `export` and `app` call `entries.range_text` rather than
  reaching into `entries._by_currency`. Nothing else found.
- **Change preventers:** what bug 2 really was. The "per currency, never
  summed" rule was written once in `totals` and ignored by three other
  callers. It now lives in one helper.
- **Global data / magic numbers / naming:** nothing new.
- **Bug classes:** logic (bug 2), a test that depended on the date (bug 1).
  Security: every `innerHTML` in `app.js` still goes through `esc()`, the
  JSON-only writes and the CSV formula guard are still in place, and no
  secrets, personal data or local paths are in the tree.
  Out of bounds: `/api/entries?limit=-1` reaches SQLite as `LIMIT -1`, which
  means no limit. That is harmless on a single-user ledger, so it is left
  (see below).

### Coverage

| Module | Statements | Lines | Branches |
|---|---|---|---|
| `app.py` | 107 | 100% | 10/10 |
| `core/db.py` | 26 | 100% | 0/0 |
| `core/paths.py` | 8 | 100% | 2/2 |
| `domain/entries.py` | 113 | 100% | 29/30 |
| `domain/export.py` | 37 | 100% | 4/4 |
| `domain/money.py` | 48 | 100% | 18/18 |
| **Total** | **339** | **100%** | **63/64 (99%)** |

The one partial branch is `categories()` when every default category is
already in use. The browser build is exercised by `build_static.py`'s
headless-Chrome parity run, not by pytest.

### Maintenance

- **Corrective:** both bugs above.
- **Adaptive:** nothing needed. The Flask and pytest pins are current, and
  the Actions versions are `checkout@v5` and `setup-python@v6`.
- **Perfective:** a trip month's export note and bars now show what was
  actually spent, in each currency.
- **Preventive:** the three mixed-currency tests. The figures on the
  published page and in the README were re-measured with
  `tools/refresh_figures.py`.

### Left for later

- Clamp `limit` on `/api/entries` (negative means unlimited). This needs
  the same change in `static-api.js` so the parity pass stays green.
- A day's bar *length* still adds currencies together. The figure beside it
  is honest now; a per-currency bar would be a design change.
- Other tests that rely on "today" should pin a date, as bug 1 now does.

## First pass

Measured 9 September 2026: **134 tests, 100% of 304 statements**, flake8 clean
including `--max-complexity=10`.

## Dispensables

**Dead flag argument — `money.format(..., symbol=False)`.** Zero callers.
`plain()` is the without-a-symbol formatter and always was. Removed, along
with the `if not symbol` branch. A boolean parameter that selects between two
behaviours is a request for two functions, and here both already existed.

**Speculative guard — `cents is None` in `format` and `plain`.** Also zero
callers, and unreachable by construction: `amount` is
`INTEGER NOT NULL CHECK (amount > 0)`, every grouped `SUM` has at least one
row by definition, and the two ungrouped sums `COALESCE` in SQL. Both removed.
This one was worse than dead — returning `""` for a null meant a *blank space
where a figure belongs*, which is the quietest way for a total to be wrong.
The obligation it was pretending to discharge is now tested where it actually
lives, in `test_no_total_comes_back_null`.

**Unused import.** `money` in `tests/test_export.py`, left behind by an
earlier version.

**Dead markup — three ids the script never wrote to.** One was litter, a
hidden chip in the header. One was an id on a plain link that works without
JavaScript. The third was not dispensable at all: an empty footer span whose
emptiness was hiding a *missing feature*. `/api/reconciles` was implemented,
tested, and listed in the README, and the page never called it — so whether
the exported file adds up to what is stored was a property the app could
answer and never did. It now prints in the footer, fetched separately from
`/api/overview` because answering it means building the whole export and the
overview is the request the keypad waits on.

`test_every_id_in_the_page_is_used_by_the_script` catches the next one. An
element nobody writes to is either litter or a missing feature, and this
repository had one of each.

Notably *absent*: this app has no rate handling, no multi-format date parsing
and no accounting-negative rules, all of which the wallet app needs because it
reads other people's files. Every amount here arrives from this app's own
keypad. Writing those would have been code with no caller.

## Bloaters

**`_writing` at complexity 13.** The only function over the threshold. Cause
was duplication: the same six-field `entries.add(...)` mapping was written out
in both the single save and the queue loop. Extracted to `_record(conn, sent)`
plus `_queued_outcome(conn, item)`, which took the group to a complexity flake8
does not report and collapsed the queue loop to a comprehension.

That duplication was not cosmetic. Written twice, a field added to one path
and forgotten in the other fails nothing — entries made offline would simply
arrive without their note, looking as though the person had typed it that way.
`test_a_queued_entry_carries_every_field_the_single_save_does` now fails if
the paths diverge, and was checked against both mutations.

## Couplers

**Inappropriate intimacy across repositories.** `tests/test_export.py` has to
know the wallet importer's header names, because the CSV columns are a
contract between two independently released repos and nothing else would
notice a rename. It reads that repo's source with `ast.literal_eval` rather
than importing it — both apps have a `db`, a `money` and a `paths`, so putting
the other on `sys.path` hands one of them whichever copy was imported first —
and skips when it is not checked out.

**Feature envy — none found.** No module reaches through another to a third.

## Change preventers

**One data-directory decision, in `paths.py`.** Two modules resolving it
independently are not forced to agree, and the failure is invisible: a ledger
read from one folder while something else writes to another, with neither
looking wrong.

**One currency list, in `money.py`.** `CURRENCIES` is read by the schema
defaults, the API and the page rather than restated.

## Naming and constants

No global mutable state. Every threshold is named where it is used:
`MINOR_UNITS = 2` (written as a name, not `100`, because JPY has none and this
is the line that would change), `MAX_QUEUED = 200`, `USUAL_WINDOW_DAYS = 90`,
`USUAL_MIN_COUNT = 3`, `USUAL_LIMIT = 8`, `QUEUE_KEY`, `DB_NAME`, `ENV_VAR`.

## Bug classes

| Class | Instance | State |
|---|---|---|
| Arithmetic | Money as float — `0.1 + 0.2` makes a total disagree with the entries above it | Integer cents throughout |
| Silent failure | `parse` returning `0` for something unreadable | Raises; an entry of nothing is indistinguishable from a real one |
| Sign | `"-5"` became `5` — the strip removed the minus before it was checked | Caught before the strip and refused |
| Type | `dt.datetime` passes `isinstance(x, dt.date)`, reaching storage as `2026-08-31T14:30:00`, which sorts after `2026-08-31` — the last day of a month falls outside its own month | Coerced in `as_date`; pinned by test |
| Null | `SUM` over no rows is NULL, not 0 | `COALESCE` in SQL; asserted, not assumed |
| Injection | A typed note into `innerHTML` — the shipped hole in the face project | Everything through `esc()` |
| Concurrency | A queue flushed twice doubling every entry | `client_id` UNIQUE, minted browser-side |
| Resource | SQLite connections leaked — `with connection:` scopes a transaction, not a close | `db.session()` closes |
| Environment | Binding `0.0.0.0` with the Werkzeug debugger on | Loopback and debug-off by default, with the consequence printed when widened |
| Off-by-one | December's month range rolling into a 13th month | `month_of` tested at December and at both February lengths |

## Coverage

| Module | Statements | Covered |
|---|---|---|
| `app.py` | 92 | 100% |
| `entries.py` | 89 | 100% |
| `export.py` | 49 | 100% |
| `money.py` | 41 | 100% |
| `db.py` | 26 | 100% |
| `paths.py` | 7 | 100% |
| **Total** | **304** | **100%** |

100% is reported here as a fact about a small codebase, not as evidence the
tests are good — it was 99% before this audit, and the four uncovered lines
turned out to be two pieces of dead code and two real gaps. Chasing the number
would have meant writing tests for code that should have been deleted.

Every structural guard added here was run against a deliberately broken copy
of the code to confirm it goes red: the `datetime` coercion, the `data_dir`
default, both `COALESCE`s, the null-total check, and the shared-field test in
both directions. Four such tests written for these projects had passed
unconditionally — one compared a value to itself, one grepped a whole file and
found its string in a comment — so a guard nobody has watched fail is treated
here as not yet written.

## Maintenance

**Corrective.** The sign bug, the silent zero, the `datetime` coercion.

**Adaptive.** The export column names track the wallet importer, checked
automatically rather than by memory.

**Perfective.** The `_record` extraction; the dead flag and guards removed.

**Preventive.** `pytest.ini` pins `testpaths = tests`, so a future tool in the
project root named `test_*.py` is not imported and run — that happened in the
face project, where pytest imported a research script. `.gitignore` excludes
`data/`, `*.db` and `*.csv` before any of them exist. CI runs `pytest -q -rs`
so the two cross-repo skips are stated rather than silent.

## Second pass — 27 September 2026

The same checklist again, a fortnight on. Six bugs, all found by sending the
routes something other than what the keypad sends.

**Bugs (each with a test that failed first).**

- `money.parse` returned a whole-number amount before any check: `0` and `-5`
  became 0 and -500 cents, SQLite's `CHECK (amount > 0)` raised
  `IntegrityError`, and the route answered 500 — on the queue, a 500 that
  `app.js` reads as "batch refused" and quarantines. The browser build has no
  CHECK and would have stored the negative. Ints now go through the same
  bounds as text.
- No upper bound: past 2**63 cents SQLite raised `OverflowError`, another 500.
  `money.MAX_CENTS` is the keypad's own cap, and a test ties the two together.
- `"1e3"` parsed as 13.00, and a JSON `1e20` as 120.00: the strip joined the
  digits either side of the letter. A letter between digits is refused.
- A non-text category, note or client id was an `AttributeError` on `.strip()`
  (500, and the whole queued batch with it); `known()` did the same for a
  non-text currency. Both builds now refuse the first three with a reason and
  fall back on the fourth. A JSON body that is not an object was also a 500.
- **CSRF.** `/api/entry` fell back to `request.form`, and a cross-site form post
  needs no preflight — so any page open in the same browser could add entries
  to a Tally on 127.0.0.1. JSON only now. The other state-changing routes were
  already JSON-only or `DELETE`, both of which force a preflight.
- **CSV injection.** A Description starting `=`, `+`, `-`, `@`, tab or CR is
  prefixed with `'` in both builds.
- The habit window was 91 days (`on - 90`); it is 90 counting today, matching
  `days`. The browser build's CSV writer also failed to quote a bare `\r`,
  which Python's does; both are now pinned by the build's fixture.
- The delete handler in `app.js` called `fetch` directly and said "Deleted."
  for a 404 or a 500. It goes through `api()` now, and a structural test fails
  if a second `fetch(` appears.

**Integration.** The wallet-column contract test had been skipping locally,
because the wallet app moved `layout.py` into `ingest/`. It looks in both
places, and a new test fails if the wallet checkout is present but its lists
are not found — a skip should mean "not checked out", nothing else.

**Dispensables.** `export.month_of` had no caller outside its own tests;
removed with them. `app.config["TALLY_DIR"]` was written and never read.

**Duplicate code.** The "1st of the month to today" default was written out
five times across `entries.py` and `export.py`; it is `entries.month_so_far`
now, and `export._stored` replaces two copies of the same SUM query.

**Coverage.** 96% of 326 before (the uncovered lines were three 400 branches
in `app.py` and the insert-race path in `entries.py`); 100% after. The README
badge said 100% while the sentence under it said 96% — both say 100% now,
because both are now true.

**Not changed.** Every error the parser can raise still names the input with
`repr`, which the browser build approximates; that gap is already documented
in `money.js`. There is still no login: that is the design, and the startup
warning stands.
