"""The theme tokens, their contrast, and the toggle.

Every page here -- the Flask app, its browser build in docs/app/ and the
write-up in docs/index.html -- opens dark with `class="theme-dark"` in the
markup, so it is themed before any script runs, and offers a toggle to
light. The colours are tokens, one block per theme; the rules read only
the tokens. These tests hold that together:

  * every theme a page's THEMES list names has a block, and every block
    sets the same tokens as the default, so a theme cannot be half-added;
  * every foreground/background pairing the page actually uses meets WCAG
    AA in every theme -- read from the tokens, so the states a DOM scan
    never sees (a held key, a pressed chip, the error notice) are covered;
  * no raw colour has crept back into a rule, and nothing follows the
    system's colour scheme any more;
  * the browser build is the build of the current sources, so the
    published demo cannot fall behind the app it copies;
  * nothing that clears or exports the app's storage can reach the
    shared 'theme' key.

The second half starts a real browser: the no-JavaScript load, the toggle,
an unknown saved theme, and a contrast scan of every visible text node in
both themes. Playwright is a build-time dependency here, not a test one
(see tools/build_static.py), so those tests skip, and say so, where it is
not installed -- CI included.
"""
import io
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools import build_static as build   # noqa: E402


def read(*parts):
    with io.open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


APP_CSS = ("static", "css", "style.css")
BANNER_CSS = ("tools", "static_src", "css", "static.css")
APP_JS = ("static", "js", "app.js")
TEMPLATE = ("templates", "index.html")
DEMO = ("docs", "app", "index.html")
WRITEUP = ("docs", "index.html")

# The contract every theme block in this family sets, under these names.
CONTRACT = {
    "--color-bg", "--color-surface", "--color-surface-subtle",
    "--color-border", "--color-border-strong", "--color-text-primary",
    "--color-text-muted", "--color-text-subtle", "--color-accent",
    "--color-accent-hover", "--color-accent-subtle",
    "--color-text-on-accent", "--color-danger",
}


def styles_of(html):
    return "\n".join(re.findall(r"<style>(.*?)</style>", html, re.S))


def scripts_of(html):
    return "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))


def without_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def theme_blocks(css):
    """{theme name: {token: value}}. The default block is the one that also
    carries `:where(:root)`, the fallback for an unknown class."""
    css = without_comments(css)
    blocks = {}
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        names = re.findall(r"\.theme-([a-z]+)", selector)
        if not names:
            continue
        tokens = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body))
        for name in names:
            blocks[name] = {k: v.strip() for k, v in tokens.items()}
        if ":where(:root)" in selector:
            blocks["(fallback)"] = blocks[names[0]]
    return blocks


def themes_list(script):
    found = re.search(r"var THEMES = \[([^\]]*)\];", script)
    assert found, "no THEMES list in the script"
    return re.findall(r"'([a-z]+)'", found.group(1))


