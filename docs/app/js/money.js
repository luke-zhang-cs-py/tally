/* ---------------------------------------------------------------------------
   money.js — money.py, in the browser
   ---------------------------------------------------------------------------
   A line-for-line port of money.py, and the one file in this bundle that is
   not allowed to be "close enough". Everything else here is presentation; this
   decides what a number *is*.

   money.py's whole argument is that an amount is an integer number of cents,
   because 0.1 + 0.2 is 0.30000000000000004 and a day's total that disagrees
   with the rows printed above it costs a reader their trust in every other
   figure on the screen. A port that quietly reintroduces a float, or rounds
   the third decimal place instead of refusing it, would put that back — and
   it would do it on the *published* build, which is the copy most people see.

   So this is not trusted. `tools/build_static.py` runs this exact file in a
   real browser against the real money.py over several hundred values —
   parsing, formatting, the plain CSV form, totals and currency fallback — and
   refuses to write docs/app/ if any one of them disagrees, down to the text of
   the error message.

   Four places where JavaScript cannot be made to match. None of them is
   reachable from the keypad, all of them are narrowed rather than papered
   over, and each is named in build_static.py's case list as an exclusion, so
   the gap between "checked" and "identical" is written down in both files
   rather than in neither:

     * Python has int and float; JavaScript has one number type. money.py
       short-circuits `isinstance(text, int)` to `text * 100`, so `parse(0)`
       returns 0 where `parse(0.0)` goes through the text path and raises. This
       file cannot tell those apart and takes the int branch for any integral
       number. The build's case list therefore holds no integral floats.
     * Python integers are unbounded. Above 2**53 cents — ninety trillion euro
       — this loses precision and money.py does not. The keypad stops at
       99999999 cents, so nothing in the app can reach it, but it is a
       difference and not a rounding.
     * Python's `\d` matches every Unicode decimal digit; JavaScript's matches
       0-9. So money.py reads an Arabic-Indic '٠' as a zero and this file
       strips it as punctuation. Both refuse the amount; they disagree about
       which message to refuse it with. Making the port agree would mean
       carrying a Unicode table to parse digits nothing in this app can type.
     * `repr()` below is Python's repr for the printable cases only. Python
       escapes an unprintable character as `\xa0`; this prints it. Only error
       messages are affected, and only for input the app cannot produce.
   --------------------------------------------------------------------------- */

