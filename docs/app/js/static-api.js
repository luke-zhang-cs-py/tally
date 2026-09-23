/* ---------------------------------------------------------------------------
   static-api.js — app.py's routes, answered inside the page
   ---------------------------------------------------------------------------
   static/js/app.js talks to the server in six places:

     GET    /api/overview        everything the page needs on load
     GET    /api/entries?limit=  the list under the keypad
     POST   /api/entries         flush the offline queue
     POST   /api/entry           one entry
     DELETE /api/entry/<id>      undo
     GET    /api/reconciles      does the export add up
                                 (/export.csv is an <a>, not a fetch — the
                                  download is handled in static-ui.js)

   This intercepts exactly those and answers them from TallyStore, so the
   *same* app.js runs in both builds. A fetch shim rather than a forked
   app.js: a second copy of the 436-line file with its fetch calls edited out
   is a fork that looks like a copy, and it rots the first time somebody fixes
   a bug in one of them. Everything shared in docs/app/ is copied byte for
   byte by tools/build_static.py; this file is the whole of what stands
   between it and a server.

   The status codes and error bodies are app.py's, not invented. app.js
   distinguishes a TypeError from fetch (genuinely offline — keep the queue,
   try later) from a response the server actually returned (do not retry).
   Getting that wrong here would mean an entry silently quarantined, so every
   answer below is a resolved reply with a status, never a rejection.

   Loaded *before* app.js, which calls flush() and load() as it finishes.
   --------------------------------------------------------------------------- */

