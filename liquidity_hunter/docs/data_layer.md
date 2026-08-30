# Data layer

Extracted from `CLAUDE.md` (2026-08-29) to keep that file under its size limit.

### Data layer (`liquidity_hunter/data`)

- **`data/providers/base.py`** — `OHLCVProvider`, the abstract port all
  market data sources implement (`get_ohlcv(symbol, timeframe, limit) -> list[Candle]`).
- **`data/providers/binance.py`** — `BinanceDataProvider`, a CCXT-backed
  implementation for Binance. `to_ccxt_symbol()` converts concatenated
  symbols (e.g. `"BTCUSDT"`) to CCXT's unified `"BASE/QUOTE"` form. Candles
  are fetched via ccxt's implicit `publicGetKlines` (raw Binance
  `/api/v3/klines`, 12 columns) rather than `fetch_ohlcv`, since only the
  raw response includes taker buy base asset volume (column index 9),
  needed to populate `Candle.taker_buy_volume`.
- **`data/retry.py`** — `retry_with_backoff` decorator (exponential backoff,
  logged) used to retry transient `ccxt.NetworkError`s.
- **`data/exceptions.py`** — `DataProviderConnectionError` (retries
  exhausted) and `DataProviderRequestError` (non-retryable, e.g. invalid
  symbol), both subclasses of `DataProviderError`.

- **`data/providers/base.py`** — also defines `FuturesDataProvider`, the
  abstract port for perpetual-futures market state
  (`get_open_interest_history`, `get_funding_rate_history`,
  `get_long_short_ratio`), a sibling to `OHLCVProvider`.
- **`data/providers/binance_futures.py`** — `BinanceFuturesDataProvider`, a
  ccxt `binanceusdm`-backed implementation. Open interest and funding use
  ccxt's unified `fetch_open_interest_history`/`fetch_funding_rate_history`
  (against the swap symbol, e.g. `"BTC/USDT:USDT"`); the crowd long/short
  account ratio uses the implicit `fapiDataGetGlobalLongShortAccountRatio`
  (raw `BTCUSDT` symbol). `TimeFrame` maps to Binance's fixed futures-data
  periods (`_FUTURES_PERIOD`). Same `retry_with_backoff` + error translation
  as `BinanceDataProvider`. `get_open_interest_history` **paginates** past
  Binance's 500-row per-request cap when `limit > 500` (paging forward with
  `since`, de-duplicated by timestamp), clamped to Binance's ~30-day OI
  retention with a 1-hour safety margin inside the boundary (a `startTime`
  at exactly −30d is rejected with error -1130).

