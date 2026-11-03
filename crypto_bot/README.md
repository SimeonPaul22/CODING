# Claude-assisted crypto spot trading bot

A Python bot that trades **spot only** (no leverage, futures or margin). Each cycle it:

1. fetches candles from the exchange (via [ccxt](https://github.com/ccxt/ccxt)),
2. computes RSI 14, EMA 20/50, MACD, ATR 14 and volume change **in Python**,
3. sends a compact summary to **Claude** (`claude-sonnet-5-5` by default) and asks for a JSON decision,
4. checks that decision against **hard risk rules in code** that Claude cannot override,
5. places, manages and closes trades only when every rule passes,
6. logs everything to SQLite and a rotating log file, and optionally alerts you on Telegram.

> **Read this first.** Trading bots lose money regularly, including well-built ones.
> Nothing here is financial advice. The defaults are deliberately tiny (10 USDT per trade).
> Run it in **paper** mode for weeks before you consider **live**, and only use money you can afford to lose.

---

## Contents
- [How it works](#how-it-works)
- [Project layout](#project-layout)
- [Setup (Linux and Windows)](#setup)
- [Getting exchange API keys (and testnet keys)](#exchange-keys)
- [Getting an Anthropic API key](#anthropic-key)
- [Running each mode](#running)
- [Reading the reports and logs](#reports)
- [Telegram alerts](#telegram)
- [Kill switch and shutdowns](#kill-switch)
- [Deploying on a cheap Linux VPS with systemd](#vps)
- [Safe choices I made for you](#choices)
- [Known limitations](#limitations)
- [Before going live checklist](#checklist)

---

<a name="how-it-works"></a>
## How it works

```
             every 30 s (monitor tick)                        once per closed candle
 ┌───────────────────────────────────────────┐      ┌─────────────────────────────────────────────┐
 │ STOP file? ──yes──> close positions, exit │      │ candles ─> indicators ─> snapshot (JSON)    │
 │ price ─> stop-loss / take-profit hit?     │      │ snapshot ─> Claude ─> JSON ─> pydantic      │
 │ equity ─> drawdown limit? daily loss?     │      │   (invalid / refusal / error / cap = HOLD)  │
 └───────────────────────────────────────────┘      │ decision ─> RiskManager ─> broker ─> order  │
                                                    │ everything ─> SQLite + log + Telegram       │
                                                    └─────────────────────────────────────────────┘
```

* **Decisions** happen once per *closed* candle (default every 15 min). The candle that is still forming is dropped, so indicators don't change from one second to the next.
* **Exits** are checked every `loop.monitor_interval_seconds` (30 s), whatever Claude says.
* **Claude's job** is only to judge the setup. Position size, stop-loss bounds, daily limits, drawdown and the kill switch all live in `risk/manager.py`.
* **Same code everywhere.** Backtest, paper and live use the same `Trader` and `RiskManager`. Only the *broker* changes: simulated fills (backtest/paper) or real orders (live).

### Risk rules (enforced in code)

| Rule | Default | Where |
|---|---|---|
| Fixed position size | 10 USDT | `risk.position_size_usdt` |
| Max open positions per symbol | 1 (cannot be raised) | `risk.max_open_positions_per_symbol` |
| Max new trades per UTC day | 3 | `risk.max_trades_per_day` |
| Minimum confidence to act | 0.7 | `risk.min_confidence` |
| Stop-loss / take-profit on every trade | 2% / 4% defaults; Claude's suggestion used only if inside 0.5–5% / 1–10% and TP ≥ SL | `risk.*_stop_loss_pct`, `risk.*_take_profit_pct` |
| Daily loss limit | 2% of capital → no new trades until 00:00 UTC | `risk.daily_loss_limit_pct` |
| Max drawdown | 10% below peak equity → bot shuts down, closes positions, alerts you | `risk.max_drawdown_pct` |
| Kill switch | create a file named `STOP` | `risk.kill_switch_file` |
| No withdrawal permission | startup refuses a key that can withdraw | `core/runner.py` |
| Spot only | `defaultType: spot`, futures symbols rejected, SELL without a position is ignored | `exchange/ccxt_client.py`, `config/settings.py` |

"Capital" (`risk.capital_usdt`, default 100) is the amount you allocate to the bot. Limits are measured against
capital plus the bot's own P&L, not your whole exchange account. In live mode the bot never spends more than that
allocation, even when the account holds more.

---

<a name="project-layout"></a>
## Project layout

```
crypto_bot/
├── main.py                 CLI: run | backtest | report | check | reset-shutdown
├── config.yaml             all settings (validated at startup)
├── .env.example            template for secrets -> copy to .env
├── requirements.txt        pinned dependencies
├── config/settings.py      pydantic models for config.yaml + .env
├── exchange/
│   ├── base.py             interface, MarketInfo (lot/tick/min size), retries, candle cleaning
│   ├── ccxt_client.py      Bybit / Binance / Bitget / KuCoin via ccxt (orders, partial fills, permission checks)
│   ├── quidax.py           EXPERIMENTAL Quidax adapter (not in ccxt)
│   ├── synthetic.py        offline fake market for demos/tests
│   └── factory.py
├── strategy/
│   ├── indicators.py       RSI, EMA, MACD, ATR, volume change, candle summary
│   ├── snapshot.py         what the decision engine sees
│   ├── decision.py         Decision schema + strict JSON parser (bad reply -> HOLD)
│   └── engines.py          rule-based stand-in for Claude
├── llm/
│   ├── prompts.py          system prompt (conservative, prefer HOLD)
│   └── claude.py           Anthropic SDK client, cost tracking, daily cap
├── risk/manager.py         all hard risk rules (pure functions)
├── core/
│   ├── trader.py           one cycle: exits -> limits -> decide -> risk -> order -> log
│   ├── broker.py           SimBroker (paper/backtest) and LiveBroker
│   ├── runner.py           paper/live loop, startup safety checks, live confirmation
│   └── report.py           performance report
├── storage/db.py           SQLite: cycles, positions, LLM usage, state
├── storage/logging_setup.py rotating log file
├── alerts/telegram.py      optional Telegram alerts
├── backtest/data.py        download + cache candles
├── backtest/engine.py      backtester
├── deploy/cryptobot.service systemd unit
└── tests/                  pytest suite (93 tests)
```

---

<a name="setup"></a>
## Setup

You need **Python 3.11 or newer** (tested on 3.13).

### Linux (Ubuntu/Debian)

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git
git clone <your repo url> && cd <repo>/crypto_bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then edit .env with nano/vim
python -m pytest              # should end with "93 passed"
```

### Windows 10/11

1. Install Python from <https://www.python.org/downloads/>. **Tick "Add python.exe to PATH"** in the installer.
2. Open **PowerShell** in the `crypto_bot` folder:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
copy .env.example .env             # then edit .env in Notepad
python -m pytest
```

Every command below is the same on Windows. Use `python` (or `py`) inside the activated virtual environment.

---

<a name="exchange-keys"></a>
## Getting exchange API keys (and testnet keys)

**Paper and backtest modes need no exchange keys.** You only need keys for live mode, or for `python main.py check` to verify permissions.

For **every** exchange:
* Enable **Read** and **Spot trading** only.
* **Never enable Withdrawals**, Transfers, Futures/Derivatives, Margin or Options. The bot refuses to start if it detects withdrawal permission. Where it can't check (Quidax, or an endpoint failure) it warns you loudly.
* Restrict the key to your VPS's IP address (IP whitelist) if the exchange supports it. A stolen key then can't be used from anywhere else.
* Put the values in `.env`: `EXCHANGE_API_KEY`, `EXCHANGE_API_SECRET`, plus `EXCHANGE_API_PASSWORD` (the passphrase) for Bitget and KuCoin.

| Exchange | Live keys | Testnet | Notes |
|---|---|---|---|
| **Bybit** (default) | bybit.com → Account → API → Create New Key → "System-generated" → permissions: *Read-Write*, tick **Spot → Trade** only | <https://testnet.bybit.com> (separate account; get test funds from the testnet faucet/asset page). Set `exchange.testnet: true`. | |
| **Binance** | binance.com → Profile → API Management → Create API → enable *Enable Spot & Margin Trading* (the bot never uses margin), leave withdrawals off | <https://testnet.binance.vision> (log in with GitHub, "Generate HMAC_SHA256 Key"). Set `exchange.testnet: true`. | |
| **Bitget** | bitget.com → API Management → Create → permissions: *Read*, *Spot trade* | no spot testnet in ccxt → use **paper mode** | needs passphrase |
| **KuCoin** | kucoin.com → API Management → Create API → *General* + *Spot Trading* | no sandbox in ccxt → use **paper mode** | needs passphrase |
| **Quidax** | quidax.com → Settings → API keys | none | **Experimental** adapter. ccxt doesn't support Quidax, so `exchange/quidax.py` was written against Quidax's v1 REST docs. **It is untested against the real API.** Live trading is blocked until you set `exchange.quidax_live_acknowledged: true` after checking the endpoints yourself. Put the secret key in `EXCHANGE_API_SECRET`. Override the base URL with `QUIDAX_BASE_URL` if needed. |

### Notes for Nigeria 🇳🇬

* **Check that the exchange currently serves Nigerian residents** and that your KYC is complete. Availability and rules have changed in recent years. Binance, for example, restricted some services for Nigeria in 2024. The SEC Nigeria now regulates digital-asset exchanges, so prefer a licensed or clearly permitted platform, and re-check the rules before going live.
* The bot trades **USDT pairs**. How you get money in and out (P2P, bank transfer to a local exchange like Quidax, and so on) is outside the bot. Never give the bot a key with withdrawal rights for that purpose.
* Choose a VPS region the exchange allows. Some exchanges block certain countries' IPs (Binance.com blocks US IPs, for example). European regions such as Frankfurt or Amsterdam are usually fine. Check your exchange's terms.
* Keep records of every trade for tax purposes. The SQLite database has them all (see [reports](#reports)).

---

<a name="anthropic-key"></a>
## Getting an Anthropic API key

1. Go to <https://console.anthropic.com>, sign up, and add billing (prepaid credits are fine).
2. **Settings → API Keys → Create Key**. Copy it into `.env` as `ANTHROPIC_API_KEY=...`.
3. Strongly recommended: set a **monthly spend limit** in the Console (Settings → Limits). That limit sits on top of the bot's own `llm.daily_cost_cap_usd`.

**What it costs.** One call sends about 1,800 input tokens and gets back roughly 150–600 output tokens (the JSON plus the model's internal reasoning, which is billed as output). At Claude Sonnet 5.5 prices ($2 per million input tokens, $10 per million output) that is about **$0.005–0.01 per call**. With a 15-minute timeframe and one symbol that is up to 96 calls a day, roughly **$0.50–1.00/day**. The default cap of **$1.00/day** stops calls when reached; the bot then treats every cycle as HOLD, and stop-loss/take-profit exits keep working. Use a longer timeframe (`1h`) or `llm.effort: low` (the default) to spend less. The cost estimate uses the prices in `config.yaml`, so update them if Anthropic's pricing changes.

Request details (in `llm/claude.py`):
* Official `anthropic` SDK, model from `llm.model`, `output_config.effort` from `llm.effort`.
* **Structured output** (`llm.structured_output: true`) asks the API to constrain the reply to the decision JSON schema. The reply is **still** validated by pydantic.
* **Refusal fallback** (`llm.refusal_fallback: true`): Claude's safety systems can occasionally decline a request. With this on, the API retries the request on a fallback model inside the same call (Anthropic's `server-side-fallback` beta, `fallbacks: "default"`). Set it to `false` if you don't want that. A refusal that still happens becomes HOLD.
* If the API rejects either optional feature (HTTP 400), the bot logs a warning, switches both off for the session, and retries once without them.

---

<a name="running"></a>
## Running each mode

```bash
python main.py check        # validates config, prints prices/lot sizes, checks key permissions
python main.py --help
```

### 1. Backtest (no keys needed)

```bash
python main.py backtest                                  # uses backtest.* in config.yaml (rule engine)
python main.py backtest --start 2025-01-01 --end 2025-03-31 --symbol ETH/USDT
python main.py backtest --offline                        # synthetic candles, no internet (plumbing test only)
python main.py backtest --engine claude                  # REAL Claude calls on the last N candles only
```

* Candles are downloaded through ccxt (public endpoints) and cached in `data/cache/*.csv`. Re-runs only download what's missing.
* `backtest.engine: rules` uses the free rule-based stand-in (EMA trend + MACD + RSI). Use it to test risk logic and plumbing at zero API cost.
* `--engine claude` runs real API calls on the last `backtest.claude_sample_candles` (default 30) candles; earlier candles are indicator history only. The daily cost cap still applies. **Caveat:** Claude may "know" what happened in historical markets, so a Claude backtest on past data can look better than reality. Treat it as a sanity check of the prompt and parsing, not as proof of an edge. Paper trading is the honest test.
* Fees (`backtest.fee_pct`, 0.1%) and slippage (`backtest.slippage_pct`, 0.05%) apply to every entry and exit. Stops are checked on each candle's high/low. If a candle touches both stop and target, the backtest assumes the **stop** was hit. If the price gaps past the stop, the fill uses the (worse) open price.
* Output: a report in the terminal, the equity curve in `data/backtest_equity.csv`, every cycle/trade in `data/bot_backtest.db`.

### 2. Paper (default; live prices, fake money)

```bash
python main.py run                      # mode: paper is the default in config.yaml
python main.py run --engine rules       # paper trade without Claude (free)
python main.py run --offline --engine rules --iterations 5 --interval 5    # offline dry run
```

* Uses public market data only. Keys are not even loaded in paper mode, so it can't place a real order.
* The fake balance starts at `risk.capital_usdt` and lives in `data/bot_paper.db`. Delete that file to start over.
* Stop with **Ctrl+C**. It finishes the current tick and exits. Open paper positions are remembered for next time.

### 3. Live (real money)

All three locks are required:
1. `mode: live` in `config.yaml` (it can't be set from the command line),
2. `live_trading_enabled: true` in `config.yaml`,
3. a confirmation at startup: type `I_UNDERSTAND_THE_RISKS` at the prompt. When there is no terminal (systemd), the bot instead requires `CONFIRM_LIVE_TRADING=I_UNDERSTAND_THE_RISKS` in `.env`.

At startup in live mode the bot also: checks key permissions (refuses withdrawal-enabled keys), shows your free USDT, and checks that the coins for any remembered open positions are still in the account.
Live results go to `data/bot_live.db`, separate from paper.

Recommended path: **testnet** (Bybit/Binance with `exchange.testnet: true`) → small live size → normal size.

---

<a name="reports"></a>
## Reading the reports and logs

```bash
python main.py report                 # current mode (paper by default)
python main.py report --mode live
python main.py report --mode backtest
```

Example output (illustrative numbers, not a real result):

```
=== Report (paper) ===
Closed trades      : 12  (open: 1)
Wins / losses      : 5 / 7   win rate 41.67%
Total P&L          : +0.3120 USDT  (+0.312% of capital)
Avg win / avg loss : +0.3500 / -0.2054 USDT
Profit factor      : 1.217
Fees paid          : 0.2410 USDT
Max drawdown       : 0.880%
Exit reasons       : {'stop_loss': 6, 'take_profit': 4, 'signal_sell': 2}
Claude calls/cost  : 410 / $2.3100
Buy & hold BTC/USDT: +3.100% (61200 -> 63100)  -> buy&hold did better
```

* **Win rate** alone means little. With TP at 2× SL you can be profitable at a 40% win rate. Look at **profit factor** (gross wins ÷ gross losses; above 1 is profitable) and **total P&L after fees**.
* **Max drawdown**: the worst fall from a peak. That is the pain you'd have sat through.
* **Buy & hold**: what you'd have made by buying with the whole capital at the start and doing nothing. If the bot can't beat this over a long period, it isn't adding value.
* **Claude cost**: compare it to P&L. A bot that makes $0.30 while spending $2.31 on API calls is losing money.

**Logs** go to `logs/bot.log`, rotating at 5 MB with 5 old files kept. Every cycle's full detail is in SQLite. You can browse it with [DB Browser for SQLite](https://sqlitebrowser.org/) or the `sqlite3` CLI:

```sql
-- what Claude said and what the risk manager did
SELECT ts, symbol, last_price, json_extract(decision_json,'$.action') AS action,
       json_extract(decision_json,'$.confidence') AS conf, parse_error,
       json_extract(risk_json,'$.reasons') AS risk
FROM cycles ORDER BY id DESC LIMIT 20;

SELECT * FROM positions ORDER BY id DESC;      -- every trade with entry/exit/fees/P&L
SELECT * FROM llm_usage;                       -- tokens and estimated $ per day
SELECT * FROM events;                          -- critical events (shutdowns)
```

---

<a name="telegram"></a>
## Telegram alerts (optional)

1. In Telegram, message **@BotFather** → `/newbot` → copy the token into `TELEGRAM_BOT_TOKEN`.
2. Send any message to your new bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy `"chat":{"id": ...}` into `TELEGRAM_CHAT_ID`.
3. Set `alerts.telegram_enabled: true`.

You get alerts for trades (buy/sell with P&L), errors (rate-limited to one per error type per 15 min), the daily summary at the UTC day change, daily-loss halts, shutdowns and the kill switch. If Telegram is unreachable the bot carries on.

---

<a name="kill-switch"></a>
## Kill switch and shutdowns

* **Kill switch:** create a file named `STOP` in the `crypto_bot` folder (`touch STOP` on Linux, `ni STOP` in PowerShell). Within about a second the bot halts. By default (`risk.kill_switch_action: close_and_exit`) it sells the bot's open positions at market, then exits. Use `exit_only` to leave positions untouched; they are then **unprotected**, since stops are managed by the bot. The bot won't start again while `STOP` exists.
* **Daily loss limit:** no new entries until 00:00 UTC. Open positions keep their stop-loss and take-profit.
* **Max drawdown:** the bot closes positions (`risk.close_positions_on_shutdown`), records the shutdown in the database, alerts you, and **refuses to start** until you review and run `python main.py reset-shutdown`.

---

<a name="vps"></a>
## Deploying on a cheap Linux VPS with systemd

Any $4–6/month VPS (1 vCPU, 1 GB RAM, Ubuntu 22.04/24.04) is plenty, from Hetzner, DigitalOcean, Vultr, Contabo or similar. Pick a region your exchange allows (see [Nigeria notes](#exchange-keys)).

```bash
# 1. As root: basic hardening and a dedicated user
apt update && apt upgrade -y
apt install -y python3 python3-venv git ufw
ufw allow OpenSSH && ufw enable
adduser --disabled-password --gecos "" bot
timedatectl set-timezone UTC          # the bot uses UTC anyway; this keeps logs consistent

# 2. As the bot user: install
su - bot
git clone <your repo url> repo && ln -s ~/repo/crypto_bot ~/crypto_bot
cd ~/crypto_bot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && chmod 600 .env && nano .env
.venv/bin/python -m pytest
.venv/bin/python main.py check
exit

# 3. As root: install the service
cp /home/bot/crypto_bot/deploy/cryptobot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now cryptobot        # start now and on every boot
systemctl status cryptobot
journalctl -u cryptobot -f              # live logs (also in ~/crypto_bot/logs/bot.log)
```

* `Restart=on-failure` restarts the bot 30 s after a crash. Deliberate stops are **not** restarted (`RestartPreventExitStatus=2 3 4`): drawdown shutdown, `STOP` file, or a refused start such as a missing live confirmation. Those need a human.
* Stop/restart: `systemctl stop cryptobot` / `systemctl restart cryptobot`. Stopping sends SIGTERM; the bot finishes its tick and exits. Positions stay open and are remembered.
* For **live** mode under systemd, set `CONFIRM_LIVE_TRADING=I_UNDERSTAND_THE_RISKS` in `.env` (there's no terminal for the prompt).
* Kill switch on the VPS: `touch /home/bot/crypto_bot/STOP`.
* Update: `su - bot -c 'cd ~/repo && git pull'` then `systemctl restart cryptobot`.
* Back up `data/` (the SQLite databases) now and then.

---

<a name="choices"></a>
## Safe choices I made for you

Where the spec left room, I picked the safer option:

1. **Stop-loss/take-profit are managed by the bot**, not placed as exchange orders. Spot stop/OCO orders differ a lot between exchanges and ccxt doesn't unify them reliably. The bot checks prices every 30 s and sells at market. Consequence: **if the bot is down, positions have no stop.** That's why it runs under systemd with auto-restart, why the kill switch closes positions by default, and why positions are small.
2. **Market orders** for entries and exits, to keep the fill logic simple. A market buy is placed by USDT cost when the exchange supports it. With 10 USDT on liquid pairs, slippage is negligible. Avoid illiquid pairs.
3. **Order placement is never blindly retried.** After a network error the bot looks the order up by its own client order id instead, so it can't buy twice. If the status stays unknown, it alerts you to check manually.
4. **Partial fills:** the bot waits up to 20 s, cancels any unfilled remainder, and records only what filled. A partly filled sell keeps the rest open as its own position. Tiny unsellable leftovers ("dust") are written off in P&L.
5. **Fees paid in the coin** (as Bybit does on spot buys) reduce the recorded position size, and sells never exceed your free balance.
6. **SELL means "close my position".** Spot only means no shorting, so a SELL without a position is ignored. "Max trades per day" counts **new positions**; closing one isn't a new trade.
7. **The daily loss limit counts realised P&L today plus unrealised P&L** on open positions (conservative), as a percentage of `capital_usdt`. Drawdown is measured from the highest equity seen.
8. **The daily cost cap is checked before each call,** so the last call of a day can overshoot it by one call (about a cent).
9. **Kill switch default = close positions and exit**, because the stops die with the bot.
10. **Paper mode never loads exchange keys.** Live mode needs three separate confirmations.
11. **Separate databases per mode** (`bot_paper.db`, `bot_live.db`, `bot_backtest.db`) so test results never mix with real ones.
12. **Max open positions per symbol is fixed at 1** in the config validator, as specified.
13. **Quidax live trading is blocked by default**, because the adapter couldn't be tested against the real API.

<a name="limitations"></a>
## Known limitations

* **Verified offline only.** The exchange APIs and the Anthropic API were not reachable from the environment where this was built. The test suite (93 tests) and a paper dry run passed against an offline synthetic market and a mocked Claude client. Your first run of `python main.py check` and a paper session are therefore the first contact with Bybit/Binance/Claude. Watch the logs closely.
* The rule-based engine is a baseline for testing, not a profitable strategy.
* Claude's judgement isn't backtestable without bias (see the backtest caveat above).
* The bot uses ccxt's built-in rate limiter. Running several bots on one API key can still hit exchange limits.

---

<a name="checklist"></a>
## Before going live checklist

- [ ] `python -m pytest` passes on the machine that will trade.
- [ ] Backtested on at least 3–6 months of real candles for your symbol and timeframe, and you understand the result, including how it compares with buy & hold.
- [ ] Ran **paper mode for at least 2–4 weeks**, read `python main.py report`, and checked the API cost against P&L.
- [ ] Ran on **testnet** (Bybit/Binance) and watched at least one full trade: buy → stop-loss or take-profit → sell.
- [ ] Exchange API key has **Read + Spot trade only**: **withdrawals OFF**, futures/margin off, **IP whitelist** set to the VPS IP. `python main.py check` shows no withdrawal permission.
- [ ] `.env` is `chmod 600` and **not** committed to git (it's in `.gitignore`).
- [ ] An Anthropic Console spend limit is set, and `llm.daily_cost_cap_usd` suits you.
- [ ] `risk.capital_usdt` equals the money you actually move to the exchange for the bot, and that's money you can afford to lose.
- [ ] Started with the smallest size the exchange accepts (check `min_cost` in `python main.py check`).
- [ ] Telegram alerts work (you received the "Bot started" message).
- [ ] You know how to stop it: `touch STOP`, or `systemctl stop cryptobot`.
- [ ] systemd service enabled, and you've tested a restart (`systemctl restart cryptobot`) while a paper position was open.
- [ ] The exchange is legally available to you in Nigeria and your KYC is done. You keep records for tax.
- [ ] Finally: `mode: live`, `live_trading_enabled: true`, confirmation typed (or `CONFIRM_LIVE_TRADING` set for systemd). Watch the first day closely.
