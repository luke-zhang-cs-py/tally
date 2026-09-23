/* ---------------------------------------------------------------------------
   static-ui.js — the three things this build has to do in the page
   ---------------------------------------------------------------------------
   Loaded last, after app.js, because everything here is either a control
   app.js does not know about or a correction to one it does. Nothing in
   static/js/app.js is edited to make this build work: it is copied byte for
   byte by tools/build_static.py, and the moment it is forked the two copies
   start drifting and the published one is the one nobody runs the tests
   against.

   1. The export. In the Flask app "Export CSV" is a plain <a href="/export.csv">
      and the server answers it with a Content-Disposition. There is no server
      here, so the link is rewritten to a button and the same CSV — built by
      store.js, which is the port of export.py — is handed to the browser as a
      blob. The filename comes from the shim's Content-Disposition rather than
      being made up again here, so both builds name the file identically.

   2. The storage warning. store.js falls back to an in-memory object when
      localStorage throws, which is what a private window and a browser with
      site data switched off both do. The fallback is right — a keypad that
      refuses to load helps nobody — but it turns "your data stays in this
      browser" into "your data is gone when you close this tab", and that is a
      different promise. It gets said out loud.

   3. Forgetting. The banner says the entries live in this browser and nowhere
      else. The honest counterpart of that is a way to take them out again,
      which is one localStorage key and therefore belongs here rather than in
      the app both builds share.
   --------------------------------------------------------------------------- */

(function () {
  'use strict';

  function el(id) { return document.getElementById(id); }

  /* --------------------------------------------------------- the export */
  function nameFrom(reply) {
    /* Whatever the shim said, so the two builds name the file the same way.
       Only falls back if that ever stops being true. */
    var header = reply.headers && reply.headers.get
      ? reply.headers.get('content-disposition') : null;
    var found = header && /filename="([^"]+)"/.exec(header);
    return found ? found[1] : 'tally.csv';
  }

  function download(text, filename) {
    var blob = new Blob([text], { type: 'text/csv;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var link = document.createElement('a');
    link.href = url;
    link.download = filename;
    /* Appended before the click: Firefox ignores a click on a link that is
       not in the document, and an export that silently does nothing is the
       worst of the available failures. */
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    /* Revoked on a turn of the event loop rather than immediately -- the
       download is started by the click but not necessarily finished by the
       time this line runs. */
    setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
  }

  var exportLink = el('exportCsv');
  if (exportLink) {
    exportLink.addEventListener('click', function (event) {
      event.preventDefault();
      /* Through fetch, not straight into TallyStore: this goes through the
         same shim, and therefore the same code path, as everything else the
         page asks for. */
      window.fetch('/export.csv').then(function (reply) {
        return reply.text().then(function (text) {
          download(text, nameFrom(reply));
        });
      });
    });
  }

  /* ------------------------------------------------------- the warning */
  if (!TallyStore.durable()) {
    var warning = el('staticWarn');
    if (warning) {
      warning.textContent = 'This browser will not let the page store ' +
        'anything — a private window, or site data switched off. Entries ' +
        'will work, but they are held in memory and go when this tab closes. ' +
        'Export before you leave.';
    }
  }

  /* ------------------------------------------------------ forgetting it */
  var forget = el('forget');
  if (forget) {
    forget.addEventListener('click', function () {
      var held = TallyStore.count();
      if (!held) {
        window.alert('There is nothing stored in this browser.');
        return;
      }
      if (!window.confirm(
          'Delete all ' + held + ' entries held in this browser?\n\n' +
          'There is no copy anywhere else. Export the CSV first if you ' +
          'want one.')) {
        return;
      }
      TallyStore.forgetEverything();
      /* The queue too: app.js keeps unsent entries under its own keys, and
         leaving them would mean "forget everything" quietly put some of them
         back on the next flush. */
      try {
        window.localStorage.removeItem('tally.queue');
        window.localStorage.removeItem('tally.queue.quarantine');
      } catch (e) { /* the same store that would not hold them */ }
      window.location.reload();
    });
  }
})();
