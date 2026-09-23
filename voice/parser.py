"""Turn a transcript into a structured intent.

Rule-based and offline. Deliberately conservative: anything it is not sure
about becomes an 'unknown' intent rather than a guess, because the cost of
a wrong guess here is a real trade. A Claude tool-use parser can replace
this later behind the same parse() signature.
"""

import re

from voice import numbers
from voice.aliases import canonical

WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
    "twenty five": 25, "thirty": 30, "fifty": 50, "hundred": 100,
    "a": 1, "an": 1, "couple": 2,
}

BUY_WORDS = r"buy|get|purchase|pick up|grab|long"
SELL_WORDS = r"sell|dump|offload|exit|close|short"


def _number(text):
    if text is None:
        return None
    text = text.strip().lower()
    try:
        return float(text) if "." in text else int(text)
    except ValueError:
        return WORD_NUMBERS.get(text)


def parse(transcript):
    """-> dict with 'intent' and whatever fields that intent carries."""
    t = " ".join(transcript.lower().split())
    # Whisper punctuates freely: "BUY 10 YESBANK." must not search "yesbank."
    # But a decimal point inside a price is data, not punctuation - only
    # strip marks that are NOT followed by a digit, so 23.20 survives.
    t = re.sub(r"[.!?,;:]+(?!\d)", " ", t)
    # Word-bounded, or "years bank" loses its "rs " and becomes "yeabank".
    t = re.sub(r"\b(?:rupees?|rs)\b", " ", t)
    t = " ".join(t.split())
    if not re.search(r"[a-z0-9]", t):
        return {"intent": "unknown", "transcript": transcript}
    # Strip conversational filler so it cannot end up inside a symbol name
    # ("dump my yesbank" was resolving the name as "my yesbank").
    t = re.sub(r"\b(?:me|my|some|please|shares?|stocks?)\b", " ", t)
    t = " ".join(t.split())

    if re.search(r"\b(funds?|balance|cash|money|buying power)\b", t):
        return {"intent": "funds"}
    if re.search(r"\b(positions?|holdings?|what do i (own|have)|portfolio)\b", t):
        return {"intent": "positions"}
    if re.search(r"\b(orders?|order book|pending)\b", t) and not re.search(
            rf"\b({BUY_WORDS}|{SELL_WORDS})\b", t):
        return {"intent": "orders"}
    if re.search(r"\b(limits?|caps?|safety)\b", t):
        return {"intent": "limits"}

    # "what is yesbank at" / "price of yesbank" / "yesbank quote"
    m = re.search(r"(?:price of|quote for|quote|what'?s|how much is)\s+([a-z0-9 ]+?)"
                  r"(?:\s+(?:at|trading|going|doing|now))?$", t)
    if m:
        return {"intent": "quote", "name": canonical(m.group(1).strip())}

    side = None
    if re.search(rf"\b({BUY_WORDS})\b", t):
        side = "B"
    elif re.search(rf"\b({SELL_WORDS})\b", t):
        side = "S"

    if side:
        # Token scan rather than one big regex: find the verb, pull out an
        # "at <price>" tail, take a leading number as quantity, and treat
        # whatever remains as the instrument name. Far less brittle.
        words = t.split()
        verb_at = next(i for i, w in enumerate(words)
                       if re.fullmatch(rf"{BUY_WORDS}|{SELL_WORDS}", w))
        rest = words[verb_at + 1:]

        price = None
        for i, w in enumerate(rest):
            if w in ("at", "for", "@") and i + 1 < len(rest):
                value, used = numbers.parse(rest[i + 1:])
                if value is not None:
                    price = value
                    rest = rest[:i]
                    break

        qty = None
        if rest:
            value, used = numbers.parse(rest)
            # Only take it as a quantity if something remains to name the
            # instrument - "buy yesbank" must not read "yesbank" as a number.
            if value is not None and used < len(rest):
                qty, rest = value, rest[used:]

        rest = [w for w in rest if w not in ("of", "all", "worth")]
        name = " ".join(rest).strip()
        if name:
            return {"intent": "order", "side": side, "quantity": qty,
                    "name": canonical(name), "price": price}

    return {"intent": "unknown", "transcript": transcript}