var TallyMoney = (function () {
  'use strict';

  /* Same order as money.py: euro first, because that is where the spending
     happens. */
  var CURRENCIES = ['EUR', 'CAD', 'USD'];
  var DEFAULT_CURRENCY = 'EUR';

  var SYMBOLS = { EUR: '€', CAD: 'CA$', USD: 'US$' };

  var MINOR_UNITS = 2;
  var SCALE = Math.pow(10, MINOR_UNITS);

  var ALLOWED = /[^\d.,]/g;

  /* money.py raises MoneyError, a ValueError subclass. The shim in
     static-api.js turns one into a 400 with the same body the Flask app
     sends, so the page's error handling does not need to know which build it
     is running in. A tagged Error rather than a subclass: the bundle targets
     browsers old enough that `class X extends Error` breaks instanceof. */
  function MoneyError(message) {
    var error = new Error(message);
    error.name = 'MoneyError';
    return error;
  }

  /* Python's repr(), for the handful of types that reach an error message.
     The messages are part of what the build compares -- an error a person
     reads is as much the behaviour as the number is -- and they interpolate
     `{text!r}`, so matching them means matching repr. */
  function repr(value) {
    if (value === null || value === undefined) return 'None';
    if (value === true) return 'True';
    if (value === false) return 'False';
    if (typeof value === 'number') return String(value);
    var text = String(value);
    // Python prefers single quotes, and switches to double only when the
    // string holds a single quote and no double quote.
    var quote = (text.indexOf("'") >= 0 && text.indexOf('"') < 0) ? '"' : "'";
    var out = text.replace(/\\/g, '\\\\');
    if (quote === "'") out = out.replace(/'/g, "\\'");
    return quote + out.replace(/\n/g, '\\n').replace(/\r/g, '\\r')
                      .replace(/\t/g, '\\t') + quote;
  }

  function group(whole) {
    /* Python's `{n:,}`. */
    return String(whole).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  function pad(fraction) {
    var text = String(fraction);
    while (text.length < MINOR_UNITS) text = '0' + text;
    return text;
  }

  function parse(text) {
    if (text === null || text === undefined) throw MoneyError('no amount given');
    /* `isinstance(text, int) and not isinstance(text, bool)`. Booleans fall
       through to the text path in money.py and fail there for having no
       digits, so they must fall through here too. */
    if (typeof text === 'number' && isFinite(text) && Math.floor(text) === text) {
      return text * SCALE;
    }

    var raw = String(text);
    /* Caught before the strip, because the strip removes it: "-5" became 5,
       so typing a minus recorded a five-euro expense instead of refusing. */
    if (raw.indexOf('-') >= 0) throw MoneyError('an expense is a positive amount');

    var cleaned = raw.replace(ALLOWED, '').replace(/,/g, '.');
    if (!/\d/.test(cleaned)) throw MoneyError('no digits in ' + repr(text));
    if (cleaned.split('.').length - 1 > 1) throw MoneyError('cannot read ' + repr(text));

    var cut = cleaned.indexOf('.');
    var whole = cut < 0 ? cleaned : cleaned.slice(0, cut);
    var fraction = cut < 0 ? '' : cleaned.slice(cut + 1);
    if (fraction.length > MINOR_UNITS) {
      throw MoneyError(repr(text) + ' has more than ' + MINOR_UNITS +
                       ' decimal places');
    }

    var cents = Number(whole || 0) * SCALE + Number((fraction + '00').slice(0, MINOR_UNITS));
    if (cents <= 0) throw MoneyError('an amount has to be more than nothing');
    return cents;
  }

  function format(cents, currency) {
    if (currency === undefined) currency = DEFAULT_CURRENCY;
    /* money.py takes a number, not None, and raises a TypeError rather than
       returning "" -- a blank where a figure belongs is harder to notice than
       a crash. */
    if (cents === null || cents === undefined || typeof cents !== 'number') {
      throw new TypeError('format() needs a number of cents, got ' + repr(cents));
    }
    var sign = cents < 0 ? '-' : '';
    var size = Math.abs(Math.trunc(cents));
    /* hasOwnProperty, not `SYMBOLS[currency] ||`: a currency named
       "constructor" or "toString" would otherwise find something on the
       prototype and print it as the symbol. */
    var symbol = Object.prototype.hasOwnProperty.call(SYMBOLS, currency)
      ? SYMBOLS[currency] : currency + ' ';
    return sign + symbol + group(Math.floor(size / SCALE)) + '.' + pad(size % SCALE);
  }

  function plain(cents) {
    /* No symbol and no grouping: "1,234.56" is two fields to anything that
       splits on commas, which is the one thing a CSV reader reliably does. */
    var sign = cents < 0 ? '-' : '';
    var size = Math.abs(Math.trunc(cents));
    return sign + String(Math.floor(size / SCALE)) + '.' + pad(size % SCALE);
  }

  function total(amounts) {
    var sum = 0;
    for (var i = 0; i < amounts.length; i++) {
      if (amounts[i] === null || amounts[i] === undefined) continue;
      sum += Math.trunc(Number(amounts[i]));
    }
    return sum;
  }

  function known(currency) {
    /* Falls back rather than raising: losing the entry is worse than filing
       it in euros and letting it be corrected. */
    var code = String(currency ? currency : '').trim().toUpperCase();
    return CURRENCIES.indexOf(code) >= 0 ? code : DEFAULT_CURRENCY;
  }

  return {
    CURRENCIES: CURRENCIES,
    DEFAULT_CURRENCY: DEFAULT_CURRENCY,
    SYMBOLS: SYMBOLS,
    MINOR_UNITS: MINOR_UNITS,
    MoneyError: MoneyError,
    repr: repr,
    parse: parse,
    format: format,
    plain: plain,
    total: total,
    known: known
  };
})();

if (typeof module !== 'undefined' && module.exports) module.exports = TallyMoney;
