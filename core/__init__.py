"""Where the data lives, and the table it lives in.

Nothing in here knows what an expense means -- that is domain/. These two
modules answer "which directory" and "which row", and every other module
asks them rather than deciding for itself.
"""
