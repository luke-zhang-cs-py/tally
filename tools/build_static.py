"""Build the browser-only copy of Tally into docs/app/.

    python tools/build_static.py

Why a script rather than a second copy of the app: the page in docs/app/ *is*
the page. Its keypad, its habit buttons, its bar charts and its offline queue
all come from the same `templates/index.html`, `static/css/style.css` and
`static/js/app.js` the Flask app serves -- copied here byte for byte and never
edited. A hand-maintained fork of a 435-line app.js is a fork that looks like
a copy, and it rots the first time somebody fixes a bug in one of them.

What the build adds is the server. Tally's server is persistence and nothing
else: app.py is eight thin routes over core/db.py, domain/entries.py,
domain/export.py and domain/money.py, and none of it needs a network. So the browser build replaces SQLite
with localStorage and answers app.js's own requests inside the page:

    tools/static_src/js/money.js       domain/money.py, ported
    tools/static_src/js/store.js       core/db.py + domain/entries.py
                                       + domain/export.py, ported
    tools/static_src/js/static-api.js  app.py's routes, as a fetch shim
    tools/static_src/js/static-ui.js   the export button and the storage note

Inlined as `.js` files rather than fetched as `.json`, deliberately: a
`fetch()` of a relative URL is blocked by the file:// origin rules, so a JSON
bundle would work on GitHub Pages and fail the moment somebody double-clicked
index.html. A `<script src>` has no such restriction, so the same directory
works both ways -- and the fetch shim matches on the tail of the path, so it
also works from a subdirectory such as /tally/app/.

--------------------------------------------------------------------------
What this refuses to ship
--------------------------------------------------------------------------
money.py's whole argument is that an amount is an integer number of cents,
because 0.1 + 0.2 is 0.30000000000000004 and a day's total that disagrees with
the rows above it costs a reader their trust in every other figure on the
screen. A port that quietly reintroduced a float, or rounded a third decimal
place instead of refusing it, would put that back on the *published* copy --
the one most people see, and the one no test in tests/ runs.

So the port is not trusted. Before a byte is written, this script starts a
real browser, loads the three JavaScript files it is about to publish, and
compares them against the real Python in three passes:

  1. **money.** Every value in `money_cases()` through `parse`, `format`,
     `plain`, `total` and `known` -- including the text of every error, since
     an error a person reads is as much the behaviour as the number is.

  2. **the queries.** One fixture of entries loaded into a real SQLite
     database and into TallyStore, then every payload compared field by
     field: the overview, the listings, the per-currency totals, the habit
     ranking, the category breakdown, the daily bars, the CSV text, the
     export filename, the reconcile figure. A GROUP BY is easy to get nearly
     right and wrong at the edges -- the wrong week boundary, a habit ranked
     by recency instead of count, a day with nothing spent omitted instead of
     shown as zero -- and "nearly" is not a property you can ship.

  3. **the routes.** The same list of HTTP requests, including the ones that
     must fail, replayed against Flask's test client and against the fetch
     shim, with status codes and bodies compared. That is the pass that keeps
     app.js -- which is byte-identical in both builds and therefore cannot
     adapt -- unable to tell which one it is running in.

Any disagreement stops the build. Nothing is written.

The browser is Playwright's Chromium, or the system Chrome if it is there.
That is a build-time dependency, not a runtime one: `pip install -r
requirements.txt` and `pytest` do not need it, and neither does the published
page. Running the JS rather than transcribing it a third time is the point --
a transcription of a port is just a third thing to get wrong.
"""

import contextlib
import datetime as dt
import io
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "docs", "app")
SRC = os.path.join(ROOT, "tools", "static_src")

REPO = "https://github.com/luke-zhang-cs-py/tally"

# Where Chrome lives on the machine this is developed on. Optional: Playwright
# ships its own Chromium and that is used when this is not here, so the build
# works on a runner too.
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# Copied unchanged from the Flask app, so both builds run the same code.
SHARED_JS = ["app.js"]
STATIC_JS = ["money.js", "store.js", "static-api.js", "static-ui.js"]

# The order the page loads them in. money.js first because store.js reads it at
# definition time; static-api.js before app.js, which calls flush() and load()
# as it finishes; static-ui.js last, because it binds controls the build's own
# banner adds and reads a store the others have to have set up.
SCRIPT_ORDER = ["js/money.js", "js/store.js", "js/static-api.js", "js/app.js",
                "js/static-ui.js"]

BANNER = "/* Generated by tools/build_static.py -- edit %s instead. */\n"

# The day both sides are told it is. Every query in entries.py is relative to
# "today", so a comparison that let the two sides ask the clock separately
# would be a comparison that failed once a year at midnight on the 1st.
FIXED_TODAY = "2026-03-18"          # a Wednesday, mid-month, mid-week


def read(path):
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def stop(message):
    raise SystemExit("build_static: " + message)


def replace_once(text, old, new, what):
    """A substitution that fails loudly if the source moved.

    Every edit below is a string match against templates/index.html. A miss is
    silent by default -- the page builds, looks nearly right, and is missing
    the one paragraph that says where the data lives. So each one is counted.
    """
    count = text.count(old)
    if count != 1:
        stop("could not %s -- expected exactly one match in "
             "templates/index.html, found %d. The template has moved; fix the "
             "anchor in tools/build_static.py.\n  looking for: %r"
             % (what, count, old[:120]))
    return text.replace(old, new)


