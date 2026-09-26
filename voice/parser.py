"""Turn a transcript into a structured intent.

Rule-based and offline. Deliberately conservative: anything it is not sure
about becomes an 'unknown' intent rather than a guess, because the cost of
a wrong guess here is a real trade. A Claude tool-use parser can replace
this later behind the same parse() signature.
"""

import re

from voice import numbers, strikes
from voice.aliases import canonical
from voice.fuzzy import normalise

WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
    "twenty five": 25, "thirty": 30, "fifty": 50, "hundred": 100,
    "a": 1, "an": 1, "couple": 2,
}

BUY_WORDS = r"buy|purchase|pick up|grab"
SELL_WORDS = r"sell|dump|offload|exit|close|short"
# Closing an existing position, as distinct from selling to open.
EXIT_WORDS = r"exit|close|square off|squareoff|square|unwind|get out of"
OPTION_WORDS = {"call": "CE", "calls": "CE", "ce": "CE",
                "put": "PE", "puts": "PE", "pe": "PE"}

# Index tokens, as produced by voice.fuzzy.normalise.
INDEX_WORDS = {"nifty": "NIFTY", "banknifty": "BANKNIFTY", "sensex": "SENSEX"}
UNSUPPORTED_WORDS = {"finnifty": "FINNIFTY", "sensex50": "SENSEX50",
                     "niftynext50": "NIFTYNXT50", "midcpnifty": "MIDCPNIFTY",
                     "bankex": "BANKEX"}
# Intraday or delivery, however it is said.
PRODUCT_WORDS = {"intraday": "I", "intra": "I", "mis": "I",
                 "delivery": "C", "cnc": "C", "carry": "C", "positional": "C",
                 "longterm": "C"}

# Words that can pad a one-word answer: "bank nifty please", "the sensex one".
ANSWER_FILLER = {"the", "a", "one", "please", "index", "it", "is", "its",
                 "ok", "okay", "yes", "that", "for"}


def _numeric_word(w):
    """A number word or digits - but not "and", which names use (M and M)."""
    return w != "and" and strikes._is_numeric(w)


def _number(text):
    if text is None:
        return None
    text = text.strip().lower()
    try:
        return float(text) if "." in text else int(text)
    except ValueError:
        return WORD_NUMBERS.get(text)


# Said to call something off, not to do it.
CANCEL = re.compile(r"^(?:cancel|cancel that|never ?mind|stop|forget it|"
                    r"scratch that|abort)$")
# Hypotheticals and advice-seeking. "Should I buy a call" is a question,
# and answering it with an order preview is the wrong kind of helpful.
QUESTION = re.compile(r"^(?:(?:ok|okay|so|um|uh|hey|alright|well|hmm|and)\s+)*"
                      r"(?:should|would|could|what if|what was|what were|"
                      r"do you think|is it a good|is now|why|when should)\b")
# The same, anywhere in the sentence: "what call should I buy", "how many
# can I buy". Asking whether to trade is never an instruction to.
QUESTION_ANYWHERE = re.compile(r"\b(?:(?:should|shall|can|could|would|may|"
                               r"might) (?:i|we)|do you think|what if|"
                               r"is it a good|is now a good)\b")
# The speaker changing their mind mid-sentence. The last one wins.
CORRECTION = re.compile(r"\b(?:no wait|no no|wait|sorry|i mean|actually|"
                        r"make that|make it|change that to|no)\b")
# "a call instead of a put" names the thing NOT wanted after the marker, so
# it is dropped rather than read as a correction to it.
NOT_THIS = re.compile(r"\b(?:instead of|rather than)\s+(?:an?\s+|the\s+)?\S+")
NEGATION = {"don't", "dont", "not", "never", "no"}
PRICE_WORDS = ("at", "@")


def _slots(text):
    """Option type, index and numbers mentioned in a fragment of speech."""
    words = text.split()
    opts = [OPTION_WORDS[w] for w in words if w in OPTION_WORDS]
    index = next((INDEX_WORDS[w] for w in words if w in INDEX_WORDS), None)
    spans = [span for _, _, span in strikes.number_spans(words)]
    return (opts[-1] if opts else None), index, spans