- **`data/providers/binance_futures_ohlcv.py`** — `BinanceFuturesOHLCVProvider`,
  an `OHLCVProvider` (sibling to `BinanceDataProvider`) that fetches **candles**
  from Binance USDT-M perpetual futures via ccxt `binanceusdm`'s implicit
  `fapiPublicGetKlines` (raw `/fapi/v1/klines`, same 12-column layout as spot,
  so `Candle.taker_buy_volume` at column 9 is still populated). Preferred over
  spot because the candles align with the futures-derived analysis already
  overlaid on the chart (OI/funding/long-short/liquidation map) and reflect
  leveraged flow. Its `max_fetch_limit` is **1500** (vs spot's 1000), so one
  request covers a larger window. Symbols with no perpetual contract raise
  `DataProviderRequestError`.
- **`data/providers/geckoterminal.py`** — `GeckoTerminalDataProvider`, an
  `OHLCVProvider` for **on-chain (DEX) pairs** — memecoins in particular, which
  no exchange lists. Symbols are `"<network>:<address>"` (e.g.
  `"solana:Ge87…pump"`) or a bare address; a *token* address resolves to its
  deepest pool by USD reserve (liquidity is fragmented across dozens of pools,
  so "the chart" is the deepest one), a *pool* address is used directly. Both
  resolutions are cached per symbol. `denomination` (`PriceDenomination`,
  default **`MARKET_CAP`**) scales the USD price by the token's supply — a
  constant factor, so every structural reading is identical to the USD chart
  and only the axis changes; `USD` and `QUOTE` (priced in the pool's quote
  token) are the alternatives. M30 and W1 have no native GeckoTerminal period
  and are **resampled** (`_RESAMPLED_FROM`) from M15 pairs / D1 weeks (Monday
  00:00 UTC buckets), so the whole M5→W1 ladder works; one upstream request
  then yields N times fewer bars there. `max_fetch_limit` is 1000.
  Two source-imposed gaps, both degrading gracefully rather than being faked:
  an on-chain OHLCV row has **no taker split**, so `taker_buy_volume` is half
  the candle's volume and `volume_delta` reads a flat zero (CVD, VSA,
  `MarketControlAnalyzer`, the profile's delta colouring all go quiet — a
  "green candle = 60% buying" proxy would feed invented flow to layers that
  read it as measured); and there is **no futures state** for a pool, so
  `_fetch_futures_state` skips the fetch outright for an on-chain symbol
  (`oi_analysis=None`, `liquidation_map=None`, `market_control=None`).
  Volume is USD, not base units.
  **Rate limiting is the operational constraint** (measured 2026-08-20): the
  free tier's per-IP budget is small, and a 429 body points at CoinGecko's paid
  plans. Its CDN, though, caches a response for 60s (`s-maxage=60`) and an edge
  `HIT` never reaches the limiter — so the fix is to *not repeat requests*, not
  to retry harder. Four mechanisms, module-scoped because
  `default_ohlcv_provider()` builds a fresh provider per API call:
  a **response cache** by URL (`_RESPONSE_CACHE_TTL_SECONDS` = 50, just inside
  the CDN window), **single-flight** locks so concurrent callers of one URL
  fetch it once, a **metadata cache** for pool resolution and token supply
  (`_METADATA_TTL_SECONDS` = 1h — properties of the asset, not of a request),
  and a **global cooldown** opened by any 429 (`_COOLDOWN_AFTER_429_SECONDS` =
  8) so every thread stands down together instead of each retrying into the
  same spent limiter. `clear_caches()` resets all of them (tests). On top,
  requests are spaced process-wide (2.2s) and retried on a 7-rung ladder, and
  the higher-timeframe run in `load_dashboard_data` degrades to "aligned" on a
  `DataProviderError` rather than failing the snapshot. The API layer widens
  its TTLs for on-chain symbols (`dashboard._ONCHAIN_TTL_SECONDS` = 60,
  `overview._ONCHAIN_MIN_SNAPSHOT_TTL_SECONDS` = 180): polling faster than the
  source's own cache window cannot return anything new, it only buys 429s.
  Measured end to end on the Jimothy pool: cold dashboard **54s → 6.9s**, a
  repeat and a timeframe switch effectively free (both reuse the ladder's
  cached URLs), cold 7-timeframe overview **3m40 → 90s**.
- **`data/providers/routing.py`** — `RoutingOHLCVProvider(exchange, onchain)`
  plus `is_onchain_symbol`: sends `<network>:<address>` symbols and bare
  base58/0x addresses to the on-chain source and everything else to the
  exchange chain, capping each request to its own source's limit (the
  `FallbackOHLCVProvider` shape). Every leaf of that tree is wrapped in a
  `CachingOHLCVProvider` (below) in `default_ohlcv_provider()`.
- **`data/providers/fallback.py`** — `FallbackOHLCVProvider(primary, secondary)`:
  an `OHLCVProvider` that tries `primary` and falls back to `secondary` on
  `DataProviderRequestError` (e.g. a symbol with no perpetual), clamping the
  fallback request to the secondary's `max_fetch_limit`; connection errors
  propagate. `max_fetch_limit` follows the primary. `load_dashboard_data`'s
  default provider is `FallbackOHLCVProvider(BinanceFuturesOHLCVProvider(),
  BinanceDataProvider())` (futures candles, spot fallback for spot-only pairs).

