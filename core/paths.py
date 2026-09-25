"""
paths.py
--------
Where this app keeps its data. One decision, in one place.

Its own module for the same reason the wallet app has one: two modules that
each resolve the data directory for themselves are not forced to agree, and
the failure is invisible -- a ledger read from one folder while something else
writes to another, with neither looking wrong.

Resolved per call, never captured at import, so a test can point the whole app
at a temporary directory and have every module follow.
"""
import os

ENV_VAR = "TALLY_DATA"
DEFAULT_DIRNAME = "data"


def data_dir(directory=None):
    """The directory holding the database.

    An explicit argument first, then TALLY_DATA, then `data/` beside the code.
    The argument wins so a caller that knows where it wants to work is never
    overridden by an environment variable it did not set.
    """
    if directory:
        return directory
    # Two dirnames, not one: this module lives in core/, and the data
    # directory belongs beside the project rather than inside a package.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.environ.get(ENV_VAR) or os.path.join(root, DEFAULT_DIRNAME)