def _is_verb(word):
    return bool(re.fullmatch(rf"{BUY_WORDS}|{SELL_WORDS}|{EXIT_WORDS}", word))


# What a correction can be merged from - anything else in it means the
# speaker changed more than a detail, and a merge would be a guess.
SLOT_ONLY = {"unknown", "index_answer", "number_answer", "option_quote"}


def parse(transcript):
    """-> dict with 'intent' and whatever fields that intent carries.

    Handles how people actually talk before reading the command itself:
    calling it off, asking rather than telling, negating, and correcting
    themselves partway through.
    """
    # Curly apostrophes ("Don’t") and digit grouping ("23,000") are how
    # recognisers write; the rules below expect neither.
    t = transcript.replace("\u2019", "'").replace("\u2018", "'")
    t = re.sub(r"(?<=\d),(?=\d)", "", t).replace("\u20b9", " ")
    t = normalise(t)
    t = re.sub(r"[.!?,;:]+(?!\d)", " ", t)
    t = " ".join(t.split())

    if CANCEL.match(t):
        return {"intent": "cancel"}
    if re.search(r"\bcancel\b", t) and re.search(r"\border", t):
        return {"intent": "cancel_order_unsupported"}
    if QUESTION.match(t) or QUESTION_ANYWHERE.search(t):
        return {"intent": "question", "transcript": transcript}

    t = NOT_THIS.sub(" ", t)
    t = " ".join(w for w in t.split() if w not in ("instead", "rather"))

    words = t.split()
    verbs = [i for i, w in enumerate(words) if _is_verb(w)]

    # A correction: "buy call no wait put". Everything after the last
    # correction word overrides what came before it - but only a detail
    # (call or put, index, numbers) is merged in. A new side, a negation,
    # or anything else unclear stops the order: a wrong merge is a trade
    # nobody asked for.
    marks = [m for m in CORRECTION.finditer(t)
             if verbs and m.start() > len(" ".join(words[:verbs[0]]))]
    if marks:
        last = marks[-1]
        before, after = t[:last.start()].strip(), t[last.end():].strip()
        if not after:
            # "buy a nifty call... no." - trailing doubt is not a go-ahead.
            return {"intent": "unclear_correction", "transcript": transcript}
        if CANCEL.match(after):
            return {"intent": "cancel"}
        after_parsed = _parse_one(after)
        if after_parsed["intent"] not in SLOT_ONLY:
            return after_parsed          # a whole new command
        after_words = after.split()
        base = _parse_one(before) if before else after_parsed
        opt, index, spans = _slots(after)
        if (any(_is_verb(w) or w in NEGATION for w in after_words)
                or not base["intent"].startswith("option_")
                or not (opt or index or spans)):
            return {"intent": "unclear_correction", "transcript": transcript}
        if opt:
            base["option_type"] = opt
        if index:
            base["underlying"] = index
        if spans:
            base["number_spans"] = spans
            base["price_spans"] = []
        base["corrected"] = True
        return base

    # "don't buy a call" - a negated action is not an instruction.
    if verbs and any(w in NEGATION for w in words[:verbs[0]]):
        return {"intent": "negated", "transcript": transcript}

    # "buy a call not a put": drop option words that were negated; if both
    # kinds still remain, it is genuinely unclear which was meant.
    kinds = {OPTION_WORDS[w] for i, w in enumerate(words)
             if w in OPTION_WORDS
             and not (i > 0 and words[i - 1] in NEGATION)
             and not (i > 1 and words[i - 2] in NEGATION)}
    if len(kinds) > 1:
        return {"intent": "option_ambiguous", "transcript": transcript}
    if len(kinds) == 1 and any(w in NEGATION for w in words):
        keep = kinds.pop()
        cleaned = [w for i, w in enumerate(words)
                   if not (w in OPTION_WORDS and OPTION_WORDS[w] != keep)]
        t = " ".join(cleaned)

    return _parse_one(t)


