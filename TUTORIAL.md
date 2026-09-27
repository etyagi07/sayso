# Tutorial: from clone to your first voice trade

About 20 minutes, most of it one-time setup. Each step says what you should
see, so you can tell working from broken. **Nothing up to Step 6 can place
an order.**

---

## Before you start

- **A Mac or Windows PC** with a microphone. Apple Silicon Macs are fastest.
- **Python 3.12 or newer.** Check with `python3 --version`.
- **A Shoonya account with API access**, and an API app registered in it.
  From the registration you need a **client ID**, your **user ID** and a
  **secret code** (shown once — copy it then).
- **For options:** the F&O segments enabled on your account — NFO for Nifty
  and Bank Nifty, BFO for Sensex. Stocks work without them.

---

## Step 1 — Install

```bash
git clone https://github.com/<you>/sayso.git
cd sayso
./setup.sh                       # Windows: powershell -ExecutionPolicy Bypass -File setup.ps1
```

It finds Python, builds a virtual environment and installs everything. Expect
a couple of minutes. It ends by listing the next steps.

---

## Step 2 — Measure your microphone

```bash
.venv/bin/python -m voice.calibrate
```

Stay quiet for 3 seconds, then say "buy one call at market" a few times for
5 seconds. It measures your room and your voice and puts the "you've stopped
talking" line between them:

```
room floor : 0.0108
your speech: 0.0852
Calibrated. threshold 0.0269
```

**Reads all zeros?** Your operating system is blocking the microphone. On
macOS: System Settings → Privacy & Security → Microphone → allow your
terminal, then **quit and reopen the terminal completely** — the permission
is only read at launch. On Windows: Settings → Privacy & security →
Microphone.

**Says the silence was too loud?** Something was talking or humming nearby.
Run it again somewhere quieter.

---

## Step 3 — Log in

Once per trading day:

```bash
.venv/bin/python -m shoonya.login
```

It first asks for your three API values, from your API app registration on
shoonya.com:

```
Client ID:    ABC123_U        ← usually your user ID plus a suffix
User ID:      ABC123          ← no suffix
Secret code:                  ← hidden
```

**The secret code shows nothing as you type or paste — that is normal.**
Paste it once and press Enter. If paste doesn't seem to work, press Enter on
an empty line and it offers to show the text instead. If you paste it twice
by accident, it notices and offers to fix it.

**These are never saved.** They are used once for this login and dropped,
so you type them each trading day. The only thing kept is the day's
session, which expires on its own, in a file only you can read.

Then your browser opens Shoonya's login page. Log in there with your password and
OTP — they go to Shoonya, never to this program.

You'll land on a page that says **"This site can't be reached"**. That is
expected. The login code is in the address bar:

![The redirect page after login, with the code in the address bar](docs/oauth-redirect.png)

Copy the whole address and paste it at the prompt. You should see:

```
Logged in as ABC123 (account ABC123). Session cached in .session.json.
```

---

## Step 4 — Check everything

```bash
.venv/bin/python -m voice.doctor
```

Each line is `ok`, `note` (works, but limited) or `FAIL` (needs fixing, with
the fix written underneath). You want it to end with **ready**. If your
account has no F&O, the NFO and BFO lines will say `note` — stocks still
work.

---

## Step 5 — Talk to it

```bash
.venv/bin/python -m voice.main
```

The first run downloads the speech model (a few hundred MB, once). Then:

```
account: ABC123 (default)
options: NIFTY 10 lots · BANKNIFTY 3 lots · SENSEX 10 lots
● READY - press Enter to speak · 't' to type · ctrl-c to quit
```

Press **Enter**, wait for **RECORDING**, speak, then pause. It stops by
itself. Press `t` instead to type a command. If it is still talking when
you press Enter, it stops at once so you can speak.

Every result starts with a sound, so you can keep your eyes on the chart.
Three to learn: **Glass** filled, **Tink** waiting to fill, **Basso**
rejected. Anything else — part filled, unknown, a question, or nothing
done — is **Pop**: listen to the words. To hear them once, each followed by
its meaning:

