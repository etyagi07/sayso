"""Stocks that can be traded by voice, and the names people use for them.

Spoken company names are the hardest thing in this program to get right.
The first bug it ever had was "idea" resolving to IDEAFORGE by prefix
search. So names are matched against this known list - never searched for
- and anything that could mean two companies is asked about, not guessed.

The built-in list is the Nifty 50 as of the September 2025 rebalance, with
tokens checked against the broker. Membership changes every six months, so
treat it as a starting point: add and remove your own with

    python -m voice.stocks add "tata power" TATAPOWER
    python -m voice.stocks remove TATAPOWER
    python -m voice.stocks list

Additions live in stocks.json, which is gitignored, so updates never
overwrite them.
"""

import difflib
import json
import re
import sys
from pathlib import Path

USER_FILE = Path(__file__).resolve().parent.parent / "stocks.json"

# symbol: (token, company, [spoken names])
NIFTY50 = {
    "ADANIENT": ("25", "Adani Enterprises", ["adani enterprises", "adani ent"]),
    "ADANIPORTS": ("15083", "Adani Ports", ["adani ports", "adani port"]),
    "APOLLOHOSP": ("157", "Apollo Hospitals", ["apollo hospitals", "apollo hospital", "apollo"]),
    "ASIANPAINT": ("236", "Asian Paints", ["asian paints", "asian paint"]),
    "AXISBANK": ("5900", "Axis Bank", ["axis bank", "axis"]),
    "BAJAJ-AUTO": ("16669", "Bajaj Auto", ["bajaj auto"]),
    "BAJFINANCE": ("317", "Bajaj Finance", ["bajaj finance"]),
    "BAJAJFINSV": ("16675", "Bajaj Finserv", ["bajaj finserv"]),
    "BEL": ("383", "Bharat Electronics", ["bharat electronics", "bel"]),
    "BHARTIARTL": ("10604", "Bharti Airtel", ["bharti airtel", "airtel"]),
    "CIPLA": ("694", "Cipla", ["cipla"]),
    "COALINDIA": ("20374", "Coal India", ["coal india"]),
    "DRREDDY": ("881", "Dr. Reddy's Laboratories", ["dr reddy", "dr reddys", "doctor reddy", "doctor reddys", "reddy"]),
    "EICHERMOT": ("910", "Eicher Motors", ["eicher motors", "eicher"]),
    "ETERNAL": ("5097", "Eternal (Zomato)", ["eternal", "zomato"]),
    "GRASIM": ("1232", "Grasim Industries", ["grasim"]),
    "HCLTECH": ("7229", "HCL Technologies", ["hcl tech", "hcl technologies", "hcl"]),
    "HDFCBANK": ("1333", "HDFC Bank", ["hdfc bank"]),
    "HDFCLIFE": ("467", "HDFC Life", ["hdfc life"]),
    "HINDALCO": ("1363", "Hindalco Industries", ["hindalco"]),
    "HINDUNILVR": ("1394", "Hindustan Unilever", ["hindustan unilever", "hul"]),
    "ICICIBANK": ("4963", "ICICI Bank", ["icici bank", "icici"]),
    "INDIGO": ("11195", "InterGlobe Aviation (IndiGo)", ["indigo", "interglobe"]),
    "INFY": ("1594", "Infosys", ["infosys", "infy"]),
    "ITC": ("1660", "ITC", ["itc"]),
    "JIOFIN": ("18143", "Jio Financial Services", ["jio financial", "jio finance", "jio financial services", "jio fin"]),
    "JSWSTEEL": ("11723", "JSW Steel", ["jsw steel"]),
    "KOTAKBANK": ("1922", "Kotak Mahindra Bank", ["kotak bank", "kotak mahindra bank", "kotak"]),
    "LT": ("11483", "Larsen & Toubro", ["l and t", "l&t", "lnt", "larsen", "larsen and toubro"]),
    "M&M": ("2031", "Mahindra & Mahindra", ["mahindra and mahindra", "m and m", "m&m"]),
    "MARUTI": ("10999", "Maruti Suzuki", ["maruti", "maruti suzuki"]),
    "MAXHEALTH": ("22377", "Max Healthcare", ["max healthcare", "max health"]),
    "NESTLEIND": ("17963", "Nestle India", ["nestle", "nestle india"]),
    "NTPC": ("11630", "NTPC", ["ntpc"]),
    "ONGC": ("2475", "ONGC", ["ongc", "oil and natural gas"]),
    "POWERGRID": ("14977", "Power Grid", ["power grid"]),
    "RELIANCE": ("2885", "Reliance Industries", ["reliance", "reliance industries", "ril"]),
    "SBILIFE": ("21808", "SBI Life Insurance", ["sbi life"]),
    "SBIN": ("3045", "State Bank of India", ["sbi", "state bank", "state bank of india"]),
    "SHRIRAMFIN": ("4306", "Shriram Finance", ["shriram finance", "shriram"]),
    "SUNPHARMA": ("3351", "Sun Pharmaceutical", ["sun pharma", "sun pharmaceutical"]),
    "TATACONSUM": ("3432", "Tata Consumer Products", ["tata consumer", "tata consumer products"]),
    "TMPV": ("3456", "Tata Motors Passenger Vehicles", ["tata motors", "tata motors passenger", "tata motors pv", "tmpv"]),
    "TATASTEEL": ("3499", "Tata Steel", ["tata steel"]),
    "TCS": ("11536", "Tata Consultancy Services", ["tcs", "tata consultancy", "tata consultancy services"]),
    "TECHM": ("13538", "Tech Mahindra", ["tech mahindra", "tech m"]),
    "TITAN": ("3506", "Titan Company", ["titan"]),
    "TRENT": ("1964", "Trent", ["trent"]),
    "ULTRACEMCO": ("11532", "UltraTech Cement", ["ultratech", "ultratech cement"]),
    "WIPRO": ("3787", "Wipro", ["wipro"]),
}

