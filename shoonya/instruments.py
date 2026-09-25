"""Derivative contract lookup, from Shoonya's public symbol master.

The master file needs no authentication, so contracts can be resolved even
when the F&O segment is not enabled on the account - only quoting and
trading them requires that.

Format note: the official docs give option symbols as NIFTY24DEC24000CE.
That form does not exist. Real contracts look like NIFTY29SEP26C23450 -
underlying, then DDMMMYY of expiry, then C or P, then the strike. Symbols
here are always read from the master, never constructed by hand.
"""

import csv
import io
import time
import urllib.request
import zipfile
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

from shoonya import underlyings

IST = ZoneInfo("Asia/Kolkata")
CLOSE = dtime(15, 30)

MASTER_URL = "https://api.shoonya.com/{segment}_symbols.txt.zip"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_cache = {}


def _master_path(segment):
    return DATA_DIR / f"{segment}_symbols.txt"


def download_master(segment="NFO", max_age_hours=12):
    """Fetch the segment's contract master if the local copy is stale.

    Contracts roll over and tokens change, so the docs advise a refresh at
    least daily.
    """
    path = _master_path(segment)
    if path.exists():
        age = (time.time() - path.stat().st_mtime) / 3600
        if age < max_age_hours:
            return path

    DATA_DIR.mkdir(exist_ok=True)
    url = MASTER_URL.format(segment=segment)
    with urllib.request.urlopen(url, timeout=60) as resp:
        blob = resp.read()
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = next(n for n in z.namelist() if n.endswith(".txt"))
        path.write_bytes(z.read(name))
    return path


def load(segment="NFO", symbol="NIFTY", instrument="OPTIDX", underlying=None):
    """All contracts for one master-file symbol, parsed and cached."""
    key = (segment, symbol, instrument)
    if key in _cache:
        return _cache[key]

    path = download_master(segment)
    out = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("Symbol") != symbol:
                continue
            if instrument and row.get("Instrument") != instrument:
                continue
            try:
                out.append({
                    "tsym": row["TradingSymbol"],
                    "token": row["Token"],
                    "lot": int(row["LotSize"]),
                    "tick": float(row["TickSize"]),
                    "strike": int(float(row["StrikePrice"] or 0)),
                    # From the master's own column - never parsed out of the
                    # trading symbol, whose layout differs by exchange.
                    "option_type": row.get("OptionType", "").strip(),
                    "expiry": datetime.strptime(row["Expiry"], "%d-%b-%Y").date(),
                    "exchange": row["Exchange"],
                    "underlying": underlying or symbol,
                })
            except (ValueError, KeyError):
                continue
    _cache[key] = out
    return out


def contracts(name):
    """Option contracts for an underlying named in shoonya.underlyings."""
    u = underlyings.get(name)
    return load(segment=u.segment, symbol=u.master_symbol, underlying=u.name)


def _now_ist():
    return datetime.now(IST)


def expiries(name="NIFTY", now=None):
    """Upcoming expiry dates, soonest first.

    On expiry day the expiring contract stays first until the close, then
    drops off - trading it after 15:30 would target a contract that has
    already expired.
    """
    now = now or _now_ist()
    today = now.date()
    after_close = now.time() >= CLOSE
    return sorted({c["expiry"] for c in contracts(name)
                   if c["expiry"] > today
                   or (c["expiry"] == today and not after_close)})


def atm_strike(spot, name="NIFTY"):
    """Nearest strike to spot on this underlying's spacing."""
    step = underlyings.get(name).step
    return int(round(spot / step) * step)


def ladder(name, expiry):
    """The strikes actually listed for one expiry."""
    return {c["strike"] for c in contracts(name) if c["expiry"] == expiry}


def find(name="NIFTY", option_type="CE", strike=None, expiry=None, spot=None):
    """Resolve one option contract.

    option_type: 'CE' or 'PE'. strike: explicit, or at-the-money from
    `spot`. expiry: explicit, or the nearest tradeable one.
    Returns the contract dict, or None.
    """
    rows = contracts(name)
    if not rows:
        return None
    if expiry is None:
        upcoming = expiries(name)
        if not upcoming:
            return None
        expiry = upcoming[0]
    if strike is None:
        if spot is None:
            return None
        strike = atm_strike(spot, name)

    same = [c for c in rows
            if c["expiry"] == expiry and c["option_type"] == option_type]
    exact = [c for c in same if c["strike"] == strike]
    if exact:
        return dict(exact[0])
    if not same:
        return None
    # Requested strike is not listed - use the closest that is, and say so,
    # rather than inventing a symbol.
    nearest = dict(min(same, key=lambda c: abs(c["strike"] - strike)))
    nearest["strike_adjusted_from"] = strike
    return nearest


def contract_for(tsym):
    """Look a held position's symbol up in the masters.

    Replaces pattern-matching the symbol for call/put and token, which only
    ever worked for NIFTY's layout - it would never have recognised a
    SENSEX position.
    """
    for name in underlyings.UNDERLYINGS:
        for c in contracts(name):
            if c["tsym"] == tsym:
                return c
    return None
