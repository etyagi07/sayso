"""Stocks that can be traded by voice, and the names people use for them.

Spoken company names are the hardest thing in this program to get right.
The first bug it ever had was "idea" resolving to IDEAFORGE by prefix
search. So names are matched against this fixed list - never searched for
- and anything that could mean two companies is asked about, not guessed.

The list is the Nifty 50 as of the September 2025 rebalance, with tokens
checked against the broker, plus Yes Bank for low-cost testing. It is
deliberately fixed: index options are the focus, and a short, known list
is one that can be checked by hand. Membership changes every six months,
so it is updated here, in code.
"""

import difflib
import re

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

# Not in the Nifty 50. The cheapest liquid stock - a ~Rs 23 way to test the
# whole pipeline with real money on a small account.
EXTRAS = {
    "YESBANK": ("11915", "Yes Bank", ["yesbank", "yes bank"]),
}

FILLER = re.compile(r"\b(?:the|shares?|stocks?|ltd|limited|of|company)\b")


def clean(name):
    """Lowercase, '&' spelled out, filler removed - the form names match in."""
    name = name.lower().replace("&", " and ").replace(".", " ").replace("'", "")
    name = FILLER.sub(" ", name)
    return " ".join(name.split())


def all_stocks():
    """{symbol: {token, company, names}} - the Nifty 50, then Yes Bank."""
    out = {}
    for table in (NIFTY50, EXTRAS):
        for sym, (token, company, names) in table.items():
            out[sym] = {"token": token, "company": company,
                        "names": [clean(n) for n in names]}
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
