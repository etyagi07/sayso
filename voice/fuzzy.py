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
    # Not "coal" - Coal India is a stock.
    "call": ["call", "calls", "ce", "kol", "cal", "caul"],
    "put": ["put", "puts", "pe", "button", "putt", "foot", "boot", "pull"],
    # actions - open. Not "get", "take" or "long": "take profit" and "get
    # me out" are exits, and reading them as buy opens a second position.
    # "by" and "bye" are only buy as the first word - see normalise().
    "buy": ["buy", "purchase", "grab", "acquire"],
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
    "banknifty", "sensex", "finnifty", "sensex50", "niftynext50",
    "midcpnifty", "bankex", "index", "which", "niftybees", "longterm",
    "intraday", "delivery",
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "zero", "twenty", "thirty", "forty", "fifty", "hundred",
}

_LOOKUP = {}
for canon, variants in SYNONYMS.items():
    for v in variants:
        _LOOKUP[v] = canon

# Multi-word phrases collapsed before token matching. Order matters:
# unsupported indices are collapsed to their own token FIRST, so that
# "fin nifty" or "sensex fifty" can never be read as NIFTY or SENSEX.
PHRASES = [
    # "long term" is holding period, not "go long" - collapse it before
    # "long" can be read as buy.
    (r"\blong\s*term\b", "longterm"),
    (r"\bnifty\s+next\s+(?:fifty|50)\b", "niftynext50"),
    (r"\bfin\s*nifty\b", "finnifty"),
    (r"\bmid\s*(?:cap|cp)\s*nifty\b", "midcpnifty"),
    (r"\bsensex\s*(?:fifty|50)\b", "sensex50"),
    (r"\bbank\s*ex\b", "bankex"),
    # NIFTYBEES is an equity ETF, not the index - collapse it before
    # "nifty" can be picked out on its own.
    (r"\bnifty\s*bees?\b", "niftybees"),
    # Supported indices, in the forms speech recognition produces.
    (r"\bbank\s*nifty(?:'s)?\b", "banknifty"),
    (r"\bnifty\s+bank\b", "banknifty"),
    (r"\bnifty\s+(?:fifty|50)\b", "nifty"),
    (r"\bsense\s*x\b", "sensex"),
    (r"\bsensex'?s\b", "sensex"),
    (r"\bcensus\b", "sensex"),
    (r"\bsquare\s+off\b", "exit"),
    (r"\bget\s+(?:me\s+)?out(?:\s+of)?\b", "exit"),
    (r"\bget\s+rid\s+of\b", "exit"),
    (r"\b(?:take|book)\s+(?:my\s+|the\s+|some\s+)?profits?\b", "exit"),
    (r"\bthank\s+you\b", "thanks"),
    (r"\bgo\s+long\b", "buy"),
    (r"\bpick\s+up\b", "buy"),
    (r"\byears?\s+bank\b", "yesbank"),
    (r"\byes\s+bank\b", "yesbank"),
    (r"\bcall\s+option\b", "call"),
    (r"\bput\s+option\b", "put"),
]


# Said before a command without being part of it.
LEADING_FILLER = {"ok", "okay", "so", "um", "uh", "hey", "alright", "right",
                  "well", "hmm", "now", "and", "please"}
# Whisper ends short clips with a sign-off nobody said - and "bye" read as
# "buy" turned "sell 10 infosys. bye." into a buy.
SIGN_OFFS = {"bye", "by", "goodbye", "thanks"}


def _stock_words():
    """Every word in a known company name - never rewritten."""
    from voice import stocks
    words = set()
    for row in stocks.all_stocks().values():
        for name in row["names"]:
            words.update(name.split())
    return words


def normalise(text, cutoff=0.82):
    """Rewrite a transcript into canonical vocabulary."""
    t = " ".join(text.lower().split())
    for pattern, repl in PHRASES:
        t = re.sub(pattern, repl, t)

    tokens = t.split()
    while tokens and tokens[-1].strip(".,!?;:") in SIGN_OFFS:
        tokens.pop()
    # "by 10 yes bank" is buy - but only as the opening word, where no
    # other reading makes sense.
    first = next((i for i, tok in enumerate(tokens)
                  if tok.strip(".,!?;:") not in LEADING_FILLER), None)
    if first is not None and tokens[first].strip(".,!?;:") in ("by", "bye"):
        tokens[first] = "buy"

    keep = PROTECTED | _stock_words()
    out = []
    for token in tokens:
        bare = token.strip(".,!?;:")
        if not bare or bare in keep:
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
