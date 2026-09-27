"""Turn a transcript into a structured intent.

Rule-based and offline. The rule for anything that would trade: every word
must have a known job - an action, an index, call or put, a company, a
number in a clear role, intraday or delivery, or harmless filler. A word
with no job means the sentence is asked about, not guessed at. Ignoring
unknown words is what turned "buy nifty call stop loss 5" into 5 lots and
"is my nifty call closed?" into an exit.

tests/test_phrasebook.py lists what each kind of sentence does.
"""

import re

from voice import numbers, strikes
from voice.aliases import canonical
from voice.fuzzy import LEADING_FILLER, normalise

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


def _quantity(tokens):
    """A share count, read whole - or None. "two hundred fifty" is 250; "two
    fifty" (which adds up to 52) and "one zero zero" (1) are not counts."""
    if not strikes._additive(tokens):
        return None
    value, used = numbers.parse(tokens)
    if value is None or used != len(tokens) or not float(value).is_integer():
        return None
    return int(value)


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
# Asking about the past or the state of things - "is my call closed?", "did
# I buy a call?" - is never an order, whatever words follow.
STATUS_QUESTION = re.compile(r"^(?:(?:ok|okay|so|um|uh|hey|alright|well|hmm|"
                             r"and|but)\s+)*(?:did|does|do|is|are|was|were|"
                             r"has|have|had|will|when|who|why|how|what|which|"
                             r"where)\b")
# The speaker changing their mind mid-sentence. The last one wins. A bare
# "no" is not here: "call, no put" can mean "not put" as easily as "put
# instead", so it is asked about.
CORRECTION = re.compile(r"\b(?:no wait|no no|wait|sorry|i mean|actually|"
                        r"make that|make it|change that to)\b")
# "a call instead of a put" names the thing NOT wanted after the marker, so
# it is dropped rather than read as a correction to it.
NOT_THIS = re.compile(r"\b(?:instead of|rather than)\s+(?:an?\s+|the\s+)?\S+")
NEGATION = {"don't", "dont", "not", "never", "no", "won't", "wont", "can't",
            "cant", "cannot", "shouldn't", "shouldnt", "wouldn't", "wouldnt",
            "couldn't", "couldnt", "didn't", "didnt", "mustn't"}
# Said before the action, these call it off: "cancel buy nifty call".
CALL_OFF = NEGATION | {"cancel", "stop"}
# A number after these is a price - never a lot count.
PRICE_WORDS = ("at", "@", "for")
LOT_WORDS = ("lot", "lots")
TRADES = {"option_buy", "option_exit", "order"}
# Sounds, not words. Never "oh" - that is a digit ("two three oh five").
HESITATION = {"um", "umm", "uh", "uhh", "hm", "hmm", "mm", "mmm", "er", "erm",
              "ah", "eh"}
# Words that can sit in an option order without changing it.
OPTION_FILLER = {"a", "an", "the", "of", "on", "in", "for", "at", "@", "it",
                 "index", "option", "options", "position", "positions",
                 "contract", "strike", "all", "lot", "lots", "atm", "weekly"}
# Things said as the part after "sorry" / "make it": a detail, nothing more.
DETAIL_FILLER = {"a", "an", "the", "it", "that", "one", "lot", "lots", "of"}


def _slots(text):
    """Option type, index and numbers mentioned in a fragment of speech."""
    words = text.split()
    opts = [OPTION_WORDS[w] for w in words if w in OPTION_WORDS]
    index = next((INDEX_WORDS[w] for w in words if w in INDEX_WORDS), None)
    spans = [span for _, _, span in strikes.number_spans(words)]
    return (opts[-1] if opts else None), index, spans


def _is_verb(word):
    return bool(re.fullmatch(rf"{BUY_WORDS}|{SELL_WORDS}|{EXIT_WORDS}", word))


def _rupee_amount(words):
    """A sum of money said as the size of an order: "worth 500", "500
    rupees of". A price is fine - "at 22 rupees" - an amount is not."""
    if "worth" in words:
        return True
    for i, w in enumerate(words):
        if w not in ("rupee", "rupees", "rs"):
            continue
        j = i
        while j > 0 and strikes._is_numeric(words[j - 1]):
            j -= 1
        if j < i and not (j > 0 and words[j - 1] in PRICE_WORDS):
            return True
    return False


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
    t = re.sub(r"(?<=\d),(?=\d)", "", t)
    # "₹8" is eight rupees - kept as words, so an amount can be told
    # from a price.
    t = re.sub(r"\u20b9\s*([\d.]+)", r"\1 rupees", t).replace("\u20b9", " ")
    # "twenty-five" is two number words, not one unreadable one.
    t = re.sub(r"(?<=[A-Za-z])-(?=[A-Za-z])", " ", t)
    t = normalise(t)
    t = re.sub(r"[.!?,;:]+(?!\d)", " ", t)
    t = " ".join(t.split())

    if CANCEL.match(t):
        return {"intent": "cancel"}
    if re.search(r"\bcancel\b", t) and re.search(r"\border", t):
        return {"intent": "cancel_order_unsupported"}
    if QUESTION.match(t) or QUESTION_ANYWHERE.search(t):
        return {"intent": "question", "transcript": transcript}

    result = _parse(t, transcript)
    # "did I buy a call?", "is my call closed?" - about the past or the
    # state of things. What/how/which also open price questions ("what is
    # the nifty call at"), so those stay quotes.
    m = STATUS_QUESTION.match(t)
    if m and (result["intent"] in TRADES | {"not_followed"}
              or (result["intent"] == "option_quote"
                  and m.group(0).split()[-1] not in ("what", "how", "which"))):
        return {"intent": "question", "transcript": transcript}
    return result


