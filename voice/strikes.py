"""Read spoken strike prices.

Traders' shorthand does not follow number grammar: "twenty three fifty"
means 23,050, not 2,350, and "twenty three one hundred" means 23,100, not
2,400. Rather than guess which convention was used, generate every
plausible reading and let the live strike ladder decide - near spot,
strikes are sparse (multiples of 50 within a few hundred points), so
wrong readings simply do not exist as instruments.

This module is pure: it proposes candidates. Resolution against the real
ladder happens where market data is available.
"""

from voice import numbers

NUM_TOKENS = (set(numbers.ONES) | set(numbers.TEENS) | set(numbers.TENS)
              | set(numbers.SCALES) | {"and"})


def _is_numeric(token):
    if token in NUM_TOKENS:
        return True
    return token.replace(".", "", 1).isdigit()


_DIGITISH = set(numbers.ONES) | set(numbers.TEENS)


def _additive(part):
    """Can this run be read by adding its words up, as numbers.parse does?

    "twenty three" and "one hundred and five" can. "zero five" and "two
    three" cannot - they are digits read out one at a time, and summing
    them invents a number: "twenty three zero five zero" became 23,500.
    """
    zeros = {"zero", "oh", "o"}
    if len(part) > 1 and any(w in zeros for w in part):
        return False
    for prev, word in zip(part, part[1:]):
        if prev in _DIGITISH and (word in _DIGITISH or word in numbers.TENS):
            return False
        if prev in numbers.TENS and word in numbers.TENS:
            return False
    return True


def _read(part):
    """numbers.parse, only when the whole run reads as one added-up number."""
    if not _additive(part):
        return None
    value, used = numbers.parse(part)
    if value is None or used != len(part):
        return None
    return value


def _digits(part):
    """"zero five zero" -> "050"; None unless every word is a single digit."""
    out = []
    for tok in part:
        if tok.isdigit():
            out.append(tok)
        elif tok in numbers.ONES:
            out.append(str(numbers.ONES[tok]))
        else:
            return None
    return "".join(out)


def number_spans(tokens):
    """Maximal runs of number words/digits, as (start, end, tokens)."""
    spans, i = [], 0
    while i < len(tokens):
        if not _is_numeric(tokens[i]):
            i += 1
            continue
        j = i
        while j < len(tokens) and _is_numeric(tokens[j]):
            j += 1
        # A trailing "and" belongs to prose, not the number.
        run = tokens[i:j]
        while run and run[-1] == "and":
            run = run[:-1]
            j -= 1
        if run:
            spans.append((i, j, run))
        # Always move forward. A run that was only "and" trims back to where
        # it started, and without this the loop never ends - which froze the
        # app on "L and T", "M and M" or "buy call and put".
        i = max(j, i + 1)
    return spans


def candidates(span):
    """Every plausible integer reading of one span of number words.

    Over-generates on purpose - the ladder filter is strong enough that a
    wrong candidate cannot survive, and a missing candidate means a strike
    the user cannot say.
    """
    out = set()

    # Whole-span literal reading: "twenty three fifty" -> 2350
    value = _read(span)
    if value is not None and float(value).is_integer():
        out.add(int(value))

    # Digit-by-digit: "two three five zero" -> "2350". People read
    # strikes out as digits, and the thousands/tens split of that string
    # is what they mean - 23|50 is 23050.
    joined = _digits(span)
    if joined:
        if joined.isdigit():
            out.add(int(joined))
            for cut in range(1, len(joined)):
                head, tail = joined[:cut], joined[cut:]
                if len(tail) <= 3:
                    out.add(int(head) * 1000 + int(tail))

    # Digits then a scale: "seven four thousand" is 74 thousand, not the
    # 7 + 4 = 11 thousand a number parser makes of it. Same for "five five
    # six hundred" (556 hundred). The ladder throws out anything absurd.
    if len(span) >= 2 and span[-1] in numbers.SCALES:
        head = span[:-1]
        if all(w in numbers.ONES or w.isdigit() for w in head):
            joined = "".join(w if w.isdigit() else str(numbers.ONES[w])
                             for w in head)
            out.add(int(joined) * numbers.SCALES[span[-1]])

    # Split into parts and recombine the way traders actually speak.
    for cut in range(1, len(span)):
        left, right = span[:cut], span[cut:]
        a = _read(left)
        # A number, then digits read out: "twenty three | zero five zero"
        # is 23050, "twenty two | nine five zero" is 22950.
        tail = _digits(right)
        if a is not None and float(a).is_integer() and tail:
            out.add(int(f"{int(a)}{tail}"))

        # Three-part forms: "twenty two | nine | fifty" -> 22950. The
        # middle is one digit word and the tail below 100 - or "one
        # hundred" splits into 1 and 100 and invents a strike, and "zero
        # five" summed to 5 made "twenty three zero five zero" 23,500.
        if (a is not None and float(a).is_integer() and len(right) >= 2
                and right[0] in numbers.ONES):
            m, n = numbers.ONES[right[0]], _read(right[1:])
            if (n is not None and float(n).is_integer()
                    and 1 <= m <= 9 and int(n) < 100):
                out.add(int(a) * 1000 + m * 100 + int(n))
        b = _read(right)
        if a is None or b is None:
            continue
        if not (float(a).is_integer() and float(b).is_integer()):
            continue
        a, b = int(a), int(b)
        # Each rule only makes sense while the right-hand part is small
        # enough to be the tail of one number. Without these guards,
        # "two | 23100" yields 2*100+23100 = 23300 - a real strike near
        # spot, so it validates cleanly and is silently wrong.
        if b < 1000:
            out.add(a * 1000 + b)      # "twenty three | fifty"  -> 23050
        if b < 100:
            out.add(a * 100 + b)       # "two thirty | five"     -> 235
        if b < 1000:
            digits = f"{a}{b:02d}" if b < 100 else f"{a}{b}"
            if digits.isdigit():
                out.add(int(digits))   # "twenty three | one hundred" -> 23100

        # A bare scale on the right - "twenty three | hundred" - is the
        # thousands form with nothing after it.
        if len(right) == 1 and right[0] in numbers.SCALES:
            out.add(a * 1000)


    return {v for v in out if v > 0}