# ---------------------------------------------------------------------------
# The fixture
# ---------------------------------------------------------------------------
# Chosen, not random. It has to exercise every branch the two sides could part
# company on:
#
#   * three currencies, so `totals` and `by_category` group and rank rather
#     than returning one row;
#   * a habit repeated four times and one repeated exactly USUAL_MIN_COUNT,
#     so the HAVING boundary is on both sides of itself, plus a near-miss at
#     two that must not appear;
#   * a triple that differs only in amount, because `usual` groups on the
#     amount as well as the category and a port that grouped on the category
#     alone would pass every other check here;
#   * dates either side of the Monday and the 1st, so a week or month
#     boundary computed differently shows up as a different total;
#   * an entry older than USUAL_WINDOW_DAYS, which must fall out of `usual`
#     and stay in the listing;
#   * notes holding a comma, a double quote and a newline, which is where two
#     CSV writers disagree -- and where two CSV *readers* disagree, since
#     `reconciles` re-reads the file it just wrote rather than re-adding the
#     numbers from memory;
#   * a note and a category with non-ASCII in them, since the file is written
#     UTF-8 on one side and by a browser on the other.
FIXTURE = [
    # clientId,  date,         amount,   category,     note,            ccy
    ("c01", "2025-12-30", "14.99", "Household", "broom", "EUR"),
    ("c02", "2026-02-11", "3.10", "Coffee", "", "EUR"),
    ("c03", "2026-02-27", "42.00", "Groceries", "big shop, end of month", "EUR"),
    ("c04", "2026-02-28", "9.99", "Transport", "", "CAD"),
    ("c05", "2026-03-01", "2.50", "Coffee", "flat white", "EUR"),
    ("c06", "2026-03-02", "2.50", "Coffee", "flat white", "EUR"),
    ("c07", "2026-03-05", "18.00", "Coffee", "beans, 1kg", "EUR"),
    ("c08", "2026-03-09", "2.50", "Coffee", "flat white", "EUR"),
    ("c09", "2026-03-11", "7.40", "Lunch", 'the "good" place', "EUR"),
    ("c10", "2026-03-12", "31.25", "Groceries", "", "USD"),
    ("c11", "2026-03-13", "1.20", "Snacks", "", "CAD"),
    ("c12", "2026-03-15", "2.50", "Coffee", "flat white", "EUR"),
    ("c13", "2026-03-16", "6.05", "Lunch", "soup\nand bread", "EUR"),
    ("c14", "2026-03-16", "55.55", "Household", "kettle", "USD"),
    ("c15", "2026-03-17", "4.75", "Drinks", "caffè corretto", "EUR"),
    ("c16", "2026-03-17", "11.11", "Café", "pâtisserie", "EUR"),
    ("c17", "2026-03-18", "3.99", "Transport", "", "EUR"),
    ("c18", "2026-03-18", "0.85", "Snacks", "", "EUR"),
    ("c19", "2026-03-18", "23.00", "Drinks", "round", "CAD"),
    ("c20", "2026-03-18", "8.20", "Lunch", "", "USD"),

    # The HAVING boundary, from both sides. Three of these is a habit and
    # earns a button; two of the next is a coincidence and must not.
    ("c21", "2026-03-04", "1.60", "Transport", "tram", "EUR"),
    ("c22", "2026-03-10", "1.60", "Transport", "tram", "EUR"),
    ("c23", "2026-03-14", "1.60", "Transport", "tram", "EUR"),
    ("c24", "2026-03-06", "4.40", "Drinks", "pint", "EUR"),
    ("c25", "2026-03-13", "4.40", "Drinks", "pint", "EUR"),

    # Same category, same currency, same note, different amount. `usual`
    # groups on all four, and a port that grouped on the category alone would
    # pass every other check in this file: it would just quietly offer one
    # button where there should be two, and put the wrong price on it.
    ("c26", "2026-03-03", "2.80", "Coffee", "flat white", "EUR"),
    ("c27", "2026-03-07", "2.80", "Coffee", "flat white", "EUR"),
    ("c28", "2026-03-10", "2.80", "Coffee", "flat white", "EUR"),
]


def fixture_for_js():
    return [{"clientId": mark, "date": day, "amount": amount,
             "category": category, "note": note, "currency": currency}
            for mark, day, amount, category, note, currency in FIXTURE]


