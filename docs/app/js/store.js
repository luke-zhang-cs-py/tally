/* ---------------------------------------------------------------------------
   store.js — db.py, entries.py and export.py, against localStorage
   ---------------------------------------------------------------------------
   The Flask app keeps entries in SQLite and answers six JSON endpoints from
   it. This build keeps them in localStorage and answers the same six from
   there. Nothing above this file changes: static-api.js turns a fetch into a
   call in here, and static/js/app.js — copied into the bundle byte for byte —
   cannot tell the difference.

   Which means every query in entries.py has to be reproduced, not
   approximated. A GROUP BY is easy to get nearly right and wrong at the
   edges: the wrong week boundary, a habit ranked by recency instead of count,
   a day with nothing spent omitted instead of shown as zero. So the whole of
   this file is compared against the Python at build time — the same fixture
   of entries loaded into both, and every payload, the CSV text and the
   reconcile figure compared field by field. See tools/build_static.py.

   Three things are genuinely different, and are differences rather than bugs:

     * There is no `client_id UNIQUE` constraint, because there is no database
       to hold one. The duplicate check is the JavaScript one below, and it is
       enough here for the reason it is not enough on the server: a browser
       tab is single-threaded, so two writes cannot race the way two requests
       can.
     * SQLite's ORDER BY leaves ties in an unspecified order. This file breaks
       them on a second key so the page is at least stable between reloads.
       The build refuses fixtures that tie, so the comparison never depends on
       which choice either side made.
     * Dates are handled as UTC day numbers, never as local Date objects. A
       `new Date('2026-03-29')` in a timezone that changes clocks that night
       is a real source of off-by-one-day bugs, and an expense that moves to
       the previous day is exactly the failure this app cannot have.
   --------------------------------------------------------------------------- */

