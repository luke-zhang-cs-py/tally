"""Where the database lives.

Small, and here because getting it wrong is invisible: two modules that each
work out the data directory for themselves are not forced to agree, and the
result is a ledger read from one folder while something else writes to
another, with neither looking wrong.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import paths   # noqa: E402


def test_an_explicit_directory_wins(tmp_path, monkeypatch):
    """A caller that knows where it wants to work is never overridden by an
    environment variable it did not set -- which is what makes the test
    fixtures in this suite reliable."""
    monkeypatch.setenv(paths.ENV_VAR, str(tmp_path / "from-the-env"))
    assert paths.data_dir(str(tmp_path / "asked-for")) == \
        str(tmp_path / "asked-for")


def test_the_environment_is_next(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_VAR, str(tmp_path))
    assert paths.data_dir() == str(tmp_path)


def test_the_default_is_data_beside_the_code(monkeypatch):
    """What a person who just clones this and runs it gets, so it is worth
    stating: `data/` next to the modules, which is the path .gitignore
    excludes. Every other test in this suite sets TALLY_DATA, so without
    clearing it here this line is never executed by anything.
    """
    monkeypatch.delenv(paths.ENV_VAR, raising=False)
    got = paths.data_dir()
    assert os.path.basename(got) == paths.DEFAULT_DIRNAME
    assert os.path.dirname(got) == os.path.dirname(
        os.path.abspath(paths.__file__))
    assert os.path.isabs(got), "a relative path depends on the working " \
        "directory, so the app would use a different folder per launch"


@pytest.mark.parametrize("blank", ["", None])
def test_a_blank_environment_variable_falls_through(blank, monkeypatch):
    """An exported-but-empty TALLY_DATA is a real shape -- `TALLY_DATA=` in a
    shell -- and joining "" gives the working directory."""
    if blank is None:
        monkeypatch.delenv(paths.ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(paths.ENV_VAR, blank)
    assert os.path.basename(paths.data_dir()) == paths.DEFAULT_DIRNAME