# One entry per group of pages that shares a palette: where its tokens
# live, where its THEMES list lives, and the pairings it uses.
GROUPS = {
    "app": {
        "css": lambda: read(*APP_CSS),
        "script": lambda: read(*APP_JS),
        "text": (
            # body text in the header and footer, on cards, keys, chips,
            # entry rows and inputs, a key held down, and the build's banner
            [(fg, bg) for fg in ("text-primary", "text-muted", "text-subtle")
             for bg in ("bg", "surface", "surface-subtle", "surface-pressed",
                        "banner")]
            # accent-subtle is not painted by any rule here (see "unpainted"
            # below, which fails the moment one does). The palette holds the
            # two stronger text colours on it; text-subtle, at 4.31:1 in
            # light, would need checking -- and passing -- before it could be.
            + [(fg, "accent-subtle") for fg in ("text-primary", "text-muted")]
            + [("accent", "surface-subtle"),      # a habit's price
               ("accent", "surface-pressed"),     # ... while it is pressed
               ("accent", "banner"),              # the banner's link
               ("accent-hover", "banner"),        # ... hovered
               ("text-on-accent", "accent"),      # a picked chip, Save
               ("warning", "warning-subtle"),     # the notice
               ("warning", "surface-subtle"),     # "pending" in a row
               ("success", "success-subtle"),     # notice.good
               ("danger", "danger-subtle"),       # notice.bad
               ("danger", "bg"),                  # the reconcile mismatch
               ("danger", "surface-subtle"),      # delete, hovered, in a row
               ("danger", "banner")]              # no-storage warning
        ),
        # Inputs and the currency select are filled with bg, so their edge
        # is checked against both sides of it: the card outside and the
        # field inside.
        "ui": [("border-strong", "surface"),       # input and select edges
               ("border-strong", "bg"),
               ("accent", "surface"),              # focus ring
               ("accent", "surface-subtle"),       # bar on its rail
               ("accent", "bg")],                  # toggle focus ring
        # Defined by the palette but not a background anything sits on.
        "unpainted": ["accent-subtle"],
        "rules": lambda: read(*APP_CSS) + read(*BANNER_CSS),
    },
    "writeup": {
        "css": lambda: styles_of(read(*WRITEUP)),
        "script": lambda: scripts_of(read(*WRITEUP)),
        "text": (
            [(fg, bg) for fg in ("text-primary", "text-muted", "text-subtle")
             for bg in ("bg", "surface")]
            + [("text-primary", "surface-subtle"),   # pre
               ("text-muted", "surface-subtle"),     # code inside a paragraph
               ("accent", "surface"),                # step numbers, pressed
               ("success", "surface"),               # the "fixed" flags
               ("danger", "surface")]
        ),
        "ui": [("accent", "track"),                  # a bar on its track
               ("accent", "surface"),                # a pressed button's edge
               ("border-strong", "surface"),         # a hovered button's edge
               ("border-strong", "bg")],
        "unpainted": [],
        "rules": lambda: styles_of(read(*WRITEUP)),
    },
}


def luminance(hex_colour):
    value = hex_colour.lstrip("#")
    assert re.fullmatch(r"[0-9a-fA-F]{6}", value), (
        "not a six-digit hex colour: %r" % hex_colour)
    rgb = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    rgb = [c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4
           for c in rgb]
    return .2126 * rgb[0] + .7152 * rgb[1] + .0722 * rgb[2]


def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + .05) / (low + .05)


# ------------------------------------------------------------ the tokens

@pytest.mark.parametrize("group", sorted(GROUPS))
def test_every_theme_sets_the_same_tokens_as_the_default(group):
    blocks = theme_blocks(GROUPS[group]["css"]())
    themes = themes_list(GROUPS[group]["script"]())
    assert themes[0] == "dark", "the first theme is the one in the markup"
    assert "(fallback)" in blocks, (
        "no `:where(:root), .theme-dark` block, so an unknown theme class "
        "would leave the page unstyled")
    default = set(blocks["dark"])
    missing = CONTRACT - default
    assert not missing, "the default theme lacks %s" % sorted(missing)
    for name in themes:
        assert name in blocks, "THEMES names %r but there is no .theme-%s " \
            "block" % (name, name)
        differ = sorted(set(blocks[name]) ^ default)
        assert not differ, "theme %r and the default disagree on %s" % (
            name, differ)


@pytest.mark.parametrize("group", sorted(GROUPS))
def test_every_pairing_meets_aa_in_every_theme(group):
    """4.5:1 for text, 3:1 for the edges and marks that identify a control
    or carry meaning. From the tokens, so every state is checked whether or
    not anything on screen shows it right now."""
    spec = GROUPS[group]
    blocks = theme_blocks(spec["css"]())
    short = []
    for name in themes_list(spec["script"]()):
        tokens = blocks[name]
        for pairs, need in ((spec["text"], 4.5), (spec["ui"], 3.0)):
            for fg, bg in pairs:
                ratio = contrast(tokens["--color-" + fg],
                                 tokens["--color-" + bg])
                if ratio < need:
                    short.append("%s: %s on %s is %.2f:1, needs %.1f"
                                 % (name, fg, bg, ratio, need))
    assert not short, "below WCAG AA:\n  " + "\n  ".join(short)


