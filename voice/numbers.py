"""Spoken numbers -> values.

"twenty three point two zero" -> 23.20
"a hundred and five"          -> 105
"twenty five"                 -> 25

Speaking prices digit-by-digit after "point" is far more reliable than
hoping the ASR writes "23.20" rather than "23 20", so this is the path
worth supporting well.
"""

ONES = {"zero": 0, "oh": 0, "o": 0, "one": 1, "two": 2, "three": 3, "four": 4,
        "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}
TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
         "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
         "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50,
        "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
SCALES = {"hundred": 100, "thousand": 1000, "lakh": 100000, "lac": 100000}
ARTICLES = {"a", "an"}

WORDS = set(ONES) | set(TEENS) | set(TENS) | set(SCALES) | ARTICLES


def _digits_after_point(tokens):
    """Read '. two zero' style decimal digits. Returns (text, consumed)."""
    out, used = [], 0
    for tok in tokens:
        if tok in ONES:
            out.append(str(ONES[tok]))
            used += 1
        elif tok in TEENS and not out:
            # "point fifteen" -> .15, which people do say
            out.append(str(TEENS[tok]))
            used += 1
        elif tok in TENS and not out:
            out.append(str(TENS[tok]))
            used += 1
        else:
            break
    return "".join(out), used


def parse(tokens):
    """Read a number from the front of `tokens`.

    Returns (value, tokens_consumed). value is None if it doesn't start
    with something numeric.
    """
    if not tokens:
        return None, 0

    # A plain digit string wins outright: "23.22", "10".
    first = tokens[0]
    try:
        value = float(first) if "." in first else int(first)
        # "23 point 2" - digits then a spoken decimal.
        if len(tokens) > 2 and tokens[1] == "point":
            frac, used = _digits_after_point(tokens[2:])
            if frac:
                return float(f"{int(value)}.{frac}"), 2 + used
        return value, 1
    except ValueError:
        pass

    if first not in WORDS:
        return None, 0

    total, current, used, saw_number = 0, 0, 0, False
    for tok in tokens:
        if tok in ARTICLES:
            if saw_number:
                break
            used += 1
            continue
        if tok == "and" and saw_number:
            used += 1
            continue
        if tok in ONES:
            current += ONES[tok]
        elif tok in TEENS:
            current += TEENS[tok]
        elif tok in TENS:
            current += TENS[tok]
        elif tok in SCALES:
            scale = SCALES[tok]
            if scale >= 1000:
                total += max(current, 1) * scale
                current = 0
            else:
                current = max(current, 1) * scale
        else:
            break
        saw_number = True
        used += 1

    if not saw_number:
        return None, 0

    value = total + current
    # "...point two zero"
    if used < len(tokens) and tokens[used] == "point":
        frac, extra = _digits_after_point(tokens[used + 1:])
        if frac:
            return float(f"{value}.{frac}"), used + 1 + extra

    return value, used