(function () {
  'use strict';

  var M = TallyMoney;
  var S = TallyStore;

  /* app.py */
  var MAX_QUEUED = 200;

  /* Enough of a Response for app.js's call sites: `.ok`, `.status`, `.json()`.
     Not a real Response — constructing one is possible, but then the shim
     would be claiming to be the platform's fetch rather than a stand-in for
     six known callers, and the first thing to use a seventh feature of it
     would fail somewhere a long way from here. */
  function reply(status, body) {
    return Promise.resolve({
      ok: status >= 200 && status < 300,
      status: status,
      statusText: status < 400 ? 'OK' : 'Static build',
      json: function () { return Promise.resolve(body); },
      text: function () { return Promise.resolve(JSON.stringify(body)); }
    });
  }

  function csvReply(text, filename) {
    return Promise.resolve({
      ok: true, status: 200, statusText: 'OK',
      headers: { get: function (name) {
        return String(name).toLowerCase() === 'content-disposition'
          ? 'attachment; filename="' + filename + '"' : null;
      } },
      text: function () { return Promise.resolve(text); },
      blob: function () {
        return Promise.resolve(new Blob([text], { type: 'text/csv' }));
      },
      json: function () { return Promise.reject(new Error('not JSON')); }
    });
  }

  /* Only these two are expected. Anything else out of TallyStore is a bug in
     the bundle, and it should reach the console as one rather than being
     laundered into a tidy 400 the page reports as the user's fault. */
  function refused(error) {
    return error && (error.name === 'MoneyError' || error.name === 'EntryError');
  }

  function body(init) {
    if (!init || init.body == null) return {};
    try { return JSON.parse(init.body); } catch (e) { return {}; }
  }

  /* app.py reads the limit with `int(raw)`, and Number() is not that. Number
     takes '0x10' as 16, '1e3' as 1000 and '1.0' as 1; int() refuses all
     three. Left as Number(), a stale link with `?limit=1.0` in it would be
     answered here and rejected by the Flask app, which is exactly the kind of
     difference this build is not allowed to have. Underscores are in because
     int() accepts them between digits; the leading/trailing whitespace is in
     because int() strips it. Returns null for anything int() would refuse. */
  function pyInt(raw) {
    var text = String(raw).trim();
    if (!/^[+-]?\d(?:_?\d)*$/.test(text)) return null;
    return Number(text.replace(/_/g, ''));
  }

  function query(url) {
    var out = {};
    var mark = url.indexOf('?');
    if (mark < 0) return out;
    url.slice(mark + 1).split('&').forEach(function (pair) {
      if (!pair) return;
      var cut = pair.indexOf('=');
      var key = decodeURIComponent((cut < 0 ? pair : pair.slice(0, cut)).replace(/\+/g, ' '));
      out[key] = cut < 0 ? '' : decodeURIComponent(pair.slice(cut + 1).replace(/\+/g, ' '));
    });
    return out;
  }

  /* app.py's _record: one place, because the single save and the queue flush
     send exactly the same shape. Written out twice, a field added to one and
     not the other would arrive without its note and nothing would fail. */
  function record(sent) {
    return S.add({
      amount: sent.amount,
      category: sent.category,
      note: sent.note === undefined ? '' : sent.note,
      currency: sent.currency,
      date: sent.date,
      clientId: sent.clientId
    });
  }

  /* app.py's _queued_outcome: never raises. A bad entry is reported and the
     rest of the queue still goes in — the others were real expenses, and
     refusing the batch to reject one loses them. */
  function queuedOutcome(item) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) {
      return { clientId: null, result: 'rejected', error: 'not an entry' };
    }
    try {
      var done = record(item);
      return { clientId: item.clientId === undefined ? null : item.clientId,
               id: done.id, result: done.result };
    } catch (error) {
      if (!refused(error)) throw error;
      return { clientId: item.clientId === undefined ? null : item.clientId,
               result: 'rejected', error: error.message };
    }
  }

  var ROUTES = [
    ['GET', /^\/api\/overview$/, function (url) {
      try {
        return reply(200, S.overview(query(url).on || null));
      } catch (error) {
        if (!refused(error)) throw error;
        return reply(400, { error: error.message });
      }
    }],

    ['GET', /^\/api\/entries$/, function (url) {
      var args = query(url);
      var raw = args.limit === undefined ? 50 : args.limit;
      var limit = pyInt(raw);
      /* app.py answers `invalid limit: '...'` with Python's repr, and the
         page prints whatever it is given. Same text, so a person reading a
         bug report cannot tell which build produced it. */
      if (limit === null) {
        return reply(400, { error: 'invalid limit: ' + M.repr(raw) });
      }
      try {
        return reply(200, { entries: S.recent(limit, args.on || null) });
      } catch (error) {
        if (!refused(error)) throw error;
        return reply(400, { error: error.message });
      }
    }],

    ['POST', /^\/api\/entries$/, function (url, init) {
      var sent = body(init);
      var queued = sent.entries;
      if (!Array.isArray(queued)) {
        return reply(400, { error: "'entries' must be a list" });
      }
      if (queued.length > MAX_QUEUED) {
        return reply(400, { error: 'more than ' + MAX_QUEUED + ' at once' });
      }
      var results = queued.map(queuedOutcome);
      return reply(200, { results: results, totals: S.totals() });
    }],

    ['POST', /^\/api\/entry$/, function (url, init) {
      try {
        var done = record(body(init));
        return reply(done.result === 'added' ? 201 : 200,
                     { id: done.id, result: done.result, totals: S.totals() });
      } catch (error) {
        if (!refused(error)) throw error;
        return reply(400, { error: error.message });
      }
    }],

    ['DELETE', /^\/api\/entry\/(\d+)$/, function (url, init, match) {
      if (!S.remove(Number(match[1]))) {
        return reply(404, { error: 'no such entry' });
      }
      return reply(200, { deleted: Number(match[1]), totals: S.totals() });
    }],

    ['GET', /^\/api\/reconciles$/, function () {
      var done = S.reconciles();
      return reply(200, { agrees: done.agrees, file: done.file,
                          stored: done.stored, text: M.format(done.stored) });
    }],

    ['GET', /^\/export\.csv$/, function (url) {
      var args = query(url);
      var first = args.first || null;
      var last = args.last || null;
      try {
        return csvReply(S.asCsv(first, last), S.filename(first, last));
      } catch (error) {
        if (!refused(error)) throw error;
        return reply(400, { error: error.message });
      }
    }]
  ];

  /* The path, with the directory the bundle happens to be served from taken
     off the front. app.js asks for "/api/overview" absolutely, and on GitHub
     Pages the page itself lives at /tally/app/ — so matching on the whole
     pathname would miss. Matching on the tail is what makes the same bundle
     work from Pages, from a subdirectory and from file://. */
  function route(method, url) {
    var path = String(url).split('#')[0];
    var cut = path.indexOf('?');
    var bare = cut < 0 ? path : path.slice(0, cut);
    bare = bare.replace(/^[a-z]+:\/\/[^/]*/i, '');
    for (var i = 0; i < ROUTES.length; i++) {
      if (ROUTES[i][0] !== method) continue;
      var match = ROUTES[i][1].exec(bare);
      if (match) return { handler: ROUTES[i][2], match: match };
    }
    return null;
  }

  var realFetch = (typeof window.fetch === 'function') ? window.fetch.bind(window) : null;

  window.fetch = function (input, init) {
    var url = String(typeof input === 'string' ? input : (input && input.url) || '');
    var method = String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    var found = route(method, url);
    if (found) return found.handler(url, init || {}, found.match);
    if (realFetch) return realFetch(input, init);
    return Promise.reject(new TypeError('no fetch available for ' + url));
  };

  window.TallyStatic = { routes: ROUTES.length };
})();