def resolve(span, ladder, spot, band=600):
    """Pick the one listed strike this span can mean.

    Returns (strike, reason). strike is None when nothing fits or when the
    reading is genuinely ambiguous - never a guess between two real
    strikes, because both would be tradeable and only one was meant.
    """
    possible = candidates(span)
    fits = sorted(v for v in possible
                  if v in ladder and abs(v - spot) <= band)
    if len(fits) == 1:
        return fits[0], None
    if not fits:
        return None, (f"I heard {' '.join(span)}, which isn't a listed strike "
                      f"near {spot:,.0f}.")
    return None, (f"{' '.join(span)} could be "
                  + " or ".join(f"{v:,}" for v in fits)
                  + ". Say the full number.")


def read_order(spans, ladder, spot, band=600, max_lots=999):
    """Work out quantity and strike from the numbers in an utterance.

    "buy 2 lots of 23100 call" gives two spans; "buy two twenty three one
    hundred calls" gives one merged run. Both have to yield lots=2,
    strike=23100. A number that resolves to a listed strike near spot is
    the strike; a small leftover is the quantity.

    `max_lots` here is only about what could plausibly be a quantity, not
    what is permitted - the safety layer enforces the cap, and it can say
    "2 lots exceeds the cap" far more clearly than this can.

    Returns (lots, strike, error). error is set only when something was
    said that cannot be read safely.
    """
    lots = strike = None

    for span in spans or []:
        found, why = resolve(span, ladder, spot, band)
        if found is not None:
            if strike is not None and strike != found:
                return None, None, (f"I heard two strikes, {strike:,} and "
                                    f"{found:,}. Say one.")
            strike = found
            continue

        # A whole-span read failed. Try quantity-then-strike BEFORE
        # reading the whole run as a quantity: "three twenty two nine
        # fifty" sums to 84, which looks like a plausible lot count and
        # would swallow the strike entirely.
        splits = set()
        for cut in range(1, len(span)):
            head, tail = span[:cut], span[cut:]
            qty = _read(head)
            found, _ = resolve(tail, ladder, spot, band)
            if (qty is not None and found is not None
                    and float(qty).is_integer() and 1 <= qty <= max_lots):
                splits.add((int(qty), found))

        if len(splits) == 1:
            qty, found = splits.pop()
            if lots is not None and lots != qty:
                return None, None, "I heard two quantities. Say one."
            if strike is not None and strike != found:
                return None, None, "I heard two strikes. Say one."
            lots, strike = qty, found
            continue
        if len(splits) > 1:
            return None, None, (f"I couldn't tell the quantity from the "
                                f"strike in \"{' '.join(span)}\". "
                                f"Try saying them separately.")

        # No strike in it - read the whole run as a quantity, but only if
        # it is short. A quantity is said in a word or two ("two", "ten");
        # a long run like "two three five zero" is someone reading a
        # strike out digit by digit, and summing it to 10 lots would build
        # a huge order from a strike that simply was not recognised.
        value = _read(span)
        if (len(span) <= 2 and value is not None
                and float(value).is_integer() and 1 <= value <= max_lots):
            if lots is not None:
                return None, None, "I heard two quantities. Say one."
            lots = int(value)
            continue

        return None, None, why

    return lots, strike, None