def _parse(t, transcript):
    """parse(), after the text is tidied and the obvious non-orders are out."""
    if _rupee_amount(t.split()):
        return {"intent": "rupee_amount", "transcript": transcript}

    t = NOT_THIS.sub(" ", t)
    t = " ".join(w for w in t.split() if w not in ("instead", "rather"))

    words = t.split()
    verbs = [i for i, w in enumerate(words) if _is_verb(w)]

    # A bare "no" after the action: "buy nifty call no put". Could be a
    # correction or a "not"; either reading is a guess.
    for i, w in enumerate(words):
        if (w == "no" and verbs and i > verbs[0]
                and words[i + 1:i + 2] not in (["wait"], ["no"])
                and words[i - 1] != "no"):
            return {"intent": "unclear_correction", "transcript": transcript}

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
        # Only a like-for-like swap: call for put, an index for an index, a
        # strike for a strike, lots for lots. Replacing every number lost
        # the strike when only the lots were corrected.
        extra = [w for w in after_words
                 if w not in OPTION_WORDS and w not in INDEX_WORDS
                 and w not in DETAIL_FILLER and not strikes._is_numeric(w)]
        if (extra or not base["intent"].startswith("option_")
                or not (opt or index or spans) or len(spans) > 1):
            return {"intent": "unclear_correction", "transcript": transcript}
        if opt:
            base["option_type"] = opt
        if index:
            base["underlying"] = index
        if spans:
            key = ("lots_override" if any(w in LOT_WORDS for w in after_words)
                   else "strike_override")
            base[key] = spans[0]
        base["corrected"] = True
        return base

    # "don't buy a call", "cancel buy nifty call" - not an instruction.
    if verbs and any(w in CALL_OFF for w in words[:verbs[0]]):
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
        # "a call not a put": drop the negated option word, the "not" and
        # its article, so nothing is left over without a job.
        keep = kinds.pop()
        drop = set()
        for i, w in enumerate(words):
            if w in OPTION_WORDS and OPTION_WORDS[w] != keep:
                drop.add(i)
                for j in (i - 1, i - 2):
                    if j >= 0 and words[j] in NEGATION | {"a", "an", "the"}:
                        drop.add(j)
        t = " ".join(w for i, w in enumerate(words) if i not in drop)

    return _parse_one(t)


