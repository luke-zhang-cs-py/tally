"""The script that keeps the published page's figures true.

tests/test_published_figures.py is the half that *checks* docs/index.html;
tools/refresh_figures.py is the half that rewrites it. A rewriter that is
wrong is worse than none -- it either leaves a whitespace diff on every run,
so nobody runs it, or it writes a figure it never measured -- so it is held
to the same standard as the app.

Nothing here runs the suite for real or touches the repository's own page:
pytest is replaced by a recorded answer, and every file the script writes is
pointed into tmp_path first.
"""
import io
import json
import os
import sys
import tempfile
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools import refresh_figures as refresh   # noqa: E402

# A page cut down to the four lines of data the script edits, laid out the
# way the script itself writes them.
PAGE = """<script>
var MODULES = [
  { name: 'a.py',     lines:  10, stmts:   5, missed:  0, does: 'the first' },
  { name: 'pkg/b.py', lines:   3, stmts:   1, missed:  1, does: 'the second' }
];
var TESTS = [
  { file: 'test_a.py',   n:  4, of: 'the first' },
  { file: 'test_b.py',   n:  2, of: 'the second' }
];
var TEST_LINES = 7;
var MEASURED_WITH = 'Python 3.0';
</script>
"""

MEASURED = {
    "a.py": {"lines": 10, "stmts": 5, "missed": 0},
    "pkg/b.py": {"lines": 4, "stmts": 2, "missed": 0},
}
COUNTS = {"test_a.py": 4, "test_b.py": 3}

RUNNING = "%d.%d" % sys.version_info[:2]


def text_of(path):
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


