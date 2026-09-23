"""What people say -> what the broker can search for.

Whisper transcribes spoken tickers loosely: YESBANK comes back as "yes bank",
"years bank", "yes-bank". Rather than hoping the decoder gets it right, map
the variants explicitly. Keys are matched after lowercasing and collapsing
whitespace.
"""

ALIASES = {
    # YESBANK
    "yes bank": "yesbank", "yesbank": "yesbank", "years bank": "yesbank",
    "yes banks": "yesbank", "yes-bank": "yesbank", "yesbanks": "yesbank",
    "ese bank": "yesbank", "yes bank limited": "yesbank",
    # Common others, for when the allowlist widens
    "reliance": "reliance", "reliance industries": "reliance",
    "nifty bees": "niftybees", "niftybees": "niftybees",
    "nifty be": "niftybees", "nifty bees etf": "niftybees",
    "state bank": "sbin", "sbi": "sbin",
    "tata motors": "tatamotors", "infosys": "infy",
}


def canonical(name):
    """Map a spoken name to a searchable one. Unknown names pass through."""
    if not name:
        return name
    key = " ".join(name.lower().split())
    key = key.strip(" .!?,-")
    if key in ALIASES:
        return ALIASES[key]
    # "yes bank" inside a longer phrase, e.g. "yes bank shares"
    for spoken, canon in ALIASES.items():
        if key.startswith(spoken + " ") or key.endswith(" " + spoken):
            return canon
    return key