@pytest.mark.parametrize("group", sorted(GROUPS))
def test_every_token_a_pairing_names_exists(group):
    """A pairing against a token nobody defines would be a KeyError above;
    this says which one, and also catches a pair list gone empty."""
    spec = GROUPS[group]
    tokens = theme_blocks(spec["css"]())["dark"]
    named = {t for pair in spec["text"] + spec["ui"] for t in pair}
    assert len(named) > 8
    assert not sorted("--color-" + t for t in named
                      if "--color-" + t not in tokens)
    # A token kept out of the text pairings above must stay off every
    # background, or the text on it would be unchecked.
    rules = without_comments(spec["rules"]())
    for name in spec["unpainted"]:
        assert not re.search(r"background[\w-]*\s*:[^;}]*var\(--color-%s\)"
                             % re.escape(name), rules), (
            "a rule paints --color-%s, but not every text colour is checked "
            "against it" % name)


@pytest.mark.parametrize("where", [APP_CSS, BANNER_CSS, WRITEUP],
                         ids=lambda parts: "/".join(parts))
def test_no_rule_holds_a_raw_colour(where):
    """Only the theme blocks may say what a colour is."""
    css = read(*where)
    if where == WRITEUP:
        css = styles_of(css)
    css = without_comments(css)
    css = re.sub(r"[^{}]*\.theme-[a-z]+[^{}]*\{[^{}]*\}", "", css)
    raw = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", css)
    assert not raw, "raw colours outside a theme block: %s" % raw


@pytest.mark.parametrize("where", [APP_CSS, BANNER_CSS, TEMPLATE, WRITEUP,
                                   APP_JS],
                         ids=lambda parts: "/".join(parts))
def test_nothing_follows_the_system_colour_scheme(where):
    """The page is dark until the visitor chooses light."""
    text = read(*where)
    assert "prefers-color-scheme" not in text
    assert "data-theme" not in text


@pytest.mark.parametrize("where", [TEMPLATE, DEMO, WRITEUP],
                         ids=lambda parts: "/".join(parts))
def test_the_markup_opens_dark_and_restores_before_paint(where):
    html = read(*where)
    assert re.search(r'<html lang="en" class="theme-dark">', html)
    head = html.split("</head>")[0]
    assert "localStorage.getItem('theme')" in head
    assert "classList.replace('theme-dark', 'theme-' + saved)" in head
    assert re.search(r'<button id="themeBtn" type="button"|'
                     r'<button type="button" class="themeToggle" '
                     r'id="themeBtn"', html)
    assert 'aria-label="Switch to light theme"' in html
    # The type is self-hosted (DESIGN.md): fonts.css is loaded in the head,
    # nothing is fetched from a font service, and no banned family is named.
    assert re.search(r'<link rel="stylesheet" href="[^"]*fonts\.css[^"]*">',
                     head)
    assert "googleapis" not in html and "gstatic" not in html
    banned = re.findall(r"(Inter|Roboto|Open Sans|Arial|system-ui|"
                        r"-apple-system|Segoe UI|Helvetica)", html)
    assert not banned, banned


def test_the_body_background_is_the_bg_token():
    app = without_comments(read(*APP_CSS))
    assert re.search(r"\nbody \{[^}]*background: var\(--color-bg\)", app)
    page = without_comments(styles_of(read(*WRITEUP)))
    assert re.search(r"\.viz-root \{[^}]*background: var\(--color-bg\)", page)


def test_the_demo_is_the_build_of_the_current_sources():
    """docs/app/ is generated. Edit a source without re-running
    tools/build_static.py and the published demo silently keeps the old
    look -- or the old theme code."""
    stale = []
    for name, body in build.collect().items():
        path = os.path.join(build.OUT, name.replace("/", os.sep))
        with io.open(path, encoding="utf-8") as handle:
            if handle.read() != body:
                stale.append(name)
    assert not stale, ("docs/app is out of date; run python "
                       "tools/build_static.py: %s" % stale)