# The requests replayed against both builds, in order. The failures are the
# point of most of them: app.js tells a refused batch apart from an
# unreachable server by the status code, so a shim that answered 200 where
# Flask answers 400 would quarantine entries somebody typed.
REQUESTS = [
    ("GET", "/api/overview", None),
    ("GET", "/api/overview?on=2026-03-01", None),
    ("GET", "/api/overview?on=2026-02-11", None),
    ("GET", "/api/overview?on=nonsense", None),
    ("GET", "/api/overview?on=2026-02-30", None),
    ("GET", "/api/entries", None),
    ("GET", "/api/entries?limit=3", None),
    ("GET", "/api/entries?limit=0", None),
    ("GET", "/api/entries?limit=-1", None),
    ("GET", "/api/entries?limit=999", None),
    ("GET", "/api/entries?limit=", None),
    ("GET", "/api/entries?limit=abc", None),
    ("GET", "/api/entries?limit=1.0", None),
    ("GET", "/api/entries?limit=1e3", None),
    ("GET", "/api/entries?limit=0x10", None),
    ("GET", "/api/entries?limit=%20+7%20", None),
    ("GET", "/api/entries?on=2026-03-18", None),
    ("GET", "/api/entries?on=2026-03-19&limit=5", None),
    ("GET", "/api/entries?on=rubbish", None),
    ("GET", "/api/reconciles", None),

    # Writing. Every one of these changes the state both sides carry into the
    # next request, which is the only way to find a divergence that needs two
    # steps to show up.
    ("POST", "/api/entry", {"clientId": "w01", "amount": "5.45",
                            "category": "Lunch", "note": "posted",
                            "currency": "EUR", "date": "2026-03-18"}),
    ("POST", "/api/entry", {"clientId": "w01", "amount": "5.45",
                            "category": "Lunch", "note": "posted",
                            "currency": "EUR", "date": "2026-03-18"}),
    ("POST", "/api/entry", {"clientId": "w02", "amount": "5.45",
                            "category": "Lunch", "currency": "gbp",
                            "date": "2026-03-17"}),
    ("POST", "/api/entry", {"clientId": "w03", "amount": "-2.00",
                            "category": "Lunch", "date": "2026-03-18"}),
    ("POST", "/api/entry", {"clientId": "w04", "amount": "1.234",
                            "category": "Lunch", "date": "2026-03-18"}),
    ("POST", "/api/entry", {"clientId": "w05", "amount": "1.00",
                            "category": "   ", "date": "2026-03-18"}),
    ("POST", "/api/entry", {"clientId": "w06", "amount": "1.00",
                            "category": "Lunch", "date": "2026-03-19"}),
    ("POST", "/api/entry", {"clientId": "w07", "amount": "1.00",
                            "category": "Lunch", "date": "the 4th"}),
    ("POST", "/api/entry", {"clientId": "w08", "amount": None,
                            "category": "Lunch"}),
    ("POST", "/api/entries", {"entries": "not a list"}),
    ("POST", "/api/entries", {}),
    ("POST", "/api/entries", {"entries": []}),
    ("POST", "/api/entries", {"entries": [
        {"clientId": "q01", "amount": "2.50", "category": "Coffee",
         "note": "flat white", "currency": "EUR", "date": "2026-03-18"},
        {"clientId": "q02", "amount": "nope", "category": "Coffee",
         "date": "2026-03-18"},
        {"clientId": "c05", "amount": "2.50", "category": "Coffee",
         "note": "flat white", "currency": "EUR", "date": "2026-03-01"},
        5,
        None,
        [],
        {"amount": "0.05", "category": "Snacks", "clientId": "q03",
         "date": "2026-03-18"},
    ]}),
    ("DELETE", "/api/entry/3", None),
    ("DELETE", "/api/entry/3", None),
    ("DELETE", "/api/entry/9999", None),

    # And the whole picture again, after all of it.
    ("GET", "/api/overview", None),
    ("GET", "/api/entries?limit=100", None),
    ("GET", "/api/reconciles", None),
    ("GET", "/export.csv", None),
    ("GET", "/export.csv?first=2026-02-01&last=2026-03-18", None),
    ("GET", "/export.csv?first=2026-03-18&last=2026-03-18", None),
    ("GET", "/export.csv?first=2026-04-01&last=2026-04-30", None),
    ("GET", "/export.csv?first=broken", None),
]


