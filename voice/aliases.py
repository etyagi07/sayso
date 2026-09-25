"""Tidy a spoken name before it is matched.

Company names and their spoken forms live in voice.stocks, which is the one
place they are matched. This only strips what speech adds around them.
"""


def canonical(name):
    """Lowercase, trimmed, whitespace collapsed. Unknown names pass through."""
    if not name:
        return name
    return " ".join(name.lower().split()).strip(" .!?,-")