var TallyStore = (function () {
  'use strict';

  var M = TallyMoney;

  var ENTRIES_KEY = 'tally.entries';
  var SEQ_KEY = 'tally.nextId';

  /* db.py */
  var DEFAULT_CATEGORIES = ['Coffee', 'Lunch', 'Groceries', 'Transport',
                            'Drinks', 'Snacks', 'Household', 'Other'];

  /* entries.py */
  var USUAL_WINDOW_DAYS = 90;
  var USUAL_LIMIT = 8;
  var USUAL_MIN_COUNT = 3;

  /* export.py — exactly the names the wallet app's importer looks for. */
  var COLUMNS = ['Date', 'Description', 'Amount', 'Currency'];

  function EntryError(message) {
    var error = new Error(message);
    error.name = 'EntryError';
    return error;
  }

  /* ------------------------------------------------------------- storage */
  /* localStorage, unless it is not there. Private mode, a disabled store and
     an opaque origin (which is what an about:blank page has, and therefore
     what the build's own harness runs under) all throw on access rather than
     returning null, so the fallback is a plain object. It loses the data when
     the tab closes, which is bad — but a keypad that refuses to load is
     worse, and the banner has already said the data lives in this browser. */
  function memoryStore() {
    var held = {};
    return {
      getItem: function (key) {
        return Object.prototype.hasOwnProperty.call(held, key) ? held[key] : null;
      },
      setItem: function (key, value) { held[key] = String(value); },
      removeItem: function (key) { delete held[key]; }
    };
  }

  /* Set inside pickStore, not compared afterwards. `backend === window.
     localStorage` reads the property a second time, and on an opaque origin
     that read throws too -- outside the try, taking the whole file with it
     and leaving TallyStore undefined. Which is how it was written, and what
     tools/build_static.py found the first time it loaded this file in a real
     browser. */
  var durable = false;

  function pickStore() {
    try {
      var probe = '__tally__';
      window.localStorage.setItem(probe, '1');
      window.localStorage.removeItem(probe);
      durable = true;
      return window.localStorage;
    } catch (e) {
      return memoryStore();
    }
  }

  var backend = (typeof window !== 'undefined') ? pickStore() : memoryStore();

  /* ---------------------------------------------------------------- dates */
  /* ISO strings in, ISO strings out, arithmetic in whole UTC days. */
  var DAY_MS = 86400000;

  function isoOf(millis) {
    var date = new Date(millis);
    return date.getUTCFullYear() + '-' +
      String(date.getUTCMonth() + 1).padStart(2, '0') + '-' +
      String(date.getUTCDate()).padStart(2, '0');
  }

  function millisOf(iso) { return Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1,
                                           +iso.slice(8, 10)); }

  function shift(iso, days) { return isoOf(millisOf(iso) + days * DAY_MS); }

  function weekday(iso) {
    /* Python's date.weekday(): Monday is 0. */
    return (new Date(millisOf(iso)).getUTCDay() + 6) % 7;
  }

  function firstOfMonth(iso) { return iso.slice(0, 8) + '01'; }

  /* Overridable, and overridden by the build so both sides agree on what day
     it is. Local date, not UTC: "today" means the user's today. */
  var api = {};
  api.today = function () {
    var now = new Date();
    return now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0') +
      '-' + String(now.getDate()).padStart(2, '0');
  };

  /* entries.as_date. Narrowed on purpose: date.fromisoformat also reads week
     dates and the basic YYYYMMDD form, and nothing in this app produces
     either — the only sender is a native <input type="date">. Both forms the
     app can actually emit are accepted, and anything else is refused with the
     message entries.py uses rather than silently becoming today. */
  function asDate(value) {
    if (value === null || value === undefined || value === '') return api.today();
    var text = String(value).trim();
    var match = /^(\d{4})-?(\d{2})-?(\d{2})$/.exec(text);
    if (!match) throw EntryError('unreadable date: ' + M.repr(value));
    var iso = match[1] + '-' + match[2] + '-' + match[3];
    // Round-trip, so 2026-02-30 and 2026-13-01 are refused the way
    // date.fromisoformat refuses them rather than rolling over into March.
    if (isoOf(millisOf(iso)) !== iso) {
      throw EntryError('unreadable date: ' + M.repr(value));
    }
    return iso;
  }

  /* ------------------------------------------------------------ the rows */
  function load() {
    try {
      var raw = backend.getItem(ENTRIES_KEY);
      var rows = raw ? JSON.parse(raw) : [];
      return Array.isArray(rows) ? rows : [];
    } catch (e) {
      /* Unreadable is treated as empty rather than fatal, the same way
         app.js treats a corrupt queue. It is the only choice that still
         lets somebody record what they are standing there spending. */
      return [];
    }
  }

  function save(rows) {
    backend.setItem(ENTRIES_KEY, JSON.stringify(rows));
  }

  function nextId() {
    var seen = Number(backend.getItem(SEQ_KEY) || 0);
    var rows = load();
    for (var i = 0; i < rows.length; i++) seen = Math.max(seen, rows[i].id);
    var id = seen + 1;
    backend.setItem(SEQ_KEY, String(id));
    return id;
  }

  function newClientId() {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
      return crypto.randomUUID().replace(/-/g, '');
    }
    return 'x' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  }

  function shown(row) {
    /* entries._shown: the figure formatted here, so there is one
       implementation of the rounding rather than two that drift. */
    var out = {};
    for (var key in row) if (Object.prototype.hasOwnProperty.call(row, key)) out[key] = row[key];
    out.amount_text = M.format(row.amount, row.currency);
    out.plain = M.plain(row.amount);
    return out;
  }

  /* SQLite's `LIMIT ?` treats a negative as unlimited. Mirrored rather than
     clamped, because app.py passes the request's limit straight through. */
  function limited(rows, limit) {
    var n = Math.trunc(Number(limit));
    if (!isFinite(n)) n = 0;
    return n < 0 ? rows : rows.slice(0, n);
  }

  function inRange(row, first, last) {
    return row.spent_on >= first && row.spent_on <= last;
  }

  /* --------------------------------------------------------------- adding */
  api.add = function (sent) {
    var cents = M.parse(sent.amount);
    var category = String(sent.category === null || sent.category === undefined
                          ? '' : sent.category).trim();
    if (!category) throw EntryError('an expense needs a category');

    var on = asDate(sent.date);
    if (on > api.today()) {
      /* A future expense has not happened. Refusing beats having today's
         total include something that has not been spent. */
      throw EntryError('that date is in the future');
    }

    var mark = String(sent.clientId || '').trim() || newClientId();
    var rows = load();
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].client_id === mark) return { id: rows[i].id, result: 'duplicate' };
    }

    var row = {
      id: nextId(),
      spent_on: on,
      amount: cents,
      currency: M.known(sent.currency),
      category: category,
      note: String(sent.note === null || sent.note === undefined ? '' : sent.note).trim(),
      created_at: new Date().toISOString().slice(0, 19),
      client_id: mark
    };
    rows.push(row);
    save(rows);
    return { id: row.id, result: 'added' };
  };

  api.remove = function (id) {
    var rows = load();
    var kept = rows.filter(function (row) { return row.id !== Number(id); });
    if (kept.length === rows.length) return false;
    save(kept);
    return true;
  };

  api.forgetEverything = function () {
    backend.removeItem(ENTRIES_KEY);
    backend.removeItem(SEQ_KEY);
  };

  api.durable = function () { return durable; };
  api.count = function () { return load().length; };

  /* -------------------------------------------------------------- reading */
  api.recent = function (limit, day) {
    if (limit === undefined) limit = 25;
    var rows = load();
    if (day !== null && day !== undefined && day !== '') {
      var on = asDate(day);
      rows = rows.filter(function (row) { return row.spent_on === on; });
    }
    rows = rows.slice().sort(function (a, b) {
      if (a.spent_on !== b.spent_on) return a.spent_on < b.spent_on ? 1 : -1;
      return b.id - a.id;
    });
    return limited(rows, limit).map(shown);
  };

  api.usual = function (limit, window, on) {
    if (limit === undefined) limit = USUAL_LIMIT;
    if (window === undefined) window = USUAL_WINDOW_DAYS;
    var since = shift(asDate(on), -window);

    /* GROUP BY category, amount, currency, note — the triple that identifies
       a habit. Not the category alone: "Coffee" is not a shortcut if it is
       sometimes 2.50 and sometimes 18. */
    var groups = {};
    var order = [];
    load().forEach(function (row) {
      if (row.spent_on < since) return;
      var key = JSON.stringify([row.category, row.amount, row.currency, row.note]);
      if (!groups[key]) {
        groups[key] = { category: row.category, amount: row.amount,
                        currency: row.currency, note: row.note,
                        times: 0, last_on: row.spent_on };
        order.push(key);
      }
      groups[key].times += 1;
      if (row.spent_on > groups[key].last_on) groups[key].last_on = row.spent_on;
    });

    var rows = order.map(function (key) { return groups[key]; })
      .filter(function (group) { return group.times >= USUAL_MIN_COUNT; })
      .sort(function (a, b) {
        if (a.times !== b.times) return b.times - a.times;
        if (a.last_on !== b.last_on) return a.last_on < b.last_on ? 1 : -1;
        return a.category < b.category ? -1 : (a.category > b.category ? 1 : 0);
      });

    return limited(rows, limit).map(function (group) {
      return { category: group.category, amount: group.amount,
               amount_text: M.format(group.amount, group.currency),
               currency: group.currency, note: group.note,
               times: group.times, last_on: group.last_on };
    });
  };

  api.categories = function () {
    var times = {};
    var order = [];
    load().forEach(function (row) {
      if (times[row.category] === undefined) { times[row.category] = 0; order.push(row.category); }
      times[row.category] += 1;
    });
    /* Most-used first, then by name: the chip you want is nearly always one
       you have used before, and alphabetical order buries it. */
    var used = order.sort(function (a, b) {
      if (times[a] !== times[b]) return times[b] - times[a];
      return a < b ? -1 : (a > b ? 1 : 0);
    });
    DEFAULT_CATEGORIES.forEach(function (name) {
      if (used.indexOf(name) < 0) used.push(name);
    });
    return used;
  };

  function byCurrency(first, last) {
    var sums = {};
    var order = [];
    load().forEach(function (row) {
      if (!inRange(row, first, last)) return;
      if (!sums[row.currency]) { sums[row.currency] = { cents: 0, entries: 0 }; order.push(row.currency); }
      sums[row.currency].cents += row.amount;
      sums[row.currency].entries += 1;
    });
    return order.map(function (code) {
      return { currency: code, cents: sums[code].cents,
               entries: sums[code].entries,
               text: M.format(sums[code].cents, code) };
    }).sort(function (a, b) {
      if (a.cents !== b.cents) return b.cents - a.cents;
      return a.currency < b.currency ? -1 : 1;
    });
  }

  api.totals = function (on) {
    /* Per currency, never summed across them: adding euros to dollars needs
       a rate this app does not have. */
    var day = asDate(on);
    var monday = shift(day, -weekday(day));
    return {
      day: day,
      today: byCurrency(day, day),
      week: byCurrency(monday, day),
      month: byCurrency(firstOfMonth(day), day)
    };
  };

  api.byCategory = function (first, last) {
    var day = api.today();
    var lo = asDate(first || firstOfMonth(day));
    var hi = asDate(last || day);
    var sums = {};
    var order = [];
    load().forEach(function (row) {
      if (!inRange(row, lo, hi)) return;
      var key = JSON.stringify([row.category, row.currency]);
      if (!sums[key]) {
        sums[key] = { category: row.category, currency: row.currency, cents: 0, entries: 0 };
        order.push(key);
      }
      sums[key].cents += row.amount;
      sums[key].entries += 1;
    });
    return order.map(function (key) {
      var group = sums[key];
      return { category: group.category, currency: group.currency,
               cents: group.cents, entries: group.entries,
               text: M.format(group.cents, group.currency) };
    }).sort(function (a, b) {
      if (a.cents !== b.cents) return b.cents - a.cents;
      if (a.category !== b.category) return a.category < b.category ? -1 : 1;
      return a.currency < b.currency ? -1 : 1;
    });
  };

  api.days = function (limit, on) {
    if (limit === undefined) limit = 14;
    var end = asDate(on);
    var start = shift(end, -(Math.trunc(limit) - 1));
    var found = {};
    load().forEach(function (row) {
      if (!inRange(row, start, end)) return;
      found[row.spent_on] = (found[row.spent_on] || 0) + row.amount;
    });
    var out = [];
    for (var offset = 0; offset < Math.trunc(limit); offset++) {
      var day = shift(start, offset);
      var cents = found[day] || 0;
      /* Zero rather than omitted: a gap in a bar chart reads as missing
         data, and a day you spent nothing is a fact worth seeing. */
      out.push({ date: day, cents: cents, text: M.format(cents, M.DEFAULT_CURRENCY) });
    }
    return out;
  };

  /* ------------------------------------------------------------- the CSV */
  function description(category, note) {
    var text = String(note || '').trim();
    /* A plain hyphen, not an em-dash: Excel on Windows re-saves in the local
       codepage and mangles anything outside ASCII. */
    return text ? category + ' - ' + text : category;
  }

  function csvField(value) {
    /* QUOTE_MINIMAL, the way Python's csv module does it: quote only when the
       field holds the delimiter, a quote, or the line terminator, and double
       any quote inside. */
    var text = String(value);
    if (text.indexOf(',') < 0 && text.indexOf('"') < 0 && text.indexOf('\n') < 0) {
      return text;
    }
    return '"' + text.replace(/"/g, '""') + '"';
  }

  function readCsv(text) {
    /* The other half: reconciles() re-reads the file it just wrote, rather
       than re-adding the numbers from memory. Adding them up again would
       check nothing — the point is whether the *file* adds up. */
    var rows = [];
    var row = [];
    var field = '';
    var quoted = false;
    var i = 0;
    while (i < text.length) {
      var char = text.charAt(i);
      if (quoted) {
        if (char === '"') {
          if (text.charAt(i + 1) === '"') { field += '"'; i += 2; continue; }
          quoted = false; i += 1; continue;
        }
        field += char; i += 1; continue;
      }
      if (char === '"') { quoted = true; i += 1; continue; }
      if (char === ',') { row.push(field); field = ''; i += 1; continue; }
      if (char === '\n') { row.push(field); rows.push(row); row = []; field = ''; i += 1; continue; }
      if (char === '\r') { i += 1; continue; }
      field += char; i += 1;
    }
    if (field !== '' || row.length) { row.push(field); rows.push(row); }
    if (!rows.length) return [];
    var head = rows[0];
    return rows.slice(1).map(function (cells) {
      var out = {};
      head.forEach(function (name, index) { out[name] = cells[index]; });
      return out;
    });
  }

  function exportRange(first, last) {
    var day = api.today();
    return [asDate(first || firstOfMonth(day)), asDate(last || day)];
  }

  api.asCsv = function (first, last) {
    var range = exportRange(first, last);
    var rows = load().filter(function (row) { return inRange(row, range[0], range[1]); })
      .sort(function (a, b) {
        if (a.spent_on !== b.spent_on) return a.spent_on < b.spent_on ? -1 : 1;
        return a.id - b.id;
      });
    var out = COLUMNS.join(',') + '\n';
    rows.forEach(function (row) {
      out += [csvField(row.spent_on),
              csvField(description(row.category, row.note)),
              /* Negative: the wallet app reads a negative as money out. */
              csvField(M.plain(-row.amount)),
              csvField(row.currency)].join(',') + '\n';
    });
    return out;
  };

  api.filename = function (first, last) {
    var range = exportRange(first, last);
    if (range[0] === range[1]) return 'tally-' + range[0] + '.csv';
    return 'tally-' + range[0] + '-to-' + range[1] + '.csv';
  };

  api.reconciles = function (first, last) {
    var text = api.asCsv(first, last);
    var fromFile = M.total(readCsv(text)
      .filter(function (row) { return row.Amount; })
      .map(function (row) { return M.parse(row.Amount.replace(/^-+/, '')); }));
    var range = exportRange(first, last);
    var stored = 0;
    load().forEach(function (row) {
      if (inRange(row, range[0], range[1])) stored += row.amount;
    });
    return { agrees: fromFile === stored, file: fromFile, stored: stored };
  };

  api.summary = function (first, last) {
    var range = exportRange(first, last);
    var count = 0;
    var cents = 0;
    load().forEach(function (row) {
      if (!inRange(row, range[0], range[1])) return;
      count += 1;
      cents += row.amount;
    });
    return { first: range[0], last: range[1], entries: count, cents: cents,
             text: M.format(cents, M.DEFAULT_CURRENCY),
             filename: api.filename(first, last) };
  };

  /* --------------------------------------------------------- /api/overview */
  api.overview = function (on) {
    return {
      today: api.today(),
      totals: api.totals(on),
      usual: api.usual(),
      categories: api.categories(),
      recent: api.recent(25),
      byCategory: api.byCategory(),
      days: api.days(),
      currencies: M.CURRENCIES.slice(),
      defaultCurrency: M.DEFAULT_CURRENCY,
      export: api.summary()
    };
  };

  /* Used only by tools/build_static.py, to run this file against the Python
     over a fixture without touching whatever is really in the browser. */
  api._useBackend = function (store, todayIso) {
    backend = store || memoryStore();
    durable = false;
    if (todayIso) api.today = function () { return todayIso; };
  };

  api.EntryError = EntryError;
  api.DEFAULT_CATEGORIES = DEFAULT_CATEGORIES;
  api.COLUMNS = COLUMNS;
  return api;
})();

if (typeof module !== 'undefined' && module.exports) module.exports = TallyStore;