def _parse_one(transcript):
    """Read one command, with no negation or correction left in it."""
    # Repair recogniser near-misses and collapse synonyms before any
    # pattern matching, so the rules below only see canonical vocabulary.
    t = normalise(transcript)
    # Whisper punctuates freely: "BUY 10 YESBANK." must not search "yesbank."
    # But a decimal point inside a price is data, not punctuation - only
    # strip marks that are NOT followed by a digit, so 23.20 survives.
    t = re.sub(r"[.!?,;:]+(?!\d)", " ", t)
    # Word-bounded, or "years bank" loses its "rs " and becomes "yeabank".
    t = re.sub(r"\b(?:rupees?|rs)\b", " ", t)
    t = " ".join(t.split())
    if not re.search(r"[a-z0-9]", t):
        return {"intent": "unknown", "transcript": transcript}

    # --- one-word answers to a question the agent asked --------------------
    tokens = [w for w in t.split() if w not in ANSWER_FILLER]
    # Whisper often repeats a short answer - "Sensex. Sensex." - so an
    # answer is one distinct word, however many times it came through.
    distinct = set(tokens)
    if len(distinct) == 1 and tokens[0] in INDEX_WORDS:
        return {"intent": "index_answer", "underlying": INDEX_WORDS[tokens[0]]}
    if len(distinct) == 1 and tokens[0] in PRODUCT_WORDS:
        return {"intent": "product_answer", "product": PRODUCT_WORDS[tokens[0]]}
    # "one" is both padding ("the nifty one") and a digit ("two three one
    # zero zero"), so number answers keep every word that reads as a number.
    tokens = [w for w in t.split()
              if w not in ANSWER_FILLER or strikes._is_numeric(w)]
    if tokens and all(strikes._is_numeric(w) for w in tokens):
        return {"intent": "number_answer",
                "number_spans": [span for _, _, span
                                 in strikes.number_spans(tokens)]}
    # Strip conversational filler so it cannot end up inside a symbol name
    # ("dump my yesbank" was resolving the name as "my yesbank").
    t = re.sub(r"\b(?:me|my|some|please|shares?|stocks?)\b", " ", t)
    t = " ".join(t.split())

    # A number that could not be read must stop the command, not vanish:
    # a dropped strike silently becomes the at-the-money one.
    from voice.fuzzy import _stock_words
    odd = next((w for w in t.split() if re.search(r"\d", w)
                and not strikes._is_numeric(w)
                and w not in _stock_words() and w not in UNSUPPORTED_WORDS),
               None)
    if odd:
        return {"intent": "number_unclear", "heard": odd}

    if re.search(r"\b(funds?|balance|cash|money|buying power)\b", t):
        return {"intent": "funds"}
    if re.search(r"\b(positions?|holdings?|what do i (own|have)|portfolio)\b", t):
        return {"intent": "positions"}
    if re.search(r"\b(orders?|order book|pending)\b", t) and not re.search(
            rf"\b({BUY_WORDS}|{SELL_WORDS})\b", t):
        return {"intent": "orders"}
    if re.search(r"\b(limits?|caps?|safety)\b", t):
        return {"intent": "limits"}

    # --- options: "buy call", "exit put" ---------------------------------
    words_all = t.split()
    unsupported = next((UNSUPPORTED_WORDS[w] for w in words_all
                        if w in UNSUPPORTED_WORDS), None)
    if unsupported:
        return {"intent": "index_unsupported", "index": unsupported}
    named = {INDEX_WORDS[w] for w in words_all if w in INDEX_WORDS}
    if len(named) > 1:
        return {"intent": "index_ambiguous", "indices": sorted(named)}
    underlying = next(iter(named), None)
    # Numbers anywhere in the command; strike and quantity are told apart
    # later, against the live strike ladder. A number after "at" is a
    # price - never a lot count - so it is kept apart: "buy call at 85"
    # read as 85 lots is the worst reading there is.
    spans, price_spans = [], []
    for start, _, span in strikes.number_spans(words_all):
        if start > 0 and words_all[start - 1] in PRICE_WORDS:
            price_spans.append(span)
        else:
            spans.append(span)

    is_buy = bool(re.search(rf"\b({BUY_WORDS})\b", t))
    is_sell = bool(re.search(rf"\b({SELL_WORDS}|{EXIT_WORDS}|write)\b", t))
    if is_buy and is_sell:
        # "sell 10 infosys ... buy" - two sides in one breath. Ask.
        return {"intent": "side_ambiguous", "transcript": transcript}

    opt = next((OPTION_WORDS[w] for w in words_all if w in OPTION_WORDS), None)
    if underlying and not opt and re.search(
            rf"\b({BUY_WORDS}|{SELL_WORDS}|{EXIT_WORDS})\b", t):
        # "buy bank nifty" - an index is not something you can buy.
        return {"intent": "index_needs_type", "underlying": underlying}
    if opt:
        is_exit = bool(re.search(rf"\b({EXIT_WORDS})\b", t))
        is_buy = bool(re.search(rf"\b({BUY_WORDS})\b", t))
        is_sell = bool(re.search(r"\b(sell|short|write)\b", t))
        if is_exit:
            return {"intent": "option_exit", "option_type": opt,
                    "underlying": underlying, "number_spans": spans,
                    "price_spans": price_spans}
        if is_buy:
            # Hand every number in the utterance to the caller. Telling a
            # strike from a quantity needs the live strike ladder, which
            # lives where market data does - not in the parser.
            return {"intent": "option_buy", "option_type": opt,
                    "underlying": underlying, "lots": None,
                    "number_spans": spans, "price_spans": price_spans}
        if is_sell:
            # Selling to open is unlimited-risk and sounds too much like
            # "exit". Refuse rather than guess which was meant.
            return {"intent": "option_refused", "option_type": opt,
                    "underlying": underlying,
                    "reason": "Selling options to open is not supported. "
                              "Say 'exit call' or 'exit put' to close a position."}
        return {"intent": "option_quote", "option_type": opt,
                "underlying": underlying, "number_spans": spans,
                "price_spans": price_spans}

    # "what is yesbank at" / "price of yesbank" / "yesbank quote"
    m = re.search(r"(?:price of|quote for|quote|what'?s|what is|how much is)\s+([a-z0-9 ]+?)"
                  r"(?:\s+(?:at|act|add|trading|going|doing|now))?$", t)
    if m:
        return {"intent": "quote", "name": canonical(m.group(1).strip())}

    side = None
    if re.search(rf"\b({BUY_WORDS})\b", t):
        side = "B"
    elif re.search(rf"\b({SELL_WORDS})\b", t):
        side = "S"

    # Intraday or delivery said as part of the order. Phrases first, so no
    # stray "for the" is left behind to be read as part of a company name.
    product = None
    if re.search(r"\b(?:for the day|for today|same day)\b", t):
        product = "I"
        t = re.sub(r"\b(?:for the day|for today|same day)\b", " ", t)
    if re.search(r"\b(?:to hold|to keep)\b", t):
        product = "C"
        t = re.sub(r"\b(?:to hold|to keep)\b", " ", t)
    t_words = t.split()
    for w in list(t_words):
        if w in PRODUCT_WORDS:
            product = PRODUCT_WORDS[w]
    # "for delivery", "as intraday" - the little word goes with it, or the
    # company becomes "tata motors for".
    t = re.sub(r"\b(?:for|as|in|on)\s+(?=(?:%s)\b)" % "|".join(PRODUCT_WORDS),
               " ", " ".join(t_words))
    t = " ".join(w for w in t.split() if w not in PRODUCT_WORDS)

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

        # "sell yesbank 500": a quantity after the name. Missing it sells
        # the whole holding.
        tail = len(rest)
        while tail > 1 and _numeric_word(rest[tail - 1]):
            tail -= 1
        if tail < len(rest):
            value, used = numbers.parse(rest[tail:])
            if value is None or used != len(rest) - tail:
                return {"intent": "number_unclear",
                        "heard": " ".join(rest[tail:])}
            if qty is not None:
                return {"intent": "quantity_ambiguous",
                        "quantities": [qty, value]}
            qty, rest = value, rest[:tail]
        if qty is not None and not float(qty).is_integer():
            return {"intent": "number_unclear", "heard": str(qty)}

        rest = [w for w in rest if w not in ("of", "all", "worth")]
        if any(_numeric_word(w) for w in rest):
            # A number left inside the name - which one was the quantity?
            return {"intent": "number_unclear", "heard": " ".join(rest)}
        name = " ".join(rest).strip()
        if name:
            return {"intent": "order", "side": side, "quantity": qty,
                    "name": canonical(name), "price": price,
                    "product": product}

    return {"intent": "unknown", "transcript": transcript}
