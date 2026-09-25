"""The HTTP surface, and the queue that makes it safe to tap save offline."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as web   # noqa: E402
from domain import entries      # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TALLY_DATA", str(tmp_path))
    application = web.create_app(str(tmp_path))
    application.config["TESTING"] = True
    return application.test_client()


def post(client, url, **fields):
    return client.post(url, data=json.dumps(fields),
                       content_type="application/json")


def today():
    return entries.today().isoformat()


# -------------------------------------------------------------------- pages

def test_the_page_loads(client):
    assert client.get("/").status_code == 200


def test_the_overview_answers_before_anything_is_recorded(client):
    """Day one. The keypad has to be usable with an empty database, and the
    category chips must not be empty or there is nothing to tap."""
    body = client.get("/api/overview").get_json()
    assert body["totals"]["today"] == []
    assert body["usual"] == []
    assert body["categories"]
    assert body["currencies"] == ["EUR", "CAD", "USD"]


def test_the_overview_is_one_request(client):
    """The page is opened to record something in the next few seconds. Four
    extra round trips is the difference between a keypad that is ready and
    one still assembling."""
    body = client.get("/api/overview").get_json()
    for key in ("totals", "usual", "categories", "recent", "byCategory",
                "days", "export"):
        assert key in body, key


# ------------------------------------------------------------------ saving

def test_an_expense_is_recorded(client):
    reply = post(client, "/api/entry", amount="3.50", category="Coffee",
                 clientId="a1")
    assert reply.status_code == 201
    body = reply.get_json()
    assert body["result"] == "added"
    assert body["totals"]["today"][0]["cents"] == 350


def test_the_same_entry_sent_twice_reports_itself(client):
    """A retry, or a second tab flushing the same queue. Not an error: the
    page needs to know it is already recorded so it can stop holding it."""
    post(client, "/api/entry", amount="3.50", category="Coffee", clientId="a1")
    again = post(client, "/api/entry", amount="3.50", category="Coffee",
                 clientId="a1")
    assert again.status_code == 200
    assert again.get_json()["result"] == "duplicate"
    assert len(client.get("/api/entries").get_json()["entries"]) == 1


@pytest.mark.parametrize("fields,why", [
    ({"amount": "abc", "category": "Coffee"}, "unreadable amount"),
    ({"amount": "0", "category": "Coffee"}, "nothing"),
    ({"amount": "-5", "category": "Coffee"}, "negative"),
    ({"amount": "3.50", "category": ""}, "no category"),
    ({"amount": "3.50", "category": "Coffee", "date": "not-a-date"}, "bad date"),
    ({"amount": "3.50", "category": "Coffee", "date": "2099-01-01"}, "future"),
])
def test_a_bad_entry_is_a_400_with_a_reason(client, fields, why):
    reply = post(client, "/api/entry", **fields)
    assert reply.status_code == 400, why
    assert reply.get_json()["error"]


def test_a_note_and_a_currency_are_carried(client):
    post(client, "/api/entry", amount="8.00", category="Drinks",
         note="Airport", currency="CAD", clientId="d1")
    row = client.get("/api/entries").get_json()["entries"][0]
    assert row["note"] == "Airport"
    assert row["currency"] == "CAD"
    assert row["amount_text"] == "CA$8.00"


# ------------------------------------------------------------- the queue

def test_a_queue_is_flushed_in_one_request(client):
    """What the page sends when it comes back online."""
    reply = client.post("/api/entries", json={"entries": [
        {"clientId": "q1", "amount": "3.50", "category": "Coffee"},
        {"clientId": "q2", "amount": "12.40", "category": "Lunch"},
    ]})
    assert reply.status_code == 200
    body = reply.get_json()
    assert [r["result"] for r in body["results"]] == ["added", "added"]
    assert body["totals"]["today"][0]["cents"] == 1590


def test_a_queue_flushed_twice_lands_once(client):
    """Two tabs, or a retry after a timeout that actually succeeded. Without
    the client id every expense in the queue would double."""
    payload = {"entries": [
        {"clientId": "q1", "amount": "3.50", "category": "Coffee"},
        {"clientId": "q2", "amount": "12.40", "category": "Lunch"},
    ]}
    client.post("/api/entries", json=payload)
    again = client.post("/api/entries", json=payload).get_json()
    assert [r["result"] for r in again["results"]] == ["duplicate", "duplicate"]
    assert len(client.get("/api/entries").get_json()["entries"]) == 2


def test_one_bad_entry_does_not_stop_the_rest_of_the_queue(client):
    """The others were real expenses. Refusing the batch would lose them to
    fix a problem with one."""
    body = client.post("/api/entries", json={"entries": [
        {"clientId": "q1", "amount": "3.50", "category": "Coffee"},
        {"clientId": "q2", "amount": "abc", "category": "Broken"},
        {"clientId": "q3", "amount": "5.00", "category": "Snacks"},
    ]}).get_json()
    assert [r["result"] for r in body["results"]] == ["added", "rejected",
                                                      "added"]
    assert body["results"][1]["error"]
    assert len(client.get("/api/entries").get_json()["entries"]) == 2


def test_something_that_is_not_an_entry_is_reported_not_fatal(client):
    body = client.post("/api/entries", json={"entries": [
        "not an entry", {"clientId": "q1", "amount": "3.50",
                         "category": "Coffee"},
    ]}).get_json()
    assert body["results"][0]["result"] == "rejected"
    assert body["results"][1]["result"] == "added"


def test_a_queue_that_is_not_a_list_is_a_400(client):
    assert client.post("/api/entries",
                       json={"entries": "nope"}).status_code == 400
    assert client.post("/api/entries", json={}).status_code == 400


def test_an_absurdly_long_queue_is_refused(client):
    """A page holds a handful of entries. Anything larger is not a person
    tapping a keypad."""
    too_many = [{"clientId": f"q{n}", "amount": "1.00", "category": "X"}
                for n in range(web.MAX_QUEUED + 1)]
    reply = client.post("/api/entries", json={"entries": too_many})
    assert reply.status_code == 400
    assert str(web.MAX_QUEUED) in reply.get_json()["error"]


def test_a_queued_entry_carries_every_field_the_single_save_does(client):
    """Both endpoints go through one function, and this is why.

    Written out twice, a field added to the single save and forgotten in the
    queue would fail nothing: entries made offline would arrive without their
    note or in the wrong currency, and it would look like the person had
    typed it that way. Nothing in the app would report it.
    """
    fields = {"clientId": "q1", "amount": "8.00", "category": "Drinks",
              "note": "Airport", "currency": "CAD", "date": "2026-09-05"}
    client.post("/api/entries", json={"entries": [dict(fields)]})
    queued = client.get("/api/entries").get_json()["entries"][0]

    fields["clientId"] = "s1"
    post(client, "/api/entry", **fields)
    saved = client.get("/api/entries?on=2026-09-05").get_json()["entries"]

    kept = ("category", "note", "currency", "spent_on", "amount")
    assert len(saved) == 2, "both landed on the given date"
    assert ({key: queued[key] for key in kept}
            == {key: saved[0][key] for key in kept})
    assert queued["note"] == "Airport"
    assert queued["currency"] == "CAD"
    assert queued["spent_on"] == "2026-09-05"


# ------------------------------------------------------------- deleting

def test_an_entry_can_be_deleted(client):
    entry_id = post(client, "/api/entry", amount="3.50", category="Coffee",
                    clientId="a1").get_json()["id"]
    reply = client.delete(f"/api/entry/{entry_id}")
    assert reply.status_code == 200
    assert reply.get_json()["totals"]["today"] == []
    assert client.get("/api/entries").get_json()["entries"] == []


def test_deleting_something_that_is_not_there_is_a_404(client):
    assert client.delete("/api/entry/999").status_code == 404


# --------------------------------------------------------------- listing

def test_the_list_can_be_narrowed_to_a_day(client):
    post(client, "/api/entry", amount="3.50", category="Coffee",
         date="2026-09-05", clientId="a")
    post(client, "/api/entry", amount="5.00", category="Lunch",
         date=today(), clientId="b")
    rows = client.get(f"/api/entries?on={today()}").get_json()["entries"]
    assert [r["category"] for r in rows] == ["Lunch"]


# ---------------------------------------------------------------- export

def test_the_export_is_a_csv_attachment(client):
    post(client, "/api/entry", amount="3.50", category="Coffee",
         note="Cafe Nero", clientId="a")
    reply = client.get("/export.csv")
    assert reply.status_code == 200
    assert reply.mimetype == "text/csv"
    assert "attachment" in reply.headers["Content-Disposition"]
    text = reply.get_data(as_text=True)
    assert text.splitlines()[0] == "Date,Description,Amount,Currency"
    assert "-3.50" in text
    assert "Coffee - Cafe Nero" in text


def test_the_export_range_can_be_given(client):
    post(client, "/api/entry", amount="3.50", category="Coffee",
         date="2026-08-15", clientId="a")
    text = client.get("/export.csv?first=2026-08-01&last=2026-08-31").get_data(
        as_text=True)
    assert "2026-08-15" in text


def test_an_unreadable_export_range_is_a_400(client):
    assert client.get("/export.csv?first=nonsense").status_code == 400


def test_the_file_is_reported_to_add_up(client):
    post(client, "/api/entry", amount="3.50", category="Coffee", clientId="a")
    body = client.get("/api/reconciles").get_json()
    assert body["agrees"] is True
    assert body["file"] == body["stored"] == 350
    assert body["text"] == "€3.50", "the footer prints this"


# ------------------------------------------------------------ the markup

def test_every_id_in_the_page_is_used_by_the_script():
    """Dead markup, caught structurally.

    The page shipped a hidden header chip and a footer span that nothing ever
    filled -- and the empty footer span was hiding something real:
    /api/reconciles was implemented and tested but never called from the
    page, so a documented feature was invisible. An element nobody writes to
    is either a missing feature or litter, and both are worth finding.
    """
    import re
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    with open(os.path.join(root, "templates", "index.html"),
              encoding="utf-8") as handle:
        ids = set(re.findall(r'id="([A-Za-z][\w-]*)"', handle.read()))
    with open(os.path.join(root, "static", "js", "app.js"),
              encoding="utf-8") as handle:
        script = handle.read()

    assert ids, "no ids found -- the pattern stopped matching"
    unused = sorted(i for i in ids if f"'{i}'" not in script)
    assert not unused, f"nothing in app.js writes to: {unused}"


# ----------------------------------------------------------------- safety

def test_it_binds_loopback_with_debug_off_by_default(monkeypatch):
    """Almanac in this family bound every interface with the Werkzeug
    debugger on, which is an interactive Python console for anyone on the
    network. This holds a record of what somebody spends.

    HOST is cleared first, because this is a claim about the default and the
    module reads the environment -- asserting against whatever the machine
    exports is the mistake that made the transit suite pass only locally.
    """
    import importlib
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    reloaded = importlib.reload(web)
    assert reloaded.HOST == "127.0.0.1"
    assert reloaded.DEBUG is False
    monkeypatch.undo()
    importlib.reload(web)


def test_binding_wider_stays_possible_on_purpose(monkeypatch):
    """Reaching it from a phone is the whole point of quick capture, so the
    flag has to exist -- with the consequence stated, since there is no
    login."""
    import importlib
    monkeypatch.setenv("HOST", "0.0.0.0")
    try:
        assert importlib.reload(web).HOST == "0.0.0.0"
    finally:
        monkeypatch.undo()
        importlib.reload(web)
