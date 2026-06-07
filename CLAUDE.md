# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

BIST (Borsa Istanbul) Discord bot with two independent entry points: (1) a news bot (`main.py`) that polls RSS feeds + KAP disclosures, scores news relevance by ticker/keyword matching, deduplicates, and pushes high-scoring items to a Discord **webhook**; (2) an interactive gateway bot (`bot.py`) serving the `/hisse` slash command (price + candlestick chart + indicators). All comments, log messages, and the README are in Turkish — keep that convention when editing.

## Commands

```bash
pip install -r requirements.txt
python main.py             # haber botu — requires DISCORD_WEBHOOK_URL in .env
python bot.py              # /hisse komut botu — requires DISCORD_BOT_TOKEN in .env
python charting.py THYAO 1a  # grafik debug: _chart_THYAO.png yazar, Discord gerekmez
```

No tests, linter, or build system. Runtime config lives in `.env` (see README table): `POLL_INTERVAL_SECONDS`, `MIN_RELEVANCE_SCORE`, `ENABLE_KAP`, `ENABLE_PRICE`, `RSS_FEEDS`, `RUN_ONCE` (single cycle then exit — used by GitHub Actions), `SEEN_DB_PATH`.

An empty `seen.db` triggers a **silent first round**: items are marked seen but not sent (prevents a spam burst on fresh deploys or cache misses). So deleting `seen.db` does NOT cause re-sends — it causes one quiet cycle instead. Rows older than `RETENTION_DAYS` (60, in store.py) are pruned on startup, so the db never grows unbounded; the dedup data is deliberately disposable (a remote DB was considered and rejected — single instance, self-healing loss).

Deployment: `.github/workflows/bot.yml` runs the bot on a ~10-min cron via `RUN_ONCE=1`, carrying `seen.db` between runs with `actions/cache` (immutable keys + `restore-keys` prefix trick). `DISCORD_WEBHOOK_URL` comes from repo Actions secrets; `ENABLE_KAP=0` there because KAP's WAF blocks datacenter IPs (it blocks home IPs too as of June 2026 — see `fetch_kap`'s warn-once handling). Deploying = pushing to GitHub; the next cron tick picks up the new code. Keep workflow action versions on Node 24-capable majors (checkout@v5, setup-python@v6, cache@v5).

## Architecture

Single asyncio polling loop in `main.py` runs the pipeline every `POLL_INTERVAL_SECONDS`:

```
sources.fetch_rss / fetch_kap  →  filters.evaluate (score)  →  store.SeenStore (dedup)
                               →  inference.infer (rule-based impact)  →  notifier.enrich_prices (optional)
                               →  notifier.build_embed + send (Discord)
```

