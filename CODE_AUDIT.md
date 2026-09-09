# Code audit

What the checklist found in this repository, and what was done about it. Same
review the other five projects in this family had; this one was written with it
in hand, so the list is shorter and the findings are mostly things caught
before they shipped rather than after.

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
