"""The index options this program can trade, in one place.

Everything that differs between indices is written down here once. Lot
sizes, expiry dates, trading symbols and tokens are deliberately NOT here:
they change, and they differ in format between exchanges (SENSEX weekly
contracts look like SENSEX26O0173900PE, NIFTY ones like NIFTY29SEP26C23100),
so they are always read from the broker's symbol master.

Adding an index is one entry below, after checking its row in the master.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Underlying:
    name: str            # canonical name used across the program
    spoken: str          # how it is read back
    segment: str         # derivatives exchange segment
    master_symbol: str   # its `Symbol` value in that segment's master file
    spot: tuple          # (exchange, token) of the index itself
    step: int            # strike spacing near the money
    cadence: str         # "weekly" or "monthly" - said in the readback
    aliases: tuple = field(default_factory=tuple)


UNDERLYINGS = {
    "NIFTY": Underlying(
        name="NIFTY", spoken="Nifty", segment="NFO", master_symbol="NIFTY",
        spot=("NSE", "26000"), step=50, cadence="weekly",
        aliases=("nifty", "nifty fifty", "nifty 50"),
    ),
    # No weekly contract exists - SEBI allows one weekly index expiry per
    # exchange, and on NSE that is NIFTY. Nearest is always a monthly.
    "BANKNIFTY": Underlying(
        name="BANKNIFTY", spoken="Bank Nifty", segment="NFO",
        master_symbol="BANKNIFTY", spot=("NSE", "26009"), step=100,
        cadence="monthly",
        aliases=("bank nifty", "banknifty", "bank nifty's"),
    ),
    # On BFO the options are listed under BSXOPT, not SENSEX. BSE token 1 is
    # the SENSEX index; token 47 is SENSEX50, a different index.
    "SENSEX": Underlying(
        name="SENSEX", spoken="Sensex", segment="BFO", master_symbol="BSXOPT",
        spot=("BSE", "1"), step=100, cadence="weekly",
        aliases=("sensex", "sense x", "sensex's", "census", "sensitive index"),
    ),
}

# Indices people will name that are NOT tradeable here. Recognising them
# matters: "fin nifty call" contains "nifty", and must not become a NIFTY
# order by accident.
UNSUPPORTED = {
    "fin nifty": "FINNIFTY", "finnifty": "FINNIFTY",
    "midcap nifty": "MIDCPNIFTY", "midcp nifty": "MIDCPNIFTY",
    "nifty next fifty": "NIFTYNXT50", "nifty next 50": "NIFTYNXT50",
    "sensex fifty": "SENSEX50", "sensex 50": "SENSEX50",
    "bankex": "BANKEX",
}

# How far from spot a spoken strike may be, in strikes. Wide enough to
# name anything you would plausibly trade, narrow enough that a mis-read
# number cannot land on a real but absurd contract.
STRIKE_WINDOW = 15


def get(name):
    return UNDERLYINGS[name]


def band(name):
    """Points either side of spot that a spoken strike may fall in."""
    return UNDERLYINGS[name].step * STRIKE_WINDOW