- **`filters.py` owns the shared `NewsItem` dataclass** — `sources.py` imports it from there, so `filters` must stay import-free of `sources` to avoid a cycle. Sources fill the raw fields; `evaluate()` mutates the item in place to fill `tickers`, `score`, `category`, `matched_keywords`.
- **Scoring** = ticker bonus (`_TICKER_BONUS` per matched ticker, capped at `_TICKER_BONUS_CAP`) + summed `KEYWORD_WEIGHTS`. Items below `MIN_RELEVANCE_SCORE` are still marked seen (so noise isn't re-evaluated) but not sent. The dominant keyword category picks the embed color (`CATEGORY_COLORS`).
- **Turkish-aware matching is mandatory**: `textnorm.fold()` lowercases and ASCII-folds Turkish characters (Python's `.lower()` mishandles `İ`). All keyword patterns and ticker aliases are folded at build time and matched against folded text. Any new matching logic must go through `fold()`; never compare raw text. Patterns in `SENTIMENT_PATTERNS` (inference.py) are therefore written in ASCII form ("temettu", not "temettü").
- **`inference.py`** produces a rule-based impact estimate (`Inference`: direction pozitif/negatif/karisik/belirsiz + matched reason labels + expected targets). Targets are the matched tickers, or "BIST geneli" for ticker-less makro news. Returns `None` when there's nothing to show. Rendered in the embed as "📌 Olası Etki (tahmini)".
- **Dedup** keys on `NewsItem.uid` (RSS entry id/link, or `kap-<index>`), stored in SQLite `seen.db` via `store.SeenStore`.
- **`sources._clean()` strips HTML tags AND iteratively unescapes entities** — some feeds double-encode (`&amp;#039;` → `&#039;` → `'`), so it loops until stable. Applied to both title and summary. Don't bypass it when adding fields from feed entries.
- **KAP source is currently dead**: KAP's new Next.js site put `kap.org.tr/tr/api/disclosures` behind a Citrix WAF that blocks bots (timeouts / custom "666" error pages — verified June 2026; not bypassable with headers/cookies). `fetch_kap` warns once then drops to DEBUG (`_kap_uyarildi` module flag), and logs recovery if access returns. Dead sources (RSS or KAP) are always skipped, never crash the bot.
- **`notifier.py`** handles Discord 429 rate limits with retry, and fetches prices via `yfinance` (blocking, so wrapped in `asyncio.to_thread`) with a 60s in-memory cache. `_parse_published()` normalizes RSS/KAP date strings (RFC 2822, ISO 8601, epoch ms, `dd.mm.yyyy HH:MM`) into the embed's `timestamp` field so Discord renders local time; unparseable dates fall back to raw text in the footer. Naive datetimes are assumed Turkey time (UTC+3).
- **Embed is deliberately minimal** (user asked for readability): no score/category fields (color still encodes category), footer is source-only, date/time via the native `timestamp` field, tickers listed inside the "Olası Etki" field — a separate price field appears only when prices were actually fetched. Don't re-add metadata fields.

### `/hisse` command bot (second entry point, independent of the news pipeline)

`bot.py` is a discord.py 2.x **gateway** client (always-on process, `DISCORD_BOT_TOKEN`), entirely separate from the webhook news loop — it must NOT be wired into `main.py` or the Actions cron (a 10-min wake/exit cycle can't answer interactions; hosting decision deferred, runs locally for now). It imports `textnorm`/`tickers` read-only.

```
/hisse kod:<autocomplete> periyot:<1 Hafta|1 Ay>  →  market.fetch_history (2y daily)
   →  market.compute_indicators (SMA200/MACD/RSI, full series)  →  market.slice_window (5/22 bars)
   →  market.get_overview (price, volume, floatShares)  →  charting.render_chart (PNG BytesIO)
   →  embed + attachment://grafik.png in ONE followup.send
```

- **`market.py`** — all functions are blocking (yfinance/pandas); `bot.py` wraps every call in `asyncio.to_thread` (same pattern as `notifier.enrich_prices`). Indicators are plain pandas (deliberate: no TA-Lib/pandas-ta dep) computed on the FULL ~2y series, sliced to the display window AFTER — never compute on the slice. `fast_info` keys are camelCase (`lastPrice`); `get_info()` (floatShares/marketCap, slow + rate-limited) sits behind a 6h in-memory cache (`_info_cache`) and degrades to `None` fields on failure — the command never dies on missing info. Invalid tickers return an EMPTY DataFrame (no exception): gate on `market.is_valid`.
- **`charting.py`** — `matplotlib.use("Agg")` MUST precede any pyplot/mplfinance import. 4 panels (candles+SMA200 / volume / MACD / RSI); every `make_addplot` passes `secondary_y=False` or mplfinance silently moves series to a right-hand axis (bit us: RSI 30/70 guides landed on their own scale). `python charting.py KOD 1h|1a` is the chart-aesthetics debug loop (`_chart_*.png`, gitignored).
- **`bot.py`** — `Intents.none()` + `guilds=True` only (no privileged intents). `GUILD_ID` env set → instant per-guild command sync (dev); empty → global sync (~1h propagation). Every code path after `defer()` must end in `followup.send` or the interaction hangs. Autocomplete searches TICKERS codes AND aliases via `fold()`; codes outside TICKERS are accepted (BIST ~600 vs 99 listed) and validated by data presence; company-name input falls back to `find_tickers()`. `fmt.py` formats all numbers Turkish-style (`1.234.567,89`, `Mr`/`Mn` TL) — never emit raw `f"{x:,}"`.

## Extension points (as designed)

- New stocks: `TICKERS` dict in `tickers.py` (code → folded aliases), or bulk-load via `load_from_csv()`. Aliases are matched by substring on folded text — pick aliases carefully to avoid false positives (see the `"bim "` trailing-space trick).
- Scoring tweaks: `KEYWORD_WEIGHTS` in `filters.py` — `(keyword, weight, category)` tuples; keywords are written in normal Turkish and folded automatically.
- Impact inference: `SENTIMENT_PATTERNS` in `inference.py` — `(folded_regex, direction, weight, label)` tuples; weight 0 entries only contribute a reason label without steering direction.
- LLM classification: `filters.llm_classify()` is a deliberate stub for a future Anthropic API call; rule-based scoring/inference is the default path (user explicitly chose rule-based over LLM for inference).
- New source types: add an async fetcher in `sources.py` returning `list[NewsItem]` and call it from `run_once()` in `main.py`.

## Decisions already made (don't re-propose)

- Rule-based inference over LLM (cost); LLM remains a stub.
- Keyword-only passes stay: news with no BIST ticker match can still be sent on keyword score alone (e.g. foreign-company "halka arz" news). A require-ticker filter was offered and declined.
- No remote DB for dedup: single instance + disposable data + silent first round make SQLite-in-cache sufficient.
- `/hisse` is ONE slash command with a `kod` parameter, not per-ticker commands (Discord caps global slash commands at 100; BIST has ~600 tickers).
- Command bot is NOT hosted on GitHub Actions (cron can't serve interactions; 24/7 Actions jobs are a ToS gray area). Runs locally for now; hosting (Oracle Free Tier / VPS / chained Actions) deliberately deferred.
- Single `requirements.txt` for both entry points (Actions needlessly installs discord.py/matplotlib for the news bot, but pip cache makes it cheap; a split file was considered and rejected for simplicity).