def test_no_storage_key_the_app_owns_can_be_the_theme():
    """Every page on the site shares 'theme'. The app's own keys all start
    with "tally." and nothing calls localStorage.clear(), so "Forget
    everything" and the export cannot reach it."""
    sources = [read(*APP_JS)] + [
        read("tools", "static_src", "js", name) for name in build.STATIC_JS]
    text = "\n".join(sources)
    assert "localStorage.clear" not in text and ".clear()" not in text
    keys = re.findall(r"_KEY = '([^']+)'", text)
    keys += re.findall(r"localStorage\.removeItem\('([^']+)'\)", text)
    assert len(keys) >= 4
    assert all(key.startswith("tally.") for key in keys), keys
    assert re.findall(r"localStorage\.setItem\('([^']+)'", text) == ["theme"]


# ----------------------------------------------------------- the browser

SCAN = r"""() => {
  const parse = c => { const n = c.match(/[\d.]+/g).map(Number);
                       return { rgb: n.slice(0, 3).map(v => v / 255), a: n.length > 3 ? n[3] : 1 }; };
  const lum = c => { const [r, g, b] = parse(c).rgb.map(v => v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4);
                     return .2126 * r + .7152 * g + .0722 * b; };
  const behind = el => { for (let e = el; e; e = e.parentElement) { const bg = getComputedStyle(e).backgroundColor;
                     if (/\d/.test(bg) && parse(bg).a > .5) return bg; }
                     return getComputedStyle(document.body).backgroundColor; };
  const out = [];
  let seen = 0;
  for (const el of document.querySelectorAll('body *')) {
    if (![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || !el.getClientRects().length) continue;
    if (el.closest('[hidden], noscript')) continue;
    seen++;
    const big = parseFloat(s.fontSize) >= 24 || (parseFloat(s.fontSize) >= 18.66 && +s.fontWeight >= 700);
    const [L1, L2] = [lum(s.color), lum(behind(el))].sort((a, b) => b - a);
    const r = (L1 + .05) / (L2 + .05);
    if (r < (big ? 3 : 4.5)) out.push(el.tagName + '.' + el.className + ' ' + r.toFixed(2) +
                                      ' "' + el.textContent.trim().slice(0, 24) + '"');
  }
  return { seen, short: [...new Set(out)] };
}"""

THEME = r"(document.documentElement.className.match(/(?:^|\s)theme-(\S+)/g) || []).join()"


@pytest.fixture(scope="module")
def chromium():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("Playwright is not installed (a build-time dependency, "
                    "see tools/build_static.py)")
    with sync_playwright() as pw:
        options = {}
        if os.path.exists(build.CHROME):
            options["executable_path"] = build.CHROME
        try:
            browser = pw.chromium.launch(**options)
        except Exception as bad:                       # noqa: BLE001
            pytest.skip("no browser to launch: %s" % bad)
        yield browser
        browser.close()


def url(*parts):
    return "file:///" + os.path.join(ROOT, *parts).replace("\\", "/")


def open_page(chromium, where, saved=None, javascript=True):
    context = chromium.new_context(java_script_enabled=javascript,
                                   viewport={"width": 1280, "height": 900})
    context.set_default_timeout(5000)
    page = context.new_page()
    page.errors = []
    page.on("pageerror", lambda bad: page.errors.append(str(bad)))
    page.on("console", lambda msg: page.errors.append(msg.text)
            if msg.type == "error" else None)
    if saved is not None:
        page.add_init_script("localStorage.setItem('theme', %r)" % saved)
    page.goto(url(*where))
    page.wait_for_timeout(200)
    return context, page


def dark_bg(group):
    hex_colour = theme_blocks(GROUPS[group]["css"]())["dark"]["--color-bg"]
    value = hex_colour.lstrip("#")
    return "rgb(%d, %d, %d)" % tuple(int(value[i:i + 2], 16)
                                     for i in (0, 2, 4))


PAGES = [(DEMO, "app"), (WRITEUP, "writeup")]
IDS = ["demo", "writeup"]


@pytest.mark.parametrize("where,group", PAGES, ids=IDS)
def test_with_no_javascript_the_page_is_dark(chromium, where, group):
    context, page = open_page(chromium, where, javascript=False)
    try:
        seen = page.evaluate(
            "[document.documentElement.className,"
            " getComputedStyle(document.body).backgroundColor,"
            " getComputedStyle(document.documentElement).colorScheme]")
        assert seen == ["theme-dark", dark_bg(group), "dark"]
    finally:
        context.close()