def _parse_one(transcript):
    """Read one command, with no negation or correction left in it."""
    # Repair recogniser near-misses and collapse synonyms before any
    # pattern matching, so the rules below only see canonical vocabulary.
    t = normalise(transcript, first_word=False)
    # Whisper punctuates freely: "BUY 10 YESBANK." must not search "yesbank."
    # But a decimal point inside a price is data, not punctuation - only
    # strip marks that are NOT followed by a digit, so 23.20 survives.
    t = re.sub(r"[.!?,;:]+(?!\d)", " ", t)
    # Word-bounded, or "years bank" loses its "rs " and becomes "yeabank".
    # "Okay, buy a put": what is said before a command isn't part of it,
    # and hesitation sounds carry no meaning wherever they fall.
    words0 = [w for w in t.split() if w not in HESITATION]
    while words0 and words0[0] in LEADING_FILLER:
        words0.pop(0)
    t = " ".join(words0)
    # Orders always go at market, so saying so changes nothing.
    t = re.sub(r"\b(?:at\s+)?(?:the\s+)?market(?:\s+price)?\b", " ", t)
    t = re.sub(r"\b(?:right\s+)?now\b", " ", t)
    t = re.sub(r"\bat\s+the\s+money\b", "atm", t)
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

    # Information requests - but not when an action was said: "buy nifty
    # call out of the money" is not a question about funds.
    acting = bool(re.search(rf"\b({BUY_WORDS}|{SELL_WORDS}|{EXIT_WORDS})\b", t))
    if not acting and re.search(r"\b(funds?|balance|cash|money|buying power)\b", t):
        return {"intent": "funds"}
    if not acting and re.search(
            r"\b(positions?|holdings?|what do i (own|have)|portfolio)\b", t):
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
    #
    # Lots must be labelled: "2 lots", or said right before the index or
    # call/put ("2 nifty calls"). A number anywhere else can only be a
    # strike - "buy nifty call 5" is not 5 lots.
    spans, price_spans, strike_spans = [], [], []
    for start, end, span in strikes.number_spans(words_all):
        before = words_all[start - 1] if start > 0 else None
        after = words_all[end] if end < len(words_all) else None
        if after in LOT_WORDS:
            spans.append(span)
        elif before in PRICE_WORDS:
            price_spans.append(span)
        elif after in INDEX_WORDS or after in OPTION_WORDS:
            spans.append(span)
        else:
            strike_spans.append(span)

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
        if is_exit or is_buy:
            # Every word needs a job. "half" only makes sense for an exit.
            known = OPTION_FILLER | {"exit" if is_exit else "buy"}
            if is_exit:
                known |= {"half"}
            unknown = [w for w in words_all
                       if w not in known and w not in OPTION_WORDS
                       and w not in INDEX_WORDS and not strikes._is_numeric(w)]
            if unknown:
                return {"intent": "not_followed", "heard": " ".join(unknown),
                        "kind": "exit" if is_exit else "option_buy"}
            flags = {"atm": "atm" in words_all,
                     "weekly": "weekly" in words_all,
                     "half": "half" in words_all}
        if is_exit:
            return {"intent": "option_exit", "option_type": opt,
                    "underlying": underlying, "number_spans": spans,
                    "price_spans": price_spans,
                    "strike_spans": strike_spans, **flags}
        if is_buy:
            # Hand every number in the utterance to the caller. Telling a
            # strike from a quantity needs the live strike ladder, which
            # lives where market data does - not in the parser.
            return {"intent": "option_buy", "option_type": opt,
                    "underlying": underlying, "lots": None,
                    "number_spans": spans, "price_spans": price_spans,
                    "strike_spans": strike_spans, **flags}
        if is_sell:
            # Selling to open is unlimited-risk and sounds too much like
            # "exit". Refuse rather than guess which was meant.
            return {"intent": "option_refused", "option_type": opt,
                    "underlying": underlying,
                    "reason": "Selling options to open is not supported. "
                              "Say 'exit call' or 'exit put' to close a position."}
        return {"intent": "option_quote", "option_type": opt,
                "underlying": underlying, "number_spans": spans,
                "price_spans": price_spans, "strike_spans": strike_spans}

    # "what is yesbank at" / "price of yesbank" / "yesbank quote"
    m = re.search(r"(?:price of|quote for|quote|what'?s|what is|how much is)\s+([a-z0-9 ]+?)"
                  r"(?:\s+(?:at|act|add|trading|going|doing|now))?$", t)
    if m:
        name = re.sub(r"^(?:the\s+)?(?:price|quote|rate)\s+(?:of|for)\s+", "",
                      m.group(1).strip())
        return {"intent": "quote", "name": canonical(name)}

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
                    # Nothing may follow the price: "at 22 100" dropped the
                    # 100 shares and sold the whole holding.
                    after = rest[i + 1 + used:]
                    if after:
                        return {"intent": "not_followed",
                                "heard": " ".join(after), "kind": "order"}
                    price = value
                    rest = rest[:i]
                    break

        half = "half" in rest
        rest = [w for w in rest if w != "half"]
        if half and side == "B":
            return {"intent": "not_followed", "heard": "half", "kind": "order"}

        qty = None
        lead = 0
        while lead < len(rest) and _numeric_word(rest[lead]):
            lead += 1
        # Only take it as a quantity if something remains to name the
        # instrument - "buy yesbank" must not read "yesbank" as a number.
        if 0 < lead < len(rest):
            qty = _quantity(rest[:lead])
            if qty is None:
                return {"intent": "number_unclear",
                        "heard": " ".join(rest[:lead])}
            rest = rest[lead:]

        # "sell yesbank 500": a quantity after the name. Missing it sells
        # the whole holding.
        tail = len(rest)
        while tail > 1 and _numeric_word(rest[tail - 1]):
            tail -= 1
        if tail < len(rest):
            value = _quantity(rest[tail:])
            if value is None:
                return {"intent": "number_unclear",
                        "heard": " ".join(rest[tail:])}
            if qty is not None:
                return {"intent": "quantity_ambiguous",
                        "quantities": [qty, value]}
            qty, rest = value, rest[:tail]
        if qty is not None and not float(qty).is_integer():
            return {"intent": "number_unclear", "heard": str(qty)}

        rest = [w for w in rest if w not in ("of", "all", "in", "on")]
        if any(_numeric_word(w) for w in rest):
            # A number left inside the name - which one was the quantity?
            return {"intent": "number_unclear", "heard": " ".join(rest)}
        name = " ".join(rest).strip()
        if name:
            if half and qty is not None:
                return {"intent": "quantity_ambiguous",
                        "quantities": [qty, 0.5]}
            return {"intent": "order", "side": side, "quantity": qty,
                    "name": canonical(name), "price": price,
                    "product": product, "half": half}

    return {"intent": "unknown", "transcript": transcript}
