"""The browser build, and the check that refuses to publish a wrong port.

tools/build_static.py runs the published JavaScript in a real browser and
compares it against the Python. These tests are about the *build*, not the
JavaScript: that it notices every kind of disagreement, says where, and
writes nothing when there is one.

No browser is started. The page object the build talks to is replaced by
`PythonPage`, a stand-in that answers every evaluate() with the Python
implementation itself -- a port that is exactly right, by construction. Each
test that needs a wrong port takes that and breaks one thing. Playwright is
stubbed in sys.modules, so none of this needs it installed, and every file
the build writes is pointed into tmp_path.
"""
import contextlib
import datetime as dt
import json
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as web                  # noqa: E402
from core import db                 # noqa: E402
from domain import entries          # noqa: E402
from tools import build_static as build   # noqa: E402

FIXED = dt.date.fromisoformat(build.FIXED_TODAY)


# ------------------------------------------------------------ the stand-in

@contextlib.contextmanager
def today_is(day):
    real = entries.today
    entries.today = lambda: dt.date.fromisoformat(day)
    try:
        yield
    finally:
        entries.today = real


def as_json(value):
    """What page.evaluate hands back: the value after a trip through JSON."""
    return json.loads(json.dumps(value))


class PythonPage:
    """A browser whose JavaScript is the Python. Agrees with everything."""

    def __init__(self, workdir):
        self.workdir = workdir
        self.handlers = {}
        self.content = None
        self.used = 0

    def _fresh(self):
        self.used += 1
        path = os.path.join(str(self.workdir), "page-%d" % self.used)
        os.makedirs(path)
        return path

    # The two calls browser() makes on a page.
    def on(self, event, handler):
        self.handlers[event] = handler

    def set_content(self, html):
        self.content = html

    def evaluate(self, script, arg):
        if script == build.MONEY_JS:
            out = build.money_in_python(arg)
        elif script == build.STORE_JS:
            out = self._store(arg)
        else:
            assert script == build.ROUTES_JS
            out = self._routes(arg)
        return self.tamper(script, as_json(out))

    def tamper(self, script, out):
        return out

    def _load(self, conn, rows):
        for row in rows:
            entries.add(conn, amount=row["amount"], category=row["category"],
                        note=row["note"], currency=row["currency"],
                        spent_on=row["date"], client_id=row["clientId"])

    def _store(self, setup):
        from domain import export
        python_name = {name: py for name, py, _js, _args
                       in build.STORE_CALLS + build.EXPORT_CALLS}
        out = {"added": len(setup["entries"]), "payloads": {}}
        with today_is(setup["today"]), db.session(self._fresh()) as conn:
            self._load(conn, setup["entries"])
            for call in setup["calls"]:
                name, args = call["name"], call["args"]
                if name == "reconciles":
                    agrees, from_file, stored = export.reconciles(conn)
                    got = {"agrees": agrees, "file": from_file,
                           "stored": stored}
                elif name.startswith("overview"):
                    got = build.overview_payload(conn, *(args or [None]))
                elif python_name[name] == "filename":
                    got = export.filename(*args)
                elif hasattr(entries, python_name[name]):
                    got = getattr(entries, python_name[name])(conn, *args)
                else:
                    got = getattr(export, python_name[name])(conn, *args)
                out["payloads"][name] = {"ok": got}
        return out

    def _routes(self, wanted):
        directory = self._fresh()
        out = []
        with today_is(build.FIXED_TODAY):
            with db.session(directory) as conn:
                self._load(conn, build.fixture_for_js())
            client = web.create_app(directory).test_client()
            for request in wanted:
                extra = ({} if request["body"] is None
                         else {"json": request["body"]})
                reply = client.open(request["url"], method=request["method"],
                                    **extra)
                row = {"status": reply.status_code, "json": None,
                       "text": None,
                       "disposition": reply.headers.get("Content-Disposition")}
                if request["csv"] and reply.status_code == 200:
                    row["text"] = reply.get_data(as_text=True)
                else:
                    row["json"] = reply.get_json()
                out.append(row)
        return out