@pytest.mark.parametrize("where,group", PAGES, ids=IDS)
def test_the_toggle_switches_and_remembers(chromium, where, group):
    context, page = open_page(chromium, where)
    try:
        assert page.evaluate(THEME) == "theme-dark"
        assert page.get_attribute("#themeBtn", "aria-label") == \
            "Switch to light theme"
        page.click("#themeBtn")
        assert page.evaluate(THEME) == "theme-light"
        assert page.get_attribute("#themeBtn", "aria-label") == \
            "Switch to dark theme"
        assert page.evaluate("localStorage.getItem('theme')") == "light"
        light = theme_blocks(GROUPS[group]["css"]())["light"]["--color-bg"]
        if group == "app":
            assert page.get_attribute('meta[name="theme-color"]',
                                      "content") == light
        page.reload()
        page.wait_for_timeout(200)
        assert page.evaluate(THEME) == "theme-light"
        page.click("#themeBtn")
        assert page.evaluate(THEME) == "theme-dark"
        assert not page.errors, page.errors
    finally:
        context.close()


@pytest.mark.parametrize("where,group", PAGES, ids=IDS)
def test_an_unknown_saved_theme_falls_back_to_dark(chromium, where, group):
    context, page = open_page(chromium, where, saved="sepia")
    try:
        assert page.evaluate(THEME) == "theme-dark"
        assert page.evaluate("getComputedStyle(document.body)"
                             ".backgroundColor") == dark_bg(group)
        assert not page.errors, page.errors
    finally:
        context.close()


def record(page, digits, chip=0):
    for digit in digits:
        page.click('#keys [data-key="%s"]' % digit)
    button = page.locator("#categories button").nth(chip)
    if button.get_attribute("aria-pressed") != "true":   # a second tap unpicks
        button.click()
    page.click("#save")
    page.wait_for_timeout(150)


@pytest.mark.parametrize("saved", ["dark", "light"])
def test_every_visible_text_on_the_demo_meets_aa(chromium, saved):
    """With entries in it, a habit button, a picked chip, the notice and
    the bars all showing -- not the empty first load."""
    context, page = open_page(chromium, DEMO, saved=saved)
    try:
        assert page.evaluate(THEME) == "theme-" + saved
        for _ in range(3):
            record(page, "250")
        record(page, "1999", chip=1)
        page.click('#keys [data-key="4"]')
        page.locator("#categories button").nth(0).click()
        assert page.is_visible("#usualCard") and page.is_visible("#notice")
        result = page.evaluate(SCAN)
        assert result["seen"] > 40
        assert not result["short"], result["short"]
        assert not page.errors, page.errors
    finally:
        context.close()


@pytest.mark.parametrize("saved", ["dark", "light"])
def test_every_visible_text_on_the_writeup_meets_aa(chromium, saved):
    context, page = open_page(chromium, WRITEUP, saved=saved)
    try:
        assert page.evaluate(THEME) == "theme-" + saved
        for _ in range(2):                     # chart view, then table view
            result = page.evaluate(SCAN)
            assert result["seen"] > 40
            assert not result["short"], result["short"]
            page.click("#tableBtn")
        assert not page.errors, page.errors
    finally:
        context.close()


def test_forgetting_everything_keeps_the_theme(chromium):
    """The demo's "Forget everything" removes its own keys by name; the
    visitor's theme, shared with the rest of the site, stays."""
    context, page = open_page(chromium, DEMO)
    try:
        page.click("#themeBtn")
        record(page, "120")
        page.on("dialog", lambda dialog: dialog.accept())
        page.click("#forget")
        page.wait_for_load_state()
        page.wait_for_timeout(200)
        assert page.evaluate("TallyStore.count()") == 0
        assert page.evaluate("localStorage.getItem('theme')") == "light"
        assert page.evaluate(THEME) == "theme-light"
    finally:
        context.close()