- **`data/providers/caching.py`** — `CachingOHLCVProvider(inner, store, …)`:
  serves candles from a persistent store and asks `inner` only for the tail it
  is missing. The sources answer *"the last N candles"* and nothing narrower,
  so a refresh that gains one bar re-downloads the other 999 — the whole cost
  model on GeckoTerminal, where the binding constraint is a per-IP request
  budget. The mechanisms already in that provider cache **requests** (by URL,
  50s, in memory); this one caches **history**, which is sound because a closed
  candle is immutable.
  `get_ohlcv` reads the store's last timestamp, sizes the fetch from the
  elapsed bars plus `_OVERLAP_CANDLES` = 3, and splices stored history in front
  of the fresh segment. Three consequences: a cold start after a restart is no
  longer cold; history accumulates **past the source's per-request cap** just by
  running (the window slides forward, old bars stay), which is why the on-chain
  leg is wired with `max_fetch_limit=_ONCHAIN_CACHED_FETCH_LIMIT` = 1500 over
  GeckoTerminal's own 1000; and the request that does go out is small.
  Two invariants carry the correctness:
  - **The live edge is never stored.** The newest candle is still forming, and
    freezing a partial bar into a series the detectors read as closed is not
    cosmetic — `_reanchor_bos_close_break` confirms a BOS on a *close*. The
    store therefore ends at the last closed bar, the next refresh's tail covers
    it, and the upsert replaces it with its final print.
  - **A gap discards stored history rather than splicing it.** If the fresh
    segment does not reach back into what is stored (a long outage), the two
    are served *not* joined: a discontinuity the detectors cannot see is worse
    than a shorter window.
  Keyed by `inner.series_key(symbol)`, not by the symbol — see the port note
  below. Wrapped **per leaf** rather than around `FallbackOHLCVProvider`, since
  only the leaf knows whether the perpetual or the spot book answered.
  Measured 2026-08-28 on the M5→W1 ladder at 1500 candles: **Binance BTCUSDT
  3.5s → 1.9s** warm (request *count* unchanged at 7, but each is the tail
  instead of 1500 bars — the saving is klines weight, the resource the ban
  incident spent); on-chain the store is what makes the deep window reachable
  at all.
- **`data/repositories/candle_store.py`** — `SQLiteCandleStore`, the archive
  behind it. SQLite because the access pattern is a range query on
  `(series, timeframe, timestamp)` and the writers are threads (the prefetch
  pool, the overview ladder); WAL, one connection per thread. `save` upserts
  (the re-fetched bar is the authority), `load` returns the newest `limit`
  rows oldest-first with an optional `before` bound, and stamps them with the
  caller's `symbol` since the rows are keyed by series. `default_candle_store_path()`
  is `~/.cache/liquidity-hunter/candles.sqlite3`.

The `OHLCVProvider` port carries a `max_fetch_limit` class attribute (default
1000, the per-request candle cap) that callers read instead of assuming a fixed
limit; **`series_key(symbol)`** names the identity of the series a symbol
resolves to, for persistence — the symbol is not always that identity. A
GeckoTerminal *token* address resolves to whichever pool is deepest by USD
reserve, and liquidity migrates, so one symbol can name two different charts
weeks apart (`gt:<network>:<pool>:<denomination>`, the denomination included
because `MARKET_CAP` scales every price by supply); Binance spot and perpetual
are different books under one ticker (`binance-spot:` / `binance-futures:`).
The default is the symbol itself; `klines_row_to_candle` (in `binance.py`) is the shared 12-column row →
`Candle` parser used by both the spot and futures providers.

`BinanceDataProvider`, `BinanceFuturesOHLCVProvider`, `FallbackOHLCVProvider`,
`OHLCVProvider`, `BinanceFuturesDataProvider`, `FuturesDataProvider`,
`GeckoTerminalDataProvider`, `PriceDenomination`, `RoutingOHLCVProvider`,
`CachingOHLCVProvider`, `SQLiteCandleStore`, `default_candle_store_path`, and
`is_onchain_symbol` are re-exported from `liquidity_hunter.data`.