def put(path, text):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with io.open(str(path), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A throwaway project root: a page, a README and a tests/ folder."""
    put(tmp_path / "docs" / "index.html", PAGE)
    put(tmp_path / "README.md",
        "# x\n\n9 tests, 50% of 4 statements -- checked.\n")
    put(tmp_path / "tests" / "test_a.py", "one\ntwo\nthree\n")
    put(tmp_path / "tests" / "sub" / "test_b.py", "one\ntwo\n")
    put(tmp_path / "tests" / "__pycache__" / "stale.py", "x\n" * 100)
    put(tmp_path / "tests" / "notes.txt", "not python\n" * 50)
    monkeypatch.setattr(refresh, "ROOT", str(tmp_path))
    monkeypatch.setattr(refresh, "PAGE", str(tmp_path / "docs" / "index.html"))
    monkeypatch.setattr(refresh, "README", str(tmp_path / "README.md"))
    return tmp_path


# --------------------------------------------------------------- files

def test_write_is_utf8_with_unix_newlines(tmp_path):
    """The page is served from Linux and edited on Windows; a CRLF write
    would turn every line into a diff."""
    path = str(tmp_path / "out.txt")
    refresh.write(path, "caf\u00e9\nline two\n")
    with open(path, "rb") as handle:
        assert handle.read() == "caf\u00e9\nline two\n".encode("utf-8")
    assert refresh.read(path) == "caf\u00e9\nline two\n"


# ---------------------------------------------------------- measuring

def test_measure_reads_the_coverage_json_and_counts_lines(project,
                                                          monkeypatch):
    """The report coverage itself wrote, so the files it lists are exactly
    the ones the terminal report prints. Paths come back forward-slashed
    whatever the platform wrote, because the page names them that way."""
    put(project / "pkg" / "b.py", "a = 1\nb = 2\nc = 3\nd = 4\n")
    real_mkstemp = tempfile.mkstemp
    monkeypatch.setattr(refresh.tempfile, "mkstemp",
                        lambda suffix: real_mkstemp(suffix=suffix,
                                                    dir=str(project)))
    seen = {}

    def run(command, **how):
        seen["command"], seen["cwd"] = command, how["cwd"]
        target = [arg for arg in command
                  if arg.startswith("--cov-report=json:")][0]
        seen["report"] = target.split(":", 1)[1]
        put(seen["report"], json.dumps({"files": {
            os.path.join("pkg", "b.py"): {"summary": {
                "num_statements": 4, "missing_lines": 1}}}}))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(refresh.subprocess, "run", run)
    got = refresh.measure()

    assert got == {"pkg/b.py": {"lines": 4, "stmts": 4, "missed": 1}}
    assert "--cov" in seen["command"] and seen["cwd"] == str(project)
    assert not os.path.exists(seen["report"]), "the JSON report was left behind"


def test_collect_counts_each_test_file(monkeypatch):
    """A parameterised test is one definition and several tests, so the
    count is from --collect-only, one line per collected test."""
    out = ("tests/test_a.py::test_one\n"
           "tests/test_a.py::test_two[1]\n"
           "tests/test_a.py::test_two[2]\n"
           "tests/test_b.py::test_x\n"
           "\n"
           "4 tests collected in 0.01s\n")
    monkeypatch.setattr(refresh.subprocess, "run", lambda *a, **k:
                        types.SimpleNamespace(stdout=out))
    assert refresh.collect() == {"test_a.py": 3, "test_b.py": 1}


def test_collect_refuses_an_empty_collection(monkeypatch):
    """A collection error prints no test ids. Writing zero for every file
    would be a page that was wrong and claimed to be measured."""
    monkeypatch.setattr(refresh.subprocess, "run", lambda *a, **k:
                        types.SimpleNamespace(stdout="ERROR collecting x\n"))
    with pytest.raises(SystemExit) as stopped:
        refresh.collect()
    assert "collection produced nothing" in str(stopped.value)
    assert "ERROR collecting x" in str(stopped.value)


# ------------------------------------------------------ the module block

def test_the_module_block_is_rewritten_largest_first():
    changes = []
    page = refresh.fix_modules(PAGE, MEASURED, changes)
    assert (
        "var MODULES = [\n"
        "  { name: 'a.py',     lines:  10, stmts:   5, missed:  0, "
        "does: 'the first' },\n"
        "  { name: 'pkg/b.py', lines:   4, stmts:   2, missed:  0, "
        "does: 'the second' }\n"
        "];") in page
    # Only the row whose figures moved is reported, old to new.
    assert len(changes) == 1
    assert changes[0].split() == ["pkg/b.py", "(3,", "1,", "1)", "->",
                                  "(4,", "2,", "0)"]
    # Everything outside the block is left exactly as it was.
    assert page.split("var TESTS")[1] == PAGE.split("var TESTS")[1]


def test_a_module_with_no_description_stops_the_rewrite():
    """Only a person knows what a new module is for; the script will not
    invent the sentence."""
    measured = dict(MEASURED, **{"new.py": {"lines": 1, "stmts": 1,
                                            "missed": 0}})
    with pytest.raises(SystemExit) as stopped:
        refresh.fix_modules(PAGE, measured, [])
    assert "no description on the page for new.py" in str(stopped.value)
    assert "MODULES block" in str(stopped.value)


def test_a_module_coverage_stopped_reporting_is_flagged_and_dropped():
    changes = []
    page = refresh.fix_modules(PAGE, {"a.py": MEASURED["a.py"]}, changes)
    assert changes == ["pkg/b.py is on the page but coverage no longer "
                       "reports it -- remove it, or check whether it became "
                       "omitted"]
    assert "pkg/b.py" not in page
    # The last row has no trailing comma, or the array is a syntax error in
    # an old browser and a lint warning in a new one.
    assert "does: 'the first' }\n];" in page


# -------------------------------------------------------- the test block

def test_the_test_block_is_rewritten_by_count_then_name():
    changes = []
    page = refresh.fix_tests(PAGE, {"test_a.py": 3, "test_b.py": 3},
                             changes)
    assert (
        "var TESTS = [\n"
        "  { file: 'test_a.py', n:  3, of: 'the first' },\n"
        "  { file: 'test_b.py', n:  3, of: 'the second' }\n"
        "];") in page
    assert [line.split() for line in changes] == [
        ["test_a.py", "4", "->", "3"], ["test_b.py", "2", "->", "3"]]


def test_a_test_file_with_no_description_stops_the_rewrite():
    with pytest.raises(SystemExit) as stopped:
        refresh.fix_tests(PAGE, dict(COUNTS, **{"test_new.py": 1}), [])
    assert "no description on the page for test_new.py" in str(stopped.value)


def test_a_test_file_that_collects_nothing_is_flagged():
    changes = []
    page = refresh.fix_tests(PAGE, {"test_a.py": 4}, changes)
    assert changes == ["test_b.py is on the page but collects nothing"]
    assert "test_b.py" not in page


# ------------------------------------------------- interpreter and lines

def test_the_interpreter_is_recorded():
    """A statement count belongs to a file and an interpreter together."""
    changes = []
    page = refresh.fix_measured_with(PAGE, changes)
    assert "var MEASURED_WITH = 'Python %s';" % RUNNING in page
    assert changes == ["MEASURED_WITH Python 3.0 -> %s" % RUNNING]

    again = []
    assert refresh.fix_measured_with(page, again) == page
    assert again == []


def test_a_page_with_no_interpreter_line_is_refused():
    with pytest.raises(SystemExit) as stopped:
        refresh.fix_measured_with("var MODULES = [];", [])
    assert "no MEASURED_WITH line" in str(stopped.value)


def test_test_lines_count_python_files_only(project):
    """test_a.py has three lines and sub/test_b.py two. The bytecode cache
    and a text file beside them are not test code."""
    changes = []
    page = refresh.fix_test_lines(PAGE, changes)
    assert "var TEST_LINES = 5;" in page
    assert changes == ["TEST_LINES 7 -> 5"]

    again = []
    assert refresh.fix_test_lines(page, again) == page
    assert again == []


def test_a_page_with_no_test_lines_figure_is_left_alone(project):
    changes = []
    assert refresh.fix_test_lines("nothing here", changes) == "nothing here"
    assert changes == []


# -------------------------------------------------------------- README

def test_the_readme_sentence_is_rewritten(project):
    changes = []
    refresh.fix_readme({"a.py": {"lines": 1, "stmts": 1200, "missed": 0}},
                       {"test_a.py": 1000, "test_b.py": 234}, changes)
    assert text_of(project / "README.md") == (
        "# x\n\n1,234 tests, 100% of 1,200 statements -- checked.\n")
    assert changes == ["README  9 tests, 50% of 4 statements -> "
                       "1,234 tests, 100% of 1,200 statements"]


def test_a_readme_already_right_is_not_touched(project):
    """9 tests and 2 of 4 statements covered is what it already says."""
    readme = str(project / "README.md")
    stamp = os.path.getmtime(readme) - 100
    os.utime(readme, (stamp, stamp))
    changes = []
    refresh.fix_readme({"a.py": {"lines": 1, "stmts": 4, "missed": 2}},
                       {"test_a.py": 5, "test_b.py": 4}, changes)
    assert changes == []
    assert os.path.getmtime(readme) == stamp, (
        "a README that was already right was written anyway")


def test_a_readme_without_the_sentence_or_at_all_is_skipped(project):
    put(project / "README.md", "# no figures here\n")
    refresh.fix_readme(MEASURED, COUNTS, [])
    assert text_of(project / "README.md") == "# no figures here\n"

    os.remove(str(project / "README.md"))
    changes = []
    refresh.fix_readme(MEASURED, COUNTS, changes)
    assert changes == []
    assert not os.path.exists(str(project / "README.md"))


# ---------------------------------------------------------------- main

@pytest.fixture
def measured(monkeypatch):
    monkeypatch.setattr(refresh, "measure", lambda: MEASURED)
    monkeypatch.setattr(refresh, "collect", lambda: COUNTS)


def test_main_rewrites_the_page_and_says_what_changed(project, measured,
                                                      capsys):
    refresh.main()
    page = text_of(project / "docs" / "index.html")
    assert "{ name: 'pkg/b.py', lines:   4, stmts:   2, missed:  0," in page
    assert "{ file: 'test_b.py', n:  3, of: 'the second' }" in page
    assert "var TEST_LINES = 5;" in page
    assert "var MEASURED_WITH = 'Python %s';" % RUNNING in page
    assert "7 tests, 100% of 7 statements" in text_of(project / "README.md")

    printed = capsys.readouterr().out.splitlines()
    assert printed[-1] == "  7 tests, 7 statements, 100% covered"
    assert any("pkg/b.py" in line for line in printed[:-1])
    assert any(line.startswith("  README") for line in printed)


def test_a_second_run_prints_only_the_totals(project, measured, capsys):
    """The documented promise: a run that prints only the totals means the
    page was already right."""
    refresh.main()
    first = text_of(project / "docs" / "index.html")
    capsys.readouterr()

    refresh.main()
    assert text_of(project / "docs" / "index.html") == first
    assert capsys.readouterr().out == "  7 tests, 7 statements, 100% covered\n"


def test_a_rewrite_that_is_not_stable_writes_nothing(project, measured,
                                                     monkeypatch):
    """A script that changes its own output on a second pass would leave a
    diff behind on every run, so it stops before writing."""
    passes = []

    def drifting(page, *_):
        passes.append(page)
        return page + " " * len(passes)

    monkeypatch.setattr(refresh, "rewrite", drifting)
    with pytest.raises(SystemExit) as stopped:
        refresh.main()
    assert "not idempotent" in str(stopped.value)
    assert text_of(project / "docs" / "index.html") == PAGE


# ------------------------------------------------- against the real page

def test_the_real_page_is_already_in_the_scripts_own_layout():
    """Read-only. Rewriting docs/index.html with the figures it already
    states must give back the same text, byte for byte -- otherwise the
    first refresh after a hand edit is a whitespace diff over every row."""
    page = text_of(os.path.join(ROOT, "docs", "index.html"))
    import re
    measured = {name: {"lines": int(a), "stmts": int(b), "missed": int(c)}
                for name, a, b, c in re.findall(
                    r"\{ name: '([\w./]+)',\s*lines:\s*(\d+),\s*stmts:\s*"
                    r"(\d+),\s*missed:\s*(\d+),", page)}
    counts = {name: int(n) for name, n in re.findall(
        r"\{ file: '([\w.]+)',\s*n:\s*(\d+),", page)}
    assert measured and counts

    changes = []
    again = refresh.fix_tests(refresh.fix_modules(page, measured, changes),
                              counts, changes)
    assert changes == []
    assert again == page