# Not in the Nifty 50, but worth having.
EXTRAS = {
    # The cheapest liquid stock - a ~Rs 23 way to test the whole pipeline.
    "YESBANK": ("11915", "Yes Bank", ["yesbank", "yes bank"]),
    # The other half of the 2025 Tata Motors demerger. "tata motors" names
    # both, so it gets asked about rather than guessed.
    "TMCV": ("759782", "Tata Motors (Commercial Vehicles)", ["tata motors", "tata motors commercial", "tata motors cv", "tmcv"]),
}

FILLER = re.compile(r"\b(?:the|shares?|stocks?|ltd|limited|of|company)\b")


def clean(name):
    """Lowercase, '&' spelled out, filler removed - the form names match in."""
    name = name.lower().replace("&", " and ").replace(".", " ").replace("'", "")
    name = FILLER.sub(" ", name)
    return " ".join(name.split())


def _user():
    try:
        return json.loads(USER_FILE.read_text())
    except (OSError, ValueError):
        return {}


def all_stocks():
    """{symbol: {token, company, names}} - built-in, extras, then yours."""
    out = {}
    for table in (NIFTY50, EXTRAS):
        for sym, (token, company, names) in table.items():
            out[sym] = {"token": token, "company": company,
                        "names": [clean(n) for n in names]}
    for sym, row in _user().items():
        if row.get("removed"):
            out.pop(sym, None)
            continue
        out[sym] = {"token": row["token"], "company": row["company"],
                    "names": [clean(n) for n in row.get("names", [])]}
    return out


def symbols():
    return set(all_stocks())


def _hit(sym, table, how):
    row = table[sym]
    return {"symbol": sym, "tsym": f"{sym}-EQ", "token": row["token"],
            "company": row["company"], "match": how}