```bash
.venv/bin/python -m voice.speak
```

Start with things that can't trade:

```
what is the nifty call at
what is reliance at
funds
what do I own
```

You'll hear the answers read out as well as see them.

---

## Step 6 — Your first order

> From here, pressing `y` sends a **real** order.

The cheapest way to try the whole thing is one share of Yes Bank, about ₹23:

> *"buy one yes bank"*

It asks:

> *"Intraday or delivery?"*

Say *"intraday"*. It reads the order back and shows it:

```
┌─ CONFIRM ─────────────────────────────────
│  BUY (intraday)  1 x YESBANK-EQ
│  Yes Bank
│  at market  22.52   (bid 22.49 / ask 22.50)
│  total    22.52 rupees
└───────────────────────────────────────────
  y send · p set price · anything else cancels:
```

**Read it before pressing anything.** Press any key other than `y` the first
time, just to see it cancel. When you do press `y`, you'll hear a sound and:

> *"Filled. Bought 1 Yes Bank at 22.50."*

Then close it:

> *"sell my yes bank"*

---

## Options

Name the index. If you don't, it asks which.

> *"buy nifty call"* — the nearest weekly, at the money
>
> *"buy bank nifty put fifty five six hundred"* — a specific strike
>
> *"buy 2 lots of sensex call seventy four thousand"*
>
> *"exit call"* — closes the call you hold; asks which if you hold two

Say strikes the way traders do — "twenty three fifty", "fifty five six
hundred", or digit by digit, "two three one zero zero". It only accepts
strikes that are actually listed, and asks when a number could mean two.

**Bank Nifty has no weekly expiry.** It trades the nearest monthly, and the
readback says "monthly" so it's never a surprise.

---

## When it asks you something

It asks rather than guessing. Answer in a word:

| It asks | You say |
|---|---|
| Which index — Nifty, Bank Nifty or Sensex? | "bank nifty" |
| Intraday or delivery? | "delivery" |
| How many shares? | "ten" |
| You hold the 23100 call and the 23150 call. Which strike? | "twenty three one hundred" |

Say anything else and the question is dropped. It also expires after 30
seconds, so a half-finished order can't be completed by accident later.

---

## Changing your mind

- *"buy call, no wait, put"* — buys the put. The last thing you said wins.
- *"don't buy a call"* — does nothing.
- *"should I buy Reliance?"* — treated as a question, not an order.
- *"cancel"* or *"never mind"* — drops whatever it was asking.

---

## Which stocks

It knows the Nifty 50, plus Yes Bank for cheap testing. If you say a name
that could be two companies — "hdfc", "tata", "bajaj" — it asks which you
mean. Anything else it says it doesn't know, rather than guessing.

---

## More than one account

```bash
.venv/bin/python -m shoonya.login --account work
.venv/bin/python -m voice.main --account work
```

Each account has its own login session and daily limits. The account
in use is shown at the top when you start.

---

## When something goes wrong

**"I didn't catch an instruction in that."** Try simpler words:
*buy / sell / exit*, the index or company, *call / put*.

**"I don't know ___."** That stock isn't in the list. Add it (above).

**"… isn't a listed strike near …"** The number didn't match a real
contract. Say the full strike, or say it digit by digit.

**"… exceeds the cap of … lots"** or **"… over the … per-order limit"** —
a safety limit, working as intended. They're in `voice/safety.py`.

**"I couldn't reach the broker, so I haven't done anything."** Usually an
expired login — run `python -m shoonya.login` again.

**"I can't confirm that order went through."** The connection dropped after
sending. **Check your order book in the Shoonya app before trying again** —
the order may be live.

**Recording never stops, or cuts you off.** Run `voice.calibrate` again in
the room you'll use it in.

**Anything else:** run `python -m voice.doctor` and read the lines marked
FAIL.
