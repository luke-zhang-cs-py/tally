"""
app.py
------
Tally — quick expense capture. 127.0.0.1:5005.

Loopback and debug-off by default, like the rest of this family. Almanac once
bound every interface with the Werkzeug debugger on, which is an interactive
Python console for anyone on the network, and this holds a record of what
somebody spends.

There is a tension in that worth stating rather than hiding. Quick capture is
*for* a phone, and a phone cannot reach 127.0.0.1. Reaching it means
`HOST=0.0.0.0`, and this app has no login -- so anyone on the same wi-fi can
read and write your expenses. That is a deliberate one-flag decision with the
consequence attached, not a default: `python app.py` stays local, and the
README says plainly what the flag costs.
"""
import datetime as dt
import os

from flask import Flask, Response, jsonify, render_template, request

import db
import entries
import export
import money

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "5005"))
DEBUG = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")

# A page holds a handful of entries; a request carrying more than this is not
# a person tapping a keypad.
MAX_QUEUED = 200


class Context:
    """Where the data is, and how to reach it.

    One object rather than threading a directory and a connection factory
    through every route group -- the shape primitive obsession takes in a web
    app, and the wallet app's create_app hit complexity 40 before it was
    split this way.
    """

    def __init__(self, directory=None):
        self.directory = directory

    def connect(self):
        """One connection per request, closed when the block ends."""
        return db.session(self.directory)


def create_app(directory=None):
    app = Flask(__name__)
    app.config["TALLY_DIR"] = directory
    context = Context(directory)
    for register in (_pages, _reading, _writing, _exporting):
        register(app, context)
    return app


def _pages(app, ctx):
    @app.route("/")
    def index():
        return render_template("index.html")


def _reading(app, ctx):
    @app.route("/api/overview")
    def overview():
        """Everything the page needs on load, in one request.

        One rather than five because the page is opened to record something
        in the next few seconds, and four extra round trips is the difference
        between a keypad that is ready and one that is still assembling.
        """
        day = request.args.get("on") or None
        with ctx.connect() as conn:
            return jsonify({
                "today": entries.today().isoformat(),
                "totals": entries.totals(conn, day),
                "usual": entries.usual(conn),
                "categories": entries.categories(conn),
                "recent": entries.recent(conn, limit=25),
                "byCategory": entries.by_category(conn),
                "days": entries.days(conn),
                "currencies": list(money.CURRENCIES),
                "defaultCurrency": money.DEFAULT_CURRENCY,
                "export": export.summary(conn),
            })

    @app.route("/api/entries")
    def listing():
        with ctx.connect() as conn:
            return jsonify({"entries": entries.recent(
                conn, limit=request.args.get("limit", 50),
                day=request.args.get("on") or None)})


def _record(conn, sent):
    """Record one entry from what the page sent.

    In one place because the single save and the queue flush send exactly the
    same shape. Written out twice, a field added to one and not the other
    would not fail anything -- entries made offline would just quietly arrive
    without their note, which is the kind of bug you find months later in the
    data rather than in a test.

    Takes anything with .get, so it serves both a parsed JSON body and a form.
    """
    return entries.add(
        conn,
        amount=sent.get("amount"),
        category=sent.get("category"),
        note=sent.get("note", ""),
        currency=sent.get("currency"),
        spent_on=sent.get("date"),
        client_id=sent.get("clientId"))


def _queued_outcome(conn, item):
    """One queued entry's outcome. Never raises.

    A bad entry is reported and the rest of the queue still goes in: the
    others were real expenses, and refusing the batch to reject one loses
    them.
    """
    if not isinstance(item, dict):
        return {"clientId": None, "result": "rejected",
                "error": "not an entry"}
    try:
        entry_id, how = _record(conn, item)
    except (entries.EntryError, money.MoneyError) as bad:
        return {"clientId": item.get("clientId"), "result": "rejected",
                "error": str(bad)}
    return {"clientId": item.get("clientId"), "id": entry_id, "result": how}


def _writing(app, ctx):
    @app.route("/api/entry", methods=["POST"])
    def add_entry():
        body = request.get_json(silent=True) or request.form
        with ctx.connect() as conn:
            try:
                entry_id, how = _record(conn, body)
            except (entries.EntryError, money.MoneyError) as bad:
                return jsonify({"error": str(bad)}), 400
            return jsonify({"id": entry_id, "result": how,
                            "totals": entries.totals(conn)}), (
                201 if how == "added" else 200)

    @app.route("/api/entries", methods=["POST"])
    def add_many():
        """Flush a queue built while the server was unreachable.

        Each carries its own client id, so a queue sent twice -- a retry, a
        second tab, a refresh mid-send -- lands once. Reported per entry
        rather than as one success, because a queue where three of eight were
        already recorded is a normal outcome the page has to reconcile with
        what it is still holding.
        """
        body = request.get_json(silent=True) or {}
        queued = body.get("entries")
        if not isinstance(queued, list):
            return jsonify({"error": "'entries' must be a list"}), 400
        if len(queued) > MAX_QUEUED:
            return jsonify({"error": f"more than {MAX_QUEUED} at once"}), 400

        with ctx.connect() as conn:
            results = [_queued_outcome(conn, item) for item in queued]
            return jsonify({"results": results,
                            "totals": entries.totals(conn)})

    @app.route("/api/entry/<int:entry_id>", methods=["DELETE"])
    def delete_entry(entry_id):
        with ctx.connect() as conn:
            gone = entries.remove(conn, entry_id)
            if not gone:
                return jsonify({"error": "no such entry"}), 404
            return jsonify({"deleted": entry_id,
                            "totals": entries.totals(conn)})


def _exporting(app, ctx):
    @app.route("/export.csv")
    def download():
        first = request.args.get("first") or None
        last = request.args.get("last") or None
        with ctx.connect() as conn:
            try:
                text = export.as_csv(conn, first, last)
            except entries.EntryError as bad:
                return jsonify({"error": str(bad)}), 400
        return Response(text, mimetype="text/csv", headers={
            "Content-Disposition":
                f'attachment; filename="{export.filename(first, last)}"'})

    @app.route("/api/reconciles")
    def reconciles():
        """Does the exported file add up to what is stored."""
        with ctx.connect() as conn:
            agrees, from_file, stored = export.reconciles(conn)
            return jsonify({"agrees": agrees, "file": from_file,
                            "stored": stored,
                            "text": money.format(stored)})


app = create_app()


if __name__ == "__main__":
    if HOST != "127.0.0.1":
        print("!! Bound to " + HOST + " with no login.")
        print("!! Anyone on this network can read and add expenses.")
    print(f"Tally on http://{HOST}:{PORT}  ({dt.date.today()})")
    app.run(host=HOST, port=PORT, debug=DEBUG)