# ---------------------------------------------------------------------------
# The cases for money.py
# ---------------------------------------------------------------------------
def money_cases():
    """Every call the two money implementations are compared on.

    Three deliberate absences, all of them documented at the top of money.js
    as places the port cannot follow the original rather than places nobody
    thought about:

      * **no integral float.** Python has int and float and JavaScript has one
        number type, so money.py's `isinstance(text, int)` short-circuit --
        `parse(0)` is 0, `parse(0.0)` raises -- cannot be told apart in the
        port. Integral *ints* are here and behave identically.
      * **no non-ASCII digit.** Python's `\\d` matches every Unicode decimal,
        JavaScript's matches 0-9, so `parse('\\u0660')` is a zero on one side
        and "no digits" on the other.
      * **no unprintable character in a value that ends up in an error
        message.** The messages interpolate `{text!r}`, and Python's repr
        escapes those where the port's does not.

    A case list that included them would be asserting a lie, so each is
    excluded here and stated there, which is the only honest way round.
    """
    texts = [
        None, "", " ", "0", "0.00", "0.0", "0.001", "00", "1", "01", "1.",
        ".5", ".05", ".005", "1.5", "1.50", "1.05", "12.34", "12,34",
        "1.234", "1,234", "1,2,3", "-1", "- 1", "1-", "1e3", "abc", "1abc",
        "abc1", "\u20ac4.20", "$1.99", "CA$ 12,00", "1 000.50", "9999999.99",
        "99999999", "0.99", " 7.25 ", "\t3.30\n", "1.999", "++5", "5%",
        "5.5.5", ",", ".", "..", "1..2", "0.", "000.10", "1'000.20",
        "it's 3.50", 'say "4.50"', True, False,
        0, 1, 7, -1, 1234567, 99999999,
        12.34, 0.5, 0.1, 0.25, 1.05, 99999999.99, 0.30000000000000004,
    ]
    # And every amount from 0.00 to 4.99 plus a spread above it, written the
    # way the keypad writes them: the systematic half of the list, so the
    # handpicked half above is only doing the work it is actually for.
    for cents in list(range(0, 500)) + [
            1000, 1001, 9999, 10000, 100000, 999999, 1000000, 123456789]:
        texts.append("%d.%02d" % (cents // 100, cents % 100))

    # For `format`, which interpolates the code straight into the string when
    # it has no symbol for it: `money.format(1, None)` is a TypeError on the
    # Python side (`None + ' '`), so passing one here would be comparing two
    # different questions. `known` is where the non-strings belong -- it is
    # the function whose whole job is to cope with them.
    symbols = ["EUR", "CAD", "USD", "JPY", "GBP", "", " eur ", "eur", "xyz",
               "constructor", "toString", "__proto__", "\u20ac", "EURO",
               "  ", "usd"]
    # `known(True)` is not here either: `(True or "").strip()` is an
    # AttributeError in Python, and a bool is not a currency code in any case.
    codes = symbols + [None, 0, False]
    amounts = [0, 1, 5, 9, 10, 11, 50, 99, 100, 101, 250, 999, 1000, 1001,
               9999, 10000, 99999, 100000, 100001, 999999, 1000000,
               12345678, 123456789, 999999999, -1, -9, -100, -1234, -999999,
               -123456789]

    cases = []
    for text in texts:
        cases.append({"fn": "parse", "args": [text]})
    for cents in amounts:
        cases.append({"fn": "plain", "args": [cents]})
        cases.append({"fn": "format", "args": [cents]})
        for code in symbols:
            cases.append({"fn": "format", "args": [cents, code]})
    for code in codes:
        cases.append({"fn": "known", "args": [code]})
    for group in ([], [0], [1, 2, 3], [None], [1, None, 2],
                  [-5, 5], [100] * 40, [999999999, 1], [-1, -2, -3],
                  [0, 0, 0], [123456789, 987654321]):
        cases.append({"fn": "total", "args": [group]})
    return cases


def money_in_python(cases):
    from domain import money

    out = []
    for case in cases:
        function = getattr(money, case["fn"])
        try:
            out.append({"ok": function(*case["args"])})
        except Exception as bad:                       # noqa: BLE001 -- the
            # point is to compare *whatever* it raised, including the type.
            out.append({"error": type(bad).__name__, "says": str(bad)})
    return out


# ---------------------------------------------------------------------------
# The JavaScript side
# ---------------------------------------------------------------------------
MONEY_JS = """(cases) => cases.map((c) => {
  try {
    return { ok: TallyMoney[c.fn].apply(null, c.args) };
  } catch (e) {
    return { error: e.name, says: e.message };
  }
})"""

STORE_JS = """(setup) => {
  /* A store of its own, and a fixed idea of what day it is, so the
     comparison is against the fixture and not against whatever is in the
     browser this happens to be running in. */
  TallyStore._useBackend(null, setup.today);
  setup.entries.forEach((entry) => { TallyStore.add(entry); });
  const out = { added: TallyStore.count(), payloads: {} };
  setup.calls.forEach((call) => {
    try {
      out.payloads[call.name] = { ok: TallyStore[call.fn].apply(null, call.args) };
    } catch (e) {
      out.payloads[call.name] = { error: e.name, says: e.message };
    }
  });
  return out;
}"""

ROUTES_JS = """async (requests) => {
  const out = [];
  for (const request of requests) {
    const init = { method: request.method };
    if (request.body !== null && request.body !== undefined) {
      init.headers = { 'Content-Type': 'application/json' };
      init.body = JSON.stringify(request.body);
    }
    const reply = await window.fetch(request.url, init);
    const row = { status: reply.status, json: null, text: null,
                  disposition: null };
    if (reply.headers && reply.headers.get) {
      row.disposition = reply.headers.get('content-disposition');
    }
    if (request.csv && reply.status === 200) {
      row.text = await reply.text();
    } else {
      row.json = await reply.json();
    }
    out.push(row);
  }
  return out;
}"""


@contextlib.contextmanager
def browser(sources):
    """A page with the bundle's JavaScript in it, and no tolerance for errors.

    about:blank, so there is no origin -- which is why store.js has to have a
    memory fallback for a localStorage that throws, and why this harness is
    the first thing that would notice if it did not.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        stop("this build runs the JavaScript it is about to publish against "
             "the Python it was ported from, and that needs a browser.\n"
             "  pip install playwright && python -m playwright install chromium\n"
             "There is no --skip-checks flag on purpose: an unchecked port of "
             "money.py is the one thing this script exists to prevent.")

    page_html = ("<!doctype html><html><head><meta charset=\"utf-8\">"
                 "<title>build check</title></head><body>"
                 + "".join("<script>\n%s\n</script>" % text for text in sources)
                 + "</body></html>")

    with sync_playwright() as pw:
        options = {}
        if os.path.exists(CHROME):
            options["executable_path"] = CHROME
        instance = pw.chromium.launch(**options)
        try:
            page = instance.new_page()
            problems = []
            page.on("pageerror", lambda bad: problems.append(str(bad)))
            page.on("console", lambda msg: problems.append(msg.text)
                    if msg.type == "error" else None)
            page.set_content(page_html)
            if problems:
                stop("the bundle's JavaScript does not load:\n  "
                     + "\n  ".join(problems))
            yield page, problems
        finally:
            instance.close()


# ---------------------------------------------------------------------------
# Comparing
# ---------------------------------------------------------------------------
def scrub(value):
    """Drop the one field that cannot match: when the row was written.

    `created_at` is `datetime.now()` on one side and `new Date()` on the
    other, a few milliseconds apart, and nothing in app.js reads it. Removed
    rather than fudged with a tolerance, because a tolerance is a thing that
    later hides a real difference.
    """
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if k != "created_at"}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def _keys_differ(want, got, path, found):
    for key in sorted(set(want) | set(got)):
        where = "%s.%s" % (path, key)
        if key not in want:
            found.append("%s: only the browser has it (%r)" % (where, got[key]))
        elif key not in got:
            found.append("%s: only python has it (%r)" % (where, want[key]))
        else:
            differences(want[key], got[key], where, found)


def _items_differ(want, got, path, found):
    if len(want) != len(got):
        found.append("%s: python has %d, browser has %d\n    python:  %r"
                     "\n    browser: %r"
                     % (path or ".", len(want), len(got), want, got))
        return
    for index, (mine, yours) in enumerate(zip(want, got)):
        differences(mine, yours, "%s[%d]" % (path, index), found)


def _same_leaf(want, got):
    """Whether two values at the bottom of a payload agree.

    Numbers compare across int and float, because JSON has one number type
    and the two sides hand back whichever fits. A bool never compares equal
    to a number, though: `True == 1` in Python, and `{"agrees": true}` coming
    back as `{"agrees": 1}` is precisely the kind of thing this is for.
    """
    if isinstance(want, bool) != isinstance(got, bool):
        return False
    return want == got


def differences(want, got, path="", found=None):
    """Every place two payloads disagree, with the path to each."""
    if found is None:
        found = []
    if isinstance(want, dict) and isinstance(got, dict):
        _keys_differ(want, got, path, found)
    elif isinstance(want, list) and isinstance(got, list):
        _items_differ(want, got, path, found)
    elif not _same_leaf(want, got):
        found.append("%s: python %r, browser %r" % (path or ".", want, got))
    return found


def report(what, wrong, extra=""):
    if not wrong:
        return
    shown = wrong[:25]
    stop("%s: the browser build disagrees with the Python in %d place%s.\n"
         "Nothing has been written to docs/app.\n%s  %s%s"
         % (what, len(wrong), "" if len(wrong) == 1 else "s", extra,
            "\n  ".join(shown),
            "\n  ... and %d more" % (len(wrong) - len(shown))
            if len(wrong) > len(shown) else ""))


# ---------------------------------------------------------------------------
# Pass 1 -- money.py
# ---------------------------------------------------------------------------
def check_money(page):
    cases = money_cases()
    theirs = page.evaluate(MONEY_JS, cases)
    ours = money_in_python(cases)

    wrong = []
    for case, mine, yours in zip(cases, ours, theirs):
        label = "money.%s(%s)" % (case["fn"],
                                  ", ".join(repr(a) for a in case["args"]))
        if "error" in mine or "error" in yours:
            wrong += error_difference(label, mine, yours)
        elif differences(mine["ok"], yours["ok"]):
            wrong.append("%s: python %r, browser %r"
                         % (label, mine["ok"], yours["ok"]))
    report("money.py vs money.js", wrong)
    return len(cases)


def error_difference(label, mine, yours):
    """Compare two failures.

    The type always, and the message too whenever it is one of the app's own
    errors -- those are written to be read by whoever is standing there with
    their phone out, so a port that raises the right class with the wrong
    sentence has not ported it. A TypeError is compared on the class alone:
    both sides refuse `format(None)`, but one of them is the interpreter
    complaining about `<` and the other is an explicit throw, and no wording
    will ever make those the same string.
    """
    if "error" not in mine:
        return ["%s: python returned %r, browser raised %s: %s"
                % (label, mine["ok"], yours["error"], yours["says"])]
    if "error" not in yours:
        return ["%s: python raised %s: %s, browser returned %r"
                % (label, mine["error"], mine["says"], yours["ok"])]
    if mine["error"] != yours["error"]:
        return ["%s: python raised %s, browser raised %s"
                % (label, mine["error"], yours["error"])]
    if mine["error"] in ("MoneyError", "EntryError") \
            and mine["says"] != yours["says"]:
        return ["%s: python says %r, browser says %r"
                % (label, mine["says"], yours["says"])]
    return []


# ---------------------------------------------------------------------------
# Pass 2 -- the queries
# ---------------------------------------------------------------------------
# name -> (python callable name, javascript name, arguments). The Python side
# takes a connection first; the JavaScript side does not.
STORE_CALLS = [
    ("recent.default", "recent", "recent", []),
    ("recent.limit3", "recent", "recent", [3]),
    ("recent.limit0", "recent", "recent", [0]),
    ("recent.unlimited", "recent", "recent", [-1]),
    ("recent.onaday", "recent", "recent", [50, "2026-03-18"]),
    ("recent.emptyday", "recent", "recent", [50, "2026-03-19"]),
    ("usual.default", "usual", "usual", []),
    ("usual.limit2", "usual", "usual", [2]),
    ("usual.shortwindow", "usual", "usual", [8, 10]),
    ("usual.boundary", "usual", "usual", [8, 14]),
    ("usual.longwindow", "usual", "usual", [8, 400]),
    ("usual.backdated", "usual", "usual", [8, 90, "2026-03-10"]),
    ("categories", "categories", "categories", []),
    ("totals.today", "totals", "totals", []),
    ("totals.monday", "totals", "totals", ["2026-03-16"]),
    ("totals.sunday", "totals", "totals", ["2026-03-15"]),
    ("totals.firstofmonth", "totals", "totals", ["2026-03-01"]),
    ("totals.lastofmonth", "totals", "totals", ["2026-02-28"]),
    ("totals.leapday", "totals", "totals", ["2026-01-31"]),
    ("byCategory.default", "by_category", "byCategory", []),
    ("byCategory.february", "by_category", "byCategory",
     ["2026-02-01", "2026-02-28"]),
    ("byCategory.oneday", "by_category", "byCategory",
     ["2026-03-18", "2026-03-18"]),
    ("byCategory.nothing", "by_category", "byCategory",
     ["2025-01-01", "2025-01-31"]),
    ("days.default", "days", "days", []),
    ("days.one", "days", "days", [1]),
    ("days.sixty", "days", "days", [60]),
    ("days.overamonth", "days", "days", [40, "2026-03-05"]),
]

EXPORT_CALLS = [
    ("csv.thismonth", "as_csv", "asCsv", []),
    ("csv.wholerange", "as_csv", "asCsv", ["2025-01-01", "2026-12-31"]),
    ("csv.oneday", "as_csv", "asCsv", ["2026-03-16", "2026-03-16"]),
    ("csv.nothing", "as_csv", "asCsv", ["2024-01-01", "2024-01-02"]),
    ("filename.default", "filename", "filename", []),
    ("filename.oneday", "filename", "filename",
     ["2026-03-18", "2026-03-18"]),
    ("filename.range", "filename", "filename",
     ["2026-02-01", "2026-03-18"]),
    ("summary.default", "summary", "summary", []),
    ("summary.february", "summary", "summary",
     ["2026-02-01", "2026-02-28"]),
]


def check_queries(page):
    """The whole of entries.py and export.py, over one fixture, both ways."""
    from core import db
    from domain import entries
    from domain import export

    calls = ([{"name": name, "fn": js, "args": args}
              for name, _py, js, args in STORE_CALLS]
             + [{"name": name, "fn": js, "args": args}
                for name, _py, js, args in EXPORT_CALLS]
             + [{"name": "reconciles", "fn": "reconciles", "args": []},
                {"name": "overview", "fn": "overview", "args": []},
                {"name": "overview.on", "fn": "overview",
                 "args": ["2026-03-01"]}])

    theirs = page.evaluate(STORE_JS, {"today": FIXED_TODAY,
                                      "entries": fixture_for_js(),
                                      "calls": calls})

    directory = tempfile.mkdtemp(prefix="tally-build-")
    real_today = entries.today
    entries.today = lambda: dt.date.fromisoformat(FIXED_TODAY)
    try:
        with db.session(directory) as conn:
            for mark, day, amount, category, note, currency in FIXTURE:
                entries.add(conn, amount=amount, category=category, note=note,
                            currency=currency, spent_on=day, client_id=mark)

            ours = {}
            for name, py, _js, args in STORE_CALLS:
                ours[name] = {"ok": getattr(entries, py)(conn, *args)}
            for name, py, _js, args in EXPORT_CALLS:
                ours[name] = {"ok": getattr(export, py)(conn, *args)
                              if py != "filename" else export.filename(*args)}
            agrees, from_file, stored = export.reconciles(conn)
            ours["reconciles"] = {"ok": {"agrees": agrees, "file": from_file,
                                         "stored": stored}}
            ours["overview"] = {"ok": overview_payload(conn, None)}
            ours["overview.on"] = {"ok": overview_payload(conn, "2026-03-01")}

            refuse_ties(ours)
    finally:
        entries.today = real_today
        shutil.rmtree(directory, ignore_errors=True)

    if theirs["added"] != len(FIXTURE):
        stop("the browser store took %d of the %d fixture entries -- the "
             "comparison below would be against the wrong data"
             % (theirs["added"], len(FIXTURE)))

    wrong = []
    for name in sorted(ours):
        mine, yours = ours[name], theirs["payloads"].get(name)
        if yours is None:
            wrong.append("%s: the browser answered nothing" % name)
        elif "error" in yours:
            wrong.append("%s: browser raised %s: %s"
                         % (name, yours["error"], yours["says"]))
        else:
            wrong += differences(scrub(mine["ok"]), scrub(yours["ok"]), name)
    report("entries.py/export.py vs store.js", wrong)
    return len(ours)


def overview_payload(conn, on):
    """app.py's /api/overview body, built here so the shape is compared even
    though this pass does not go through Flask."""
    from domain import entries
    from domain import export
    from domain import money
    return {
        "today": entries.today().isoformat(),
        "totals": entries.totals(conn, on),
        "usual": entries.usual(conn),
        "categories": entries.categories(conn),
        "recent": entries.recent(conn, limit=25),
        "byCategory": entries.by_category(conn),
        "days": entries.days(conn),
        "currencies": list(money.CURRENCIES),
        "defaultCurrency": money.DEFAULT_CURRENCY,
        "export": export.summary(conn),
    }


def refuse_ties(payloads):
    """Stop if the fixture makes any comparison depend on a coin toss.

    SQLite leaves tied rows in whatever order it happened to produce them;
    store.js breaks the same ties on a second key so the page is at least
    stable between reloads. Neither is wrong, and a fixture that ties would
    make this build fail or pass depending on which. So the fixture is
    required not to tie, and the requirement is enforced rather than
    remembered.
    """
    tied = []

    def look(name, rows, key, label):
        seen = {}
        for row in rows:
            mark = key(row)
            if mark in seen:
                tied.append("%s: %s and %s both %r"
                            % (name, seen[mark], label(row), mark))
            seen[mark] = label(row)

    for name, body in sorted(payloads.items()):
        got = body["ok"]
        if name.startswith("totals."):
            for window in ("today", "week", "month"):
                look("%s/%s" % (name, window), got[window],
                     lambda row: row["cents"], lambda row: row["currency"])
        elif name.startswith("byCategory."):
            look(name, got, lambda row: row["cents"],
                 lambda row: "%s/%s" % (row["category"], row["currency"]))
        elif name.startswith("usual."):
            look(name, got, lambda row: (row["times"], row["last_on"]),
                 lambda row: "%s %s" % (row["category"], row["amount"]))

    if tied:
        stop("the comparison fixture ties, so the order of these rows is "
             "SQLite's choice on one side and store.js's on the other and "
             "this build would pass or fail at random. Nudge an amount in "
             "FIXTURE:\n  " + "\n  ".join(tied))


# ---------------------------------------------------------------------------
# Pass 3 -- the routes
# ---------------------------------------------------------------------------
def check_routes(page):
    """The same requests, to Flask and to the shim, with the same state."""
    import app as flask_app
    from core import db
    from domain import entries

    wanted = [{"method": method, "url": url, "body": body,
               "csv": url.startswith("/export.csv")}
              for method, url, body in REQUESTS]

    # The browser is already holding the fixture from pass 2, in the same
    # order and with the same ids, so the two sides start level.
    theirs = page.evaluate(ROUTES_JS, wanted)

    directory = tempfile.mkdtemp(prefix="tally-routes-")
    real_today = entries.today
    entries.today = lambda: dt.date.fromisoformat(FIXED_TODAY)
    try:
        with db.session(directory) as conn:
            for mark, day, amount, category, note, currency in FIXTURE:
                entries.add(conn, amount=amount, category=category, note=note,
                            currency=currency, spent_on=day, client_id=mark)

        client = flask_app.create_app(directory).test_client()
        ours = []
        for request in wanted:
            extra = {} if request["body"] is None else {"json": request["body"]}
            reply = client.open(request["url"], method=request["method"],
                                **extra)
            row = {"status": reply.status_code, "json": None, "text": None,
                   "disposition": reply.headers.get("Content-Disposition")}
            if request["csv"] and reply.status_code == 200:
                row["text"] = reply.get_data(as_text=True)
            else:
                row["json"] = reply.get_json()
            ours.append(row)
    finally:
        entries.today = real_today
        shutil.rmtree(directory, ignore_errors=True)

    wrong = []
    for request, mine, yours in zip(wanted, ours, theirs):
        label = "%s %s" % (request["method"], request["url"])
        wrong += differences(scrub(mine), scrub(yours), label)
    report("app.py vs static-api.js", wrong,
           "app.js is byte-identical in both builds, so a difference here is "
           "one it cannot adapt to.\n")
    return len(wanted)


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------
def page_html(template):
    text = template

    text = replace_once(
        text, "<title>Tally</title>", "<title>Tally \u2014 browser build</title>",
        "retitle the page")

    text = replace_once(
        text,
        "<link rel=\"stylesheet\" href=\"{{ url_for('static', "
        "filename='css/style.css') }}\">",
        '<link rel="stylesheet" href="css/style.css">\n'
        '<link rel="stylesheet" href="css/static.css">',
        "point the stylesheet at the copied CSS")

    text = replace_once(text, "</header>\n", "</header>\n\n" + banner(),
                        "add the build banner under the header")

    text = replace_once(
        text,
        '<a class="chip button" href="/export.csv">Export CSV</a>',
        '<!-- A button, not a link: there is no server to answer /export.csv,\n'
        '         so static-ui.js builds the same file here and hands it over\n'
        '         as a blob. href="#" rather than the path, so a page whose\n'
        '         script failed shows a dead control instead of a 404. -->\n'
        '    <a class="chip button" id="exportCsv" href="#">Export CSV</a>',
        "turn the export link into a button")

    scripts = "\n".join('<script src="%s"></script>' % name
                        for name in SCRIPT_ORDER)
    text = replace_once(
        text,
        "<script src=\"{{ url_for('static', filename='js/app.js') }}\"></script>",
        "<!-- Order matters. money.js is read by store.js as it defines\n"
        "     itself; static-api.js installs the fetch shim before app.js\n"
        "     runs and immediately calls it; static-ui.js binds the controls\n"
        "     this build adds and goes last. app.js itself is the Flask app's\n"
        "     file, copied byte for byte -- see tools/build_static.py. -->\n"
        + scripts,
        "rewrite the script tags")

    left = re.findall(r"\{\{.*?\}\}|\{%.*?%\}", text)
    if left:
        stop("unresolved Jinja left in the page: %r" % left)
    return text


def banner():
    return """<!-- Added by tools/build_static.py. The Flask app does not have this
     paragraph because the Flask app does not need it: it writes to a SQLite
     file you can see, back up and copy. This build cannot, and the two pages
     are otherwise identical, so the difference has to be said out loud
     before somebody records a fortnight of spending into a browser tab. -->
<div class="staticBanner">
  <b>This is the browser-only build.</b> There is no server and no database.
  Your entries are held in <b>this browser's storage, on this device, and
  nowhere else</b> — nothing is uploaded, nothing syncs between your phone and
  your laptop, and clearing site data for this page deletes them. <b>Export the
  CSV</b> if you want a copy that outlives the tab; it is the same file the
  Flask app produces, and it still imports straight into the wallet app.
  The arithmetic is not re-done by eye: <code>money.js</code> is a port of
  <code>money.py</code>'s integer cents, and <code>tools/build_static.py</code>
  runs both against each other and refuses to publish this page if they
  disagree about a single amount, a single total or a single error message.
  <a href="%s">Source, and the Flask app that keeps it in SQLite &rarr;</a>
  <span class="warn" id="staticWarn"></span>
  <button type="button" class="forget" id="forget">Forget everything in this browser</button>
</div>
""" % REPO


# ---------------------------------------------------------------------------
def prune(kept):
    """Delete anything left in docs/app/ from an older build.

    Without this a renamed file lives on and is still loaded, which is the one
    failure mode a generated directory has that a hand-written one does not.
    """
    if not os.path.isdir(OUT):
        return
    for here, _dirs, names in os.walk(OUT):
        for name in names:
            path = os.path.join(here, name)
            if os.path.relpath(path, OUT).replace("\\", "/") not in kept:
                os.remove(path)
                print("  removed stale %s" % os.path.relpath(path, OUT))


def collect():
    """Everything the bundle is made of, as {path in docs/app: text}."""
    written = {}
    for name in SHARED_JS:
        written["js/" + name] = (
            BANNER % ("static/js/" + name)
            + read(os.path.join(ROOT, "static", "js", name)))
    for name in STATIC_JS:
        written["js/" + name] = read(os.path.join(SRC, "js", name))
    written["css/style.css"] = (
        BANNER % "static/css/style.css"
        + read(os.path.join(ROOT, "static", "css", "style.css")))
    written["css/static.css"] = read(os.path.join(SRC, "css", "static.css"))
    written["index.html"] = page_html(
        read(os.path.join(ROOT, "templates", "index.html")))
    return written


def main():
    written = collect()

    # The three files the checks are actually about, in load order, exactly as
    # they will be published -- not the sources they were read from.
    harness = [written["js/money.js"], written["js/store.js"],
               written["js/static-api.js"], written["js/static-ui.js"]]

    print("checking the port against the Python it came from...")
    with browser(harness) as (page, problems):
        values = check_money(page)
        print("  money.py  == money.js        %d values, and the wording of "
              "every error" % values)
        payloads = check_queries(page)
        print("  entries/export == store.js   %d payloads over %d entries"
              % (payloads, len(FIXTURE)))
        routes = check_routes(page)
        print("  app.py == static-api.js      %d requests, replayed against "
              "both" % routes)
        if problems:
            stop("the bundle's JavaScript logged errors while being "
                 "checked:\n  " + "\n  ".join(problems))

    for name, body in sorted(written.items()):
        write(os.path.join(OUT, name.replace("/", os.sep)), body)
    prune(set(written))

    total = sum(len(body.encode("utf-8")) for body in written.values())
    print("wrote %d files to docs/app (%.0f KB)" % (len(written), total / 1024.0))
    print("  serve it:  python -m http.server -d docs 8000  ->  "
          "http://127.0.0.1:8000/app/")


if __name__ == "__main__":
    main()