class Broken(PythonPage):
    """A port with one thing wrong, chosen by the test."""

    def __init__(self, workdir, script, change):
        super().__init__(workdir)
        self.script, self.change = script, change

    def tamper(self, script, out):
        if script == self.script:
            self.change(out)
        return out


@pytest.fixture(autouse=True)
def scratch(tmp_path, monkeypatch):
    """The build's own temporary databases go under tmp_path too."""
    made = []

    def mkdtemp(prefix=""):
        path = str(tmp_path / ("%s%d" % (prefix, len(made))))
        os.makedirs(path)
        made.append(path)
        return path

    monkeypatch.setattr(build.tempfile, "mkdtemp", mkdtemp)
    return made


@pytest.fixture
def page(tmp_path):
    return PythonPage(tmp_path / "browser")


def stopped_with(call, *args):
    with pytest.raises(SystemExit) as stop:
        call(*args)
    message = str(stop.value)
    assert message.startswith("build_static: ")
    return message


# ------------------------------------------------------------ small parts

def test_write_makes_the_folder_and_uses_unix_newlines(tmp_path):
    path = str(tmp_path / "deep" / "er" / "file.js")
    build.write(path, "a\nb\n")
    with open(path, "rb") as handle:
        assert handle.read() == b"a\nb\n"
    assert build.read(path) == "a\nb\n"


def test_replace_once_refuses_a_missing_or_doubled_anchor():
    """A silent miss builds a page missing the paragraph about where the
    data lives, so a miss is an error, and so is an anchor that matches
    twice."""
    assert build.replace_once("a b c", "b", "B", "x") == "a B c"
    message = stopped_with(build.replace_once, "a c", "b", "B",
                           "find the b")
    assert "could not find the b" in message and "found 0" in message
    assert "found 2" in stopped_with(build.replace_once, "b b", "b", "B", "x")


def test_the_fixture_goes_to_the_browser_under_apps_js_field_names():
    rows = build.fixture_for_js()
    assert len(rows) == len(build.FIXTURE)
    assert rows[0] == {"clientId": "c01", "date": "2025-12-30",
                       "amount": "14.99", "category": "Household",
                       "note": "broom", "currency": "EUR"}


def test_money_in_python_records_errors_as_data():
    """An error a person reads is behaviour too, so it is compared rather
    than allowed to stop the comparison."""
    got = build.money_in_python([{"fn": "parse", "args": ["3.50"]},
                                 {"fn": "parse", "args": ["abc"]},
                                 {"fn": "format", "args": [None]}])
    assert got[0] == {"ok": 350}
    assert got[1]["error"] == "MoneyError" and got[1]["says"]
    assert got[2]["error"] == "TypeError"