def resolve(spoken):
    """Match a spoken name to one stock.

    Returns one of:
      {symbol, tsym, token, company, match}   - a single stock
      {"ambiguous": [(symbol, company), ...]} - more than one fits
      None                                    - nothing known fits
    """
    table = all_stocks()
    n = clean(spoken)
    if not n:
        return None

    by_name = {}
    for sym, row in table.items():
        for name in row["names"]:
            by_name.setdefault(name, set()).add(sym)

    def one_or_many(found, how):
        if len(found) == 1:
            return _hit(next(iter(found)), table, how)
        return {"ambiguous": sorted((s, table[s]["company"]) for s in found)}

    # 1. A name we know exactly - possibly one that two companies share.
    if n in by_name:
        return one_or_many(by_name[n], "name")
    # 2. The ticker itself: "infy", "sbin", "bajaj-auto".
    for sym in table:
        if n.replace(" ", "") == sym.lower().replace("-", "").replace("&", "and"):
            return _hit(sym, table, "symbol")
    # 3. The start of a longer name: "tata" -> every Tata company.
    starts = {s for name, syms in by_name.items()
              if name.startswith(n + " ") for s in syms}
    if starts:
        return one_or_many(starts, "prefix")
    # 4. A near-miss from the recogniser - strict, since a wrong company
    #    looks entirely plausible on the confirmation screen.
    close = difflib.get_close_matches(n, list(by_name), n=3, cutoff=0.86)
    found = {s for c in close for s in by_name[c]}
    if found:
        return one_or_many(found, "close")
    # Never drop words to force a match: "sbi card" is not SBI, and "sun
    # tv" is not Sun Pharma. An unknown name is asked about, not guessed.
    return None


# --- managing your own list -----------------------------------------------

def add(name, symbol):
    """Add a stock after checking it exists on NSE, reading its name back."""
    import shoonya.broker as b

    symbol = symbol.upper().removesuffix("-EQ")
    uid = getattr(b.api(), "_NorenApi__username", None)
    # Search by the name, not the symbol: symbols with '&' break the search.
    found = None
    for query in (symbol, name):
        res = b._raw_post("/SearchScrip", {"uid": uid, "exch": "NSE",
                                           "stext": query})
        found = next((v for v in (res.get("values") or [])
                      if v.get("tsym") == f"{symbol}-EQ"), None)
        if found:
            break
    if not found:
        raise SystemExit(f"{symbol}-EQ isn't listed on NSE. Check the symbol "
                         f"- it's the ticker, e.g. TATAPOWER, not the name.")

    rows = _user()
    existing = rows.get(symbol, {})
    names = sorted(set(existing.get("names", [])) | {name.lower().strip()})
    rows[symbol] = {"token": found["token"],
                    "company": (found.get("cname") or symbol).title(),
                    "names": names}
    USER_FILE.write_text(json.dumps(rows, indent=2) + "\n")
    return rows[symbol]


def remove(symbol):
    symbol = symbol.upper().removesuffix("-EQ")
    rows = _user()
    if symbol in NIFTY50 or symbol in EXTRAS:
        # Built-ins are hidden, not deleted, so an update cannot bring
        # back something you took out.
        rows[symbol] = {"removed": True}
    elif symbol in rows:
        del rows[symbol]
    else:
        raise SystemExit(f"{symbol} isn't in the list.")
    USER_FILE.write_text(json.dumps(rows, indent=2) + "\n")


def _main(argv):
    if len(argv) >= 3 and argv[0] == "add":
        row = add(" ".join(argv[1:-1]), argv[-1])
        print(f"Added {argv[-1].upper()} - {row['company']}, said as: "
              f"{', '.join(row['names'])}")
    elif len(argv) == 2 and argv[0] == "remove":
        remove(argv[1])
        print(f"Removed {argv[1].upper()}.")
    elif argv[:1] == ["list"] or not argv:
        for sym, row in sorted(all_stocks().items()):
            print(f"  {sym:<12} {row['company']:<34} {', '.join(row['names'])}")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
