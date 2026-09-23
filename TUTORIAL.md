# Tutorial: from clone to your first voice trade

Roughly 20 minutes, most of it waiting for a model download. Each step says
what you should see, so you can tell working from broken.

Everything up to Step 6 is read-only and cannot place an order.

---

## Before you start

You need:

- **macOS on Apple Silicon.** The Whisper backend (MLX) is Apple-specific.
- **Python 3.12 or newer.** `python3 --version` — if macOS gives you 3.9,
  install a newer one (`brew install python@3.12`).
- **A Shoonya account with API access**, and an API app registered. From
  that registration you need three things: your **client ID**, your **user
  ID**, and your **secret code**.
- **A funded account** if you want to place real orders. ₹100 is plenty —
  the defaults cap orders at that, and this whole project was tested for
  about ₹0.07 in brokerage.

---

## Step 1 — Install

```bash
git clone https://github.com/<you>/sayso.git
cd sayso
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The install pulls Whisper and PyTorch-adjacent wheels, so expect a minute or
two and a few hundred MB.

---

## Step 2 — Credentials

```bash
cp .env.example .env
```

Open `.env` and fill in all three values:

```
SHOONYA_CLIENT_ID=ABC123_U
SHOONYA_USER_ID=ABC123
SHOONYA_SECRET_CODE=<64 character string>
```

`.env` is gitignored. Never commit it, never paste its contents anywhere.

**Check the redirect URI on your Shoonya app registration.** It is usually
something like `http://127.0.0.1:8787/`. Nothing listens on that port, so
after login your browser will show `ERR_CONNECTION_REFUSED` — that is
expected, and the bit you need is in the address bar.

---

## Step 3 — Log in

```bash
.venv/bin/python -m shoonya.login
```

It prints an authorize URL and opens your browser.

1. Log in with your Shoonya user ID, password and OTP.
2. You land on a dead `127.0.0.1` page. **Copy the whole URL** from the
   address bar — it looks like
   `127.0.0.1:8787/?code=2142a242-1f17-4977-a1a3-e36ec6779e6b`.
3. Paste it at the prompt.

You should see:

```
Logged in as ABC123 (account ABC123). Session cached in .session.json.
```

The session is cached, so you only do this **once per trading day** — tokens
are day-scoped and there is no refresh flow.

---

## Step 4 — Check the API works

```bash
.venv/bin/python smoke_test.py
```

Read-only. Prints your funds, positions, holdings, order book, a symbol
search and live quotes for RELIANCE, Nifty 50 and Bank Nifty.

If quotes come back with prices, your session is good.

> **Note:** `cash` will read `0.00` even with money in the account —
> that field means *settled* cash. Look at `available` instead.

---

## Step 5 — Check your microphone

This catches the most common setup failure before it wastes your time.

```bash
.venv/bin/python -m voice.miccheck
```

Speak for the 5 seconds it gives you. You want to see a clear difference
between silence and speech:

```
  0.0063 quiet  |██
  0.1900 SPEECH |████████████████████████████████████████
```

**All zeros?** macOS is blocking the mic. System Settings → Privacy &
Security → Microphone, enable your terminal app, then **fully quit and
reopen it** — the permission is only read at launch.

**Everything under the threshold?** Your input volume is low:

```bash
osascript -e "set volume input volume 85"
```

**Speech barely above the noise floor?** Set `SILENCE_RMS` in
`voice/listen.py` to sit between the two — roughly a third of your speech
level. The default (0.030) suits a quiet room at 85% input volume.

---

## Step 6 — Talk to it

```bash
.venv/bin/python -m voice.main
```

First run downloads the Whisper model (~500 MB, once). Then:

```
● READY - press Enter to speak · 't' to type · ctrl-c to quit
```

Press **Enter**, wait for `● RECORDING`, speak, then pause. It stops on its
own about 1.4 seconds after you go quiet.

Start with something that cannot trade:

> *"what's yesbank at"*

```
  heard: "What's YESBANK at?"
  YESBANK is at 23.22, +0.09 percent.
```

Other safe things to try:

```
funds
what do i own
what are my limits
```

Press `t` instead of Enter to type a command — useful for testing without
speaking.

---

## Step 7 — Your first order

> From here, confirmed orders are **real**.

Say:

> *"buy one yesbank at twenty three point two zero"*

You will get a confirmation box:

```
┌─ CONFIRM ─────────────────────────────────
│  BUY  1 x YESBANK-EQ
│  limit    23.20   (market 23.21)
│  total    23.20 rupees
└───────────────────────────────────────────
  press y to send, anything else to cancel:
```

**Read it before pressing anything.** Check the symbol, the quantity and the
total. Press any key except `y` to cancel — do that the first time, just to
see it refuse.

When you do press `y`:

```
  Order is OPEN, not filled yet.
```

or

```
  Done. buy 1 YESBANK at 23.20.
```

`OPEN` means the order is resting in the book because your price is not
crossing the market. It fills when the market reaches you, or expires at
close.

---

## Speaking prices

Say **"point"** explicitly:

| Say this | Get this |
|---|---|
| "twenty three point two zero" | 23.20 |
| "twenty three point two two" | 23.22 |
| "twenty five" | 25 |
| "a hundred and five" | 105 |

Spoken digits are more reliable than expecting the recogniser to place a
decimal point itself.

**"twenty three twenty two" gives you 2322, not 23.22.** It is genuinely
ambiguous, so it is not guessed. Always say "point".

To buy at the current market price, leave the price out — *"buy one
yesbank"* prices against the live ask so it fills.

---

## When it goes wrong

**"Sorry, I didn't catch an instruction in that."**
The parser did not recognise the phrasing. Try simpler wording:
`buy <number> <symbol> at <price>`.

**"I couldn't find anything called ..."**
The recogniser heard the ticker differently. Check `voice/aliases.py` and add
the spelling it produced — YESBANK arrives as "yes bank" or "years bank"
depending on how you say it.

**"... is not on the allowlist"**
Working as intended. Only YESBANK is permitted by default. Edit `allowlist`
in `voice/safety.py` to widen it.

**"That order is worth N rupees, over the 100 rupee per-order limit"**
Also intended. Raise `max_order_value` in `voice/safety.py` when you are
ready.

**"... is an index, not a tradeable instrument"**
Nifty and Bank Nifty are numbers, not instruments. Trade an ETF like
NIFTYBEES, or futures and options if your account has F&O enabled.

**Recording never stops.** Your `SILENCE_RMS` is below the room's noise
floor. Re-run `voice.miccheck` and raise it.

**Recording cuts you off mid-sentence.** Raise `SILENCE_SECONDS` in
`voice/listen.py` rather than lowering the threshold — the problem is your
pauses between words, not the level.

---

## What it cannot do yet

- No spoken replies; answers print to screen.
- No cancelling or modifying by voice — use the broker's app or
  `shoonya.broker` directly.
- No live fill notifications; fills are checked once, shortly after placing.

See the Limitations section in the [README](README.md).