def test_the_money_cases_cover_every_function_and_every_keypad_amount():
    cases = build.money_cases()
    assert {case["fn"] for case in cases} == {"parse", "plain", "format",
                                              "known", "total"}
    parsed = [case["args"][0] for case in cases if case["fn"] == "parse"]
    for cents in range(500):
        assert "%d.%02d" % (cents // 100, cents % 100) in parsed
    assert json.dumps(cases)      # it has to survive the trip to a browser


# -------------------------------------------------------------- comparing

def test_scrub_drops_created_at_at_any_depth():
    assert build.scrub({"a": 1, "created_at": "now",
                        "rows": [{"id": 1, "created_at": "then"}, 3]}) == \
        {"a": 1, "rows": [{"id": 1}, 3]}


def test_differences_names_the_path_to_each_one():
    assert build.differences({"a": [1, {"b": 2}]}, {"a": [1, {"b": 2}]}) == []
    assert build.differences(1, 1.0) == [], "JSON has one number type"
    assert build.differences({"a": [1, {"b": 2}]},
                             {"a": [1, {"b": 3}]}) == \
        [".a[1].b: python 2, browser 3"]
    assert build.differences({"x": 1}, {"y": 1}) == [
        ".x: only python has it (1)", ".y: only the browser has it (1)"]
    assert build.differences("a", "b") == [".: python 'a', browser 'b'"]


def test_a_bool_never_equals_a_number():
    """True == 1 in Python; `{"agrees": 1}` from the browser is a bug."""
    assert build.differences({"agrees": True}, {"agrees": 1}) == [
        ".agrees: python True, browser 1"]
    assert build.differences(0, False)


def test_a_list_of_the_wrong_length_is_one_difference_not_many():
    found = build.differences({"rows": [1, 2, 3]}, {"rows": [1, 2]})
    assert len(found) == 1
    assert found[0].startswith(".rows: python has 3, browser has 2")
    assert build.differences([1], [])[0].startswith(".: python has 1")


def test_report_is_silent_when_everything_agrees():
    assert build.report("x", []) is None


def test_report_counts_and_caps_what_it_shows():
    one = stopped_with(build.report, "money", ["one thing"])
    assert "disagrees with the Python in 1 place.\n" in one
    assert "Nothing has been written to docs/app." in one
    assert "... and" not in one

    many = stopped_with(build.report, "money",
                        ["wrong %d" % n for n in range(30)], "context\n")
    assert "in 30 places" in many
    assert "context\n  wrong 0" in many
    assert "wrong 24" in many and "wrong 25" not in many
    assert many.endswith("... and 5 more")


def test_error_difference_compares_class_always_and_wording_for_ours():
    ok, money_error = {"ok": 1}, {"error": "MoneyError", "says": "no digits"}
    assert build.error_difference("f()", ok, money_error) == [
        "f(): python returned 1, browser raised MoneyError: no digits"]
    assert build.error_difference("f()", money_error, ok) == [
        "f(): python raised MoneyError: no digits, browser returned 1"]
    assert build.error_difference(
        "f()", money_error, {"error": "TypeError", "says": "x"}) == [
        "f(): python raised MoneyError, browser raised TypeError"]
    assert build.error_difference(
        "f()", money_error, {"error": "MoneyError", "says": "no Digits"}) == [
        "f(): python says 'no digits', browser says 'no Digits'"]
    assert build.error_difference(
        "f()", {"error": "EntryError", "says": "a"},
        {"error": "EntryError", "says": "a"}) == []
    # The interpreter's wording and an explicit throw never match, so a
    # TypeError is compared on its class alone.
    assert build.error_difference(
        "f()", {"error": "TypeError", "says": "'<' not supported"},
        {"error": "TypeError", "says": "cents must be a number"}) == []


# ------------------------------------------------------------ pass 1

def test_a_port_that_agrees_passes_the_money_check(page):
    assert build.check_money(page) == len(build.money_cases())


def test_a_money_port_that_answers_differently_is_refused(tmp_path):
    def change(out):
        out[0] = {"ok": 999}             # parse(None) should raise
        out[-1] = {"ok": -1}             # the last total, wrong

    message = stopped_with(build.check_money,
                           Broken(tmp_path, build.MONEY_JS, change))
    assert "money.py vs money.js" in message and "2 places" in message
    assert "money.parse(None): python raised MoneyError" in message
    assert "browser returned 999" in message
    assert "money.total([123456789, 987654321]): python 1111111110, " \
        "browser -1" in message


# ------------------------------------------------------------ pass 2

def test_a_port_that_agrees_passes_the_query_check(page):
    before = entries.today
    calls = len(build.STORE_CALLS) + len(build.EXPORT_CALLS) + 3
    assert build.check_queries(page) == calls
    assert entries.today is before, "the fixed date leaked out of the check"


def test_the_query_check_cleans_up_its_database(page, scratch):
    build.check_queries(page)
    assert scratch and not any(os.path.exists(path) for path in scratch)


def test_a_store_that_lost_fixture_entries_is_refused(tmp_path):
    def change(out):
        out["added"] = 3

    message = stopped_with(build.check_queries,
                           Broken(tmp_path, build.STORE_JS, change))
    assert "took 3 of the %d fixture entries" % len(build.FIXTURE) in message


def test_a_store_that_is_silent_raises_or_differs_is_refused(tmp_path):
    def change(out):
        del out["payloads"]["categories"]
        out["payloads"]["usual.default"] = {"error": "TypeError",
                                            "says": "x is undefined"}
        out["payloads"]["csv.oneday"]["ok"] += "extra"

    message = stopped_with(build.check_queries,
                           Broken(tmp_path, build.STORE_JS, change))
    assert "entries.py/export.py vs store.js" in message
    assert "3 places" in message
    assert "categories: the browser answered nothing" in message
    assert "usual.default: browser raised TypeError: x is undefined" in message
    assert "csv.oneday: python " in message


def test_the_query_check_restores_the_date_even_when_it_fails(tmp_path,
                                                              monkeypatch):
    before = entries.today

    def tie(payloads):
        raise SystemExit("build_static: tied")

    monkeypatch.setattr(build, "refuse_ties", tie)
    stopped_with(build.check_queries, PythonPage(tmp_path))
    assert entries.today is before


def test_refuse_ties_passes_a_fixture_with_none():
    payloads = {
        "totals.today": {"ok": {"today": [{"cents": 1, "currency": "EUR"}],
                                "week": [], "month": []}},
        "byCategory.x": {"ok": [{"cents": 2, "category": "A",
                                 "currency": "EUR"},
                                {"cents": 1, "category": "B",
                                 "currency": "EUR"}]},
        "usual.x": {"ok": [{"times": 3, "last_on": "2026-03-01",
                            "category": "A", "amount": "1.00"}]},
        "csv.x": {"ok": "Date,Description\n"},
    }
    assert build.refuse_ties(payloads) is None


def test_refuse_ties_names_every_tie():
    """A tie leaves the order to SQLite on one side and store.js on the
    other, so the build would pass or fail at random."""
    same = {"cents": 5}
    payloads = {
        "totals.today": {"ok": {
            "today": [], "month": [],
            "week": [dict(same, currency="EUR"), dict(same, currency="CAD")]}},
        "byCategory.x": {"ok": [dict(same, category="A", currency="EUR"),
                                dict(same, category="B", currency="EUR")]},
        "usual.x": {"ok": [
            {"times": 3, "last_on": "d", "category": "A", "amount": "1"},
            {"times": 3, "last_on": "d", "category": "B", "amount": "2"}]},
    }
    message = stopped_with(build.refuse_ties, payloads)
    assert "the comparison fixture ties" in message
    assert "totals.today/week: EUR and CAD both 5" in message
    assert "byCategory.x: A/EUR and B/EUR both 5" in message
    assert "usual.x: A 1 and B 2 both (3, 'd')" in message


def test_the_real_fixture_does_not_tie(page):
    """The fixture is required not to tie; the agreeing query pass would
    have stopped if it did. Asserted on its own so a nudge to FIXTURE that
    introduces one fails here by name."""
    from domain import export
    with today_is(build.FIXED_TODAY), \
            db.session(str(page.workdir.parent / "ties")) as conn:
        page._load(conn, build.fixture_for_js())
        payloads = {name: {"ok": getattr(entries, py)(conn, *args)}
                    for name, py, _js, args in build.STORE_CALLS}
        payloads["csv"] = {"ok": export.as_csv(conn)}
    build.refuse_ties(payloads)


def test_the_overview_payload_has_app_pys_shape(page, tmp_path):
    with today_is(build.FIXED_TODAY), db.session(str(tmp_path)) as conn:
        body = build.overview_payload(conn, None)
        client = web.create_app(str(tmp_path)).test_client()
        served = client.get("/api/overview").get_json()
    assert set(body) == set(served)
    assert body["today"] == build.FIXED_TODAY
    assert body["currencies"] == ["EUR", "CAD", "USD"]


# ------------------------------------------------------------ pass 3

def test_a_shim_that_agrees_passes_the_route_check(page, scratch):
    before = entries.today
    assert build.check_routes(page) == len(build.REQUESTS)
    assert entries.today is before
    assert not any(os.path.exists(path) for path in scratch)


def test_a_shim_that_answers_200_where_flask_says_400_is_refused(tmp_path):
    """app.js tells a refused batch from an unreachable server by the
    status, so this is the difference that quarantines real entries."""
    bad = [index for index, (_m, url, _b) in enumerate(build.REQUESTS)
           if url == "/api/overview?on=nonsense"][0]

    def change(out):
        out[bad]["status"] = 200

    message = stopped_with(build.check_routes,
                           Broken(tmp_path, build.ROUTES_JS, change))
    assert "app.py vs static-api.js" in message
    assert "GET /api/overview?on=nonsense.status: python 400, browser 200" \
        in message
    assert "app.js is byte-identical in both builds" in message


# -------------------------------------------------------------- the page

def template():
    return build.read(os.path.join(ROOT, "templates", "index.html"))


def test_the_page_is_the_template_with_the_server_parts_replaced():
    html = build.page_html(template())
    assert "<title>Tally — browser build</title>" in html
    assert '<link rel="stylesheet" href="css/static.css">' in html
    assert 'id="exportCsv" href="#"' in html
    assert 'href="/export.csv"' not in html
    assert "{{" not in html and "{%" not in html
    loaded = [line for line in html.splitlines()
              if line.startswith("<script src=")]
    assert loaded == ['<script src="%s"></script>' % name
                      for name in build.SCRIPT_ORDER]
    assert build.banner() in html
    assert build.REPO in build.banner()


def test_jinja_left_in_the_page_stops_the_build():
    message = stopped_with(build.page_html,
                           template() + "{{ csrf_token() }}{% if x %}")
    assert "unresolved Jinja" in message
    assert "{{ csrf_token() }}" in message and "{% if x %}" in message


def test_a_template_that_moved_stops_the_build():
    message = stopped_with(build.page_html,
                           template().replace("<title>Tally</title>", ""))
    assert "could not retitle the page" in message


def test_collect_is_every_published_file_with_a_banner_on_the_copies():
    written = build.collect()
    assert sorted(written) == sorted(
        ["index.html", "css/style.css", "css/static.css"]
        + ["js/" + name for name in build.SHARED_JS + build.STATIC_JS])
    app_js = build.read(os.path.join(ROOT, "static", "js", "app.js"))
    assert written["js/app.js"] == build.BANNER % "static/js/app.js" + app_js
    assert written["js/money.js"] == build.read(
        os.path.join(build.SRC, "js", "money.js"))
    assert written["css/style.css"].startswith(
        build.BANNER % "static/css/style.css")


def test_prune_removes_what_an_older_build_left(tmp_path, monkeypatch,
                                                capsys):
    out = tmp_path / "app"
    monkeypatch.setattr(build, "OUT", str(out))
    build.prune({"index.html"})               # no folder yet: nothing to do
    assert not out.exists()

    for name in ("index.html", "js/app.js", "js/renamed.js"):
        build.write(str(out / name), "x")
    build.prune({"index.html", "js/app.js"})
    assert (out / "index.html").exists() and (out / "js" / "app.js").exists()
    assert not (out / "js" / "renamed.js").exists()
    assert "removed stale %s" % os.path.join("js", "renamed.js") in \
        capsys.readouterr().out


# ---------------------------------------------------------- the browser

class FakePlaywright:
    """sync_playwright(), as much of it as browser() uses."""

    def __init__(self, page):
        self.page = page
        self.launched = []
        self.closed = 0
        self.chromium = self

    # sync_playwright() is a context manager yielding the playwright object.
    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def launch(self, **options):
        self.launched.append(options)
        return self

    def new_page(self):
        return self.page

    def close(self):
        self.closed += 1


@pytest.fixture
def playwright(monkeypatch, page):
    fake = FakePlaywright(page)
    package = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = fake
    package.sync_api = sync_api
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    return fake


def test_browser_loads_the_sources_and_closes_after(playwright, monkeypatch,
                                                    tmp_path):
    chrome = tmp_path / "chrome.exe"
    chrome.write_text("")
    monkeypatch.setattr(build, "CHROME", str(chrome))
    with build.browser(["var a = 1;", "var b = 2;"]) as (page, problems):
        assert problems == []
        assert "<script>\nvar a = 1;\n</script><script>\nvar b = 2;\n" \
            in page.content
        assert playwright.closed == 0
    assert playwright.launched == [{"executable_path": str(chrome)}]
    assert playwright.closed == 1


def test_browser_uses_playwrights_chromium_without_a_system_chrome(
        playwright, monkeypatch, tmp_path):
    monkeypatch.setattr(build, "CHROME", str(tmp_path / "not-here.exe"))
    with build.browser([]):
        pass
    assert playwright.launched == [{}]


def test_browser_collects_page_errors_and_console_errors_only(playwright,
                                                              monkeypatch,
                                                              tmp_path):
    monkeypatch.setattr(build, "CHROME", str(tmp_path / "none"))
    with build.browser([]) as (page, problems):
        page.handlers["console"](types.SimpleNamespace(type="log",
                                                       text="hello"))
        page.handlers["console"](types.SimpleNamespace(type="error",
                                                       text="boom"))
        page.handlers["pageerror"](ValueError("thrown"))
        assert problems == ["boom", "thrown"]


def test_a_bundle_that_does_not_load_stops_and_still_closes(playwright,
                                                            page,
                                                            monkeypatch,
                                                            tmp_path):
    monkeypatch.setattr(build, "CHROME", str(tmp_path / "none"))

    def load_badly(html):
        page.handlers["pageerror"](SyntaxError("Unexpected token"))

    page.set_content = load_badly
    with pytest.raises(SystemExit) as stop:
        with build.browser(["broken("]):
            pytest.fail("the body ran for a page that did not load")
    assert "does not load:\n  Unexpected token" in str(stop.value)
    assert playwright.closed == 1


def test_no_playwright_is_an_error_with_the_install_line(monkeypatch):
    """There is deliberately no flag to skip the checks."""
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    message = stopped_with(lambda: build.browser([]).__enter__())
    assert "pip install playwright" in message
    assert "no --skip-checks flag on purpose" in message


# ----------------------------------------------------------------- main

@pytest.fixture
def out(tmp_path, monkeypatch):
    folder = tmp_path / "docs-app"
    monkeypatch.setattr(build, "OUT", str(folder))
    return folder


def use_browser(monkeypatch, page, problems=None):
    seen = []

    @contextlib.contextmanager
    def fake(sources):
        seen.append(sources)
        yield page, problems if problems is not None else []

    monkeypatch.setattr(build, "browser", fake)
    return seen


def test_main_checks_then_writes_the_bundle(out, page, monkeypatch, capsys):
    seen = use_browser(monkeypatch, page)
    build.write(str(out / "js" / "old-name.js"), "stale")

    build.main()

    written = build.collect()
    # The checks ran on the files as published, in load order.
    assert seen == [[written["js/money.js"], written["js/store.js"],
                     written["js/static-api.js"], written["js/static-ui.js"]]]
    for name, body in written.items():
        assert build.read(str(out / name)) == body, name
    assert not (out / "js" / "old-name.js").exists()

    printed = capsys.readouterr().out
    assert "%d values, and the wording of every error" \
        % len(build.money_cases()) in printed
    assert "payloads over %d entries" % len(build.FIXTURE) in printed
    assert "%d requests, replayed against both" % len(build.REQUESTS) \
        in printed
    assert "wrote %d files to docs/app" % len(written) in printed


def test_main_writes_nothing_when_a_check_fails(out, tmp_path, monkeypatch):
    def change(out_):
        out_[0] = {"ok": 0}

    use_browser(monkeypatch, Broken(tmp_path, build.MONEY_JS, change))
    with pytest.raises(SystemExit):
        build.main()
    assert not out.exists()


def test_main_writes_nothing_when_the_page_logged_errors(out, page,
                                                         monkeypatch):
    use_browser(monkeypatch, page, problems=["TypeError: x is undefined"])
    with pytest.raises(SystemExit) as stop:
        build.main()
    assert "logged errors while being checked:\n  TypeError: x is " \
        "undefined" in str(stop.value)
    assert not out.exists()
