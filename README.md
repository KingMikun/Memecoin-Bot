# King Analytics — Wallet Intelligence Bot

Charts show you what already happened. Wallets show you what's about to.

This bot watches labeled wallets across **Ethereum, Solana, Base, and Robinhood Chain**,
flags confluence — multiple smart wallets buying the same token in a tight window —
and only alerts you once the token clears a honeypot/rug check. No mcap or FDV filter.
If it ticks the boxes, it ticks the boxes.

## What it does

1. **Tracks wallets you label** — `/addwallet`, `/wallets`, `/label`, `/untrack`. EVM wallets
   auto-track across Ethereum, Base, and Robinhood Chain from a single add.
2. **Ingests trades in real time** via Helius (Solana) and Alchemy (EVM: Ethereum, Base,
   Robinhood Chain) webhooks — push-based, not polling
3. **Prices every trade at ingest** via a live DexScreener lookup, converting raw on-chain
   quantity into an actual USD value — the foundation for real PnL, not quantity math
   dressed up as dollars
4. **Notifies on every tracked-wallet trade** — buy or sell — with contract address and
   chart/explorer links, separate from the confluence alert
5. **Scores confluence** — wallet count, historical win rate, liquidity health — size-agnostic
6. **Gates every alert through GoPlus Security** — honeypot, mint/freeze authority, sell tax,
   holder concentration. Fails the check → no alert, full stop.
7. **Pushes a clean, branded alert to Telegram** the moment a token clears both bars
8. **`/wallethistory`** — trade history, FIFO-matched realized PnL, and win rate for any
   tracked wallet, on demand — includes quantity bought/sold and market cap at entry
   and exit for each closed position, not just a dollar total

## Why Robinhood Chain is in here

Robinhood Chain went live July 1, 2026 — an Arbitrum-based, EVM-compatible L2.
Early memecoin activity is already showing up on it, and because it's EVM-standard,
it plugs into the same Alchemy/GoPlus pipeline as Ethereum and Base with almost no
extra code. That's the whole point of catching a chain early — the tooling catches up,
you're already positioned.

## Setup

```bash
git clone <this repo>
cd king_wallet_bot
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
```

### Keys you need
| Service | What for | Get it |
|---|---|---|
| Telegram Bot Token | the bot itself | @BotFather on Telegram |
| Helius API key | Solana webhooks | dev.helius.xyz |
| Alchemy API key | Ethereum/Base/Robinhood Chain webhooks | alchemy.com |
| GoPlus API key + secret | honeypot/rug checks | gopluslabs.io (free tier available) |

### Run locally
```bash
uvicorn main:app --reload
```
Talk to your bot on Telegram, run `/addwallet <address> <chain> <label>`.

### Deploy to Railway
1. Push this repo to GitHub, connect it to a new Railway project
2. Add a Postgres plugin — Railway injects `DATABASE_URL` automatically, the bot
   upgrades from SQLite to Postgres with zero code changes
3. Set the env vars from `.env.example` in Railway's dashboard
4. Set `PUBLIC_BASE_URL` to the Railway-assigned domain — this now also
   controls how Telegram updates are delivered (see below), not just the
   chain webhooks.

### Telegram: webhook mode, not polling — and why that matters

The bot uses a Telegram **webhook** in production (Telegram POSTs updates to
`{PUBLIC_BASE_URL}/webhook/telegram`), falling back to polling only when
`PUBLIC_BASE_URL` is unset — i.e. local dev with `uvicorn main:app --reload`.

This isn't a style choice — polling on a platform like Railway is fragile:
Telegram only allows **one** process to hold a `getUpdates` long-poll
connection per bot token at a time. Any overlap — a previous deploy's
container not fully stopped, an accidental second replica, a local test run
left running against the same token — throws
`telegram.error.Conflict: terminated by other getUpdates request`, which
crash-loops the whole app. That crash loop is what silently eats commands:
the bot is only actually up in the gaps between restarts. Webhook mode has
no such contention — Telegram just POSTs to a URL, and whichever container
is up handles it. No conflict is possible, no crash loop, no dropped commands.

**If you ever see that Conflict error in Railway logs:** it means something
is still polling with the same token. Check Railway's replica count (should
be 1), confirm no stale deployment is still alive, and confirm nobody's
running the bot locally against the same `TELEGRAM_BOT_TOKEN`.
5. Deploy — Railway reads the `Procfile` automatically

### Wiring up webhooks — now automatic
`/addwallet`, `/importwallets`, and `/untrack` sync straight to Helius/Alchemy —
no manual dashboard step per wallet. What you set up **once**, not per wallet:

