"""Repair a transcript against the vocabulary we actually trade in.

Speech recognition produces near-misses that are obvious to a human and
invisible to a regex: "put" comes back as "button", YESBANK as "years
bank". Rather than widening every pattern, normalise the words first, so
the parser only ever sees canonical vocabulary.

Matching is deliberately conservative - a token is only rewritten when it
is a close match AND not already a real word we know. The cost of a wrong
rewrite here is a wrong trade.
"""

import difflib
import re

# Canonical word -> the things people and recognisers actually say.
SYNONYMS = {
    # instruments
    "call": ["call", "calls", "ce", "kol", "coal", "cal", "caul"],
    "put": ["put", "puts", "pe", "button", "putt", "foot", "boot", "pull"],
    # actions - open
    "buy": ["buy", "by", "bye", "purchase", "get", "grab", "take", "long",
            "acquire", "pick"],
    # actions - close
    "exit": ["exit", "close", "square", "squareoff", "unwind", "offload",
             "dump", "flatten", "exist", "exits"],
    # actions - sell to open (kept distinct so it can be refused)
    "sell": ["sell", "short", "write", "sale"],
}

# Words that must never be rewritten, because they are legitimate and
# close to something else in the vocabulary.
PROTECTED = {
    "at", "a", "an", "the", "my", "me", "is", "it", "in", "on", "of", "for",
    "what", "how", "much", "price", "quote", "limits", "funds", "cash",
    "position", "positions", "own", "have", "point", "lot", "lots", "and",
    "balance", "money", "orders", "order", "nifty", "yesbank", "bank",
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "zero", "twenty", "thirty", "forty", "fifty", "hundred",
}

_LOOKUP = {}
for canon, variants in SYNONYMS.items():
    for v in variants:
        _LOOKUP[v] = canon

# Multi-word phrases collapsed before token matching.
PHRASES = [
    (r"\bsquare\s+off\b", "exit"),
    (r"\bget\s+out\s+of\b", "exit"),
    (r"\bget\s+rid\s+of\b", "exit"),
    (r"\bgo\s+long\b", "buy"),
    (r"\bpick\s+up\b", "buy"),
    (r"\byears?\s+bank\b", "yesbank"),
    (r"\byes\s+bank\b", "yesbank"),
    (r"\bcall\s+option\b", "call"),
    (r"\bput\s+option\b", "put"),
]


def normalise(text, cutoff=0.82):
    """Rewrite a transcript into canonical vocabulary."""
    t = " ".join(text.lower().split())
    for pattern, repl in PHRASES:
        t = re.sub(pattern, repl, t)

    out = []
    for token in t.split():
        bare = token.strip(".,!?;:")
        if not bare or bare in PROTECTED:
            out.append(token)
            continue
        if bare in _LOOKUP:
            out.append(_LOOKUP[bare])
            continue
        # Near-miss: only rewrite on a strong match.
        match = difflib.get_close_matches(bare, _LOOKUP.keys(), n=1,
                                          cutoff=cutoff)
        out.append(_LOOKUP[match[0]] if match else token)
    return " ".join(out)
