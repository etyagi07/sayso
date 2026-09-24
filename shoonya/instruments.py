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
from datetime import datetime, date
from pathlib import Path

MASTER_URL = "https://api.shoonya.com/{segment}_symbols.txt.zip"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Strike spacing near the money, per underlying. The master has finer
# spacing far out; near spot Nifty runs in 50s.
STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50, "MIDCPNIFTY": 25}

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


def load(segment="NFO", symbol="NIFTY", instrument="OPTIDX"):
    """All contracts for one underlying, parsed and cached in-process."""
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
                    "option_type": row.get("OptionType", "").strip(),
                    "expiry": datetime.strptime(row["Expiry"], "%d-%b-%Y").date(),
                    "exchange": row["Exchange"],
                })
            except (ValueError, KeyError):
                continue
    _cache[key] = out
    return out


def expiries(symbol="NIFTY", on=None):
    """Upcoming expiry dates, soonest first."""
    on = on or date.today()
    return sorted({c["expiry"] for c in load(symbol=symbol) if c["expiry"] >= on})


def atm_strike(spot, symbol="NIFTY"):
    """Nearest tradeable strike to spot."""
    step = STRIKE_STEP.get(symbol, 50)
    return int(round(spot / step) * step)


def find(symbol="NIFTY", option_type="CE", strike=None, expiry=None, spot=None):
    """Resolve one option contract.

    option_type: 'CE' (call) or 'PE' (put).
    strike: explicit, or derived from `spot` if omitted.
    expiry: explicit date, or the nearest upcoming one.

    Returns the contract dict, or None with no match.
    """
    contracts = load(symbol=symbol)
    if not contracts:
        return None

    if expiry is None:
        upcoming = expiries(symbol)
        if not upcoming:
            return None
        expiry = upcoming[0]

    if strike is None:
        if spot is None:
            return None
        strike = atm_strike(spot, symbol)

    matches = [c for c in contracts
               if c["expiry"] == expiry
               and c["option_type"] == option_type
               and c["strike"] == strike]
    if matches:
        return matches[0]

    # Requested strike is not listed - fall back to the closest that is,
    # rather than failing or inventing a symbol.
    same = [c for c in contracts
            if c["expiry"] == expiry and c["option_type"] == option_type]
    if not same:
        return None
    nearest = min(same, key=lambda c: abs(c["strike"] - strike))
    nearest = dict(nearest)
    nearest["strike_adjusted_from"] = strike
    return nearest