1. Create one webhook in the Helius dashboard (type: `Enhanced`, tx type: `SWAP`,
   at least one placeholder address), copy its ID into `HELIUS_WEBHOOK_ID`
2. Create one Address Activity webhook per EVM chain in Alchemy's dashboard,
   pointed at `{PUBLIC_BASE_URL}/webhook/evm`, copy each ID into
   `ALCHEMY_WEBHOOK_ID_ETHEREUM` / `_BASE` / `_ROBINHOOD`
3. Grab your Alchemy Notify auth token (Dashboard → Settings → Auth Token,
   *different* from your API key) into `ALCHEMY_AUTH_TOKEN`

After that, every `/addwallet` PATCHes the right webhook automatically, and
`/untrack` pulls it back off. If a sync call fails (bad token, rate limit),
the bot tells you in the reply rather than failing silently — the wallet is
still saved locally, it just won't receive live trades until you re-run
`/addwallet` once the credentials are fixed.

## Project structure
```
main.py                 FastAPI + Telegram bot, single-process entrypoint
config.py                all env vars and chain config in one place
database.py              Wallet / Trade / Alert / Subscriber models
bot/handlers.py          /addwallet /wallets /label /untrack /stats /wallethistory
ingest/helius_webhook.py Solana trade ingestion + live pricing
ingest/evm_webhook.py    Ethereum / Base / Robinhood Chain trade ingestion + live pricing
scoring/confluence.py    size-agnostic opportunity scoring
scoring/security.py      GoPlus honeypot/rug gate
scoring/wallet_stats.py  FIFO-matched trade history, realized PnL, win rate
utils/price.py           live USD price lookup (DexScreener) used at ingest time
alerts/notifier.py       per-trade notifications + confluence alerts
```

## Tuning the filter
Everything's in `.env` — confluence wallet minimum, time window, max holder
concentration, min liquidity. No mcap/FDV cutoff by design; if you want one later,
add it in `scoring/confluence.py::score_token()`.

## Seeding wallets from real leaderboards

**Solana — automated:**
```bash
python -m ingest.discovery --min-trades 10 --top 15
```
Pulls KOLscan's live leaderboard, filters out low-trade-count noise, adds the rest
straight to your DB labeled `KOLscan: <name>`. Re-run daily (or wire into a
Railway cron / APScheduler job) to keep the bench current.

**Bulk manual add (any chain):**
```
/importwallets
<address>, <chain>, <label>
<address>, <chain>, <label>
```

**Ethereum / Base — no free global leaderboard exists.** Nansen's Smart Money
API (docs.nansen.ai) is the real tool here but it's paid. The free workaround
is per-token: pull the "Top Traders" list on Birdeye/DexScreener for tokens
you already have conviction on — see `seed_evm_from_birdeye_top_traders()` in
`ingest/discovery.py` for a stub to wire up once you have a Birdeye key.

## Upgrading an already-deployed bot — read this before redeploying

`init_db()` uses SQLAlchemy's `create_all()`, which only creates tables that
don't exist yet — it does **not** add new columns to a table that's already
live in your Railway Postgres. Recent updates added `token_amount` and
`entry_mcap` to the `trades` table. If your bot was already running before
those were added, every trade insert/query will now throw a silent
"column does not exist" error — which, before this update, showed the user
nothing at all (fixed now with a global error handler, but you still need
to fix the schema).

Run this once against your Railway Postgres (Railway dashboard → Postgres
plugin → Connect → run via `psql` or the Query tab) before redeploying:

```sql
ALTER TABLE trades ADD COLUMN IF NOT EXISTS token_amount FLOAT DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_mcap FLOAT;
```

If you're still on the SQLite default (no Postgres plugin added yet), skip
this — SQLite gets rebuilt fresh on each deploy in most Railway setups, so
there's no stale schema to migrate.

## Not finding a command? Check this first

If a command you just added (like `/wallethistory`) gets no response at
all — not even an error — the two most likely causes, in order:
1. **The latest `bot/telegram_bot.py` and `bot/handlers.py` haven't been
   redeployed yet** — Telegram silently ignores commands with no registered
   handler. Check `/help` on your live bot; if it's missing, redeploy.
2. **The schema migration above hasn't been run** — every Trade-related
   command errors out. You'll now get a "⚠️ Something went wrong" reply
   instead of silence, and the real error will be in Railway's logs.

## Not financial advice
The bot flags patterns, not guarantees. GoPlus and every honeypot detector will
occasionally miss something new — treat every alert as a lead to verify, not an
auto-buy signal.
