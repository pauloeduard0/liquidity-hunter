# Architecture

`liquidity_hunter` is a research platform for market liquidity detection and
market psychology analysis. It is **not** a trading system: it produces no
buy/sell signals and contains no order execution or strategy logic.

## Layering

Dependencies flow inward only — outer layers may depend on inner layers,
never the reverse.

```
        app
         │
 ┌───────┼────────────┐
 │       │            │
liquidity  psychology │
 │       │            │
 indicators           │
 │       │            │
 └───►  data ◄────────┘
         │
        core (domain)
```

| Layer        | Responsibility                                                                 | Depends on                          |
|--------------|---------------------------------------------------------------------------------|--------------------------------------|
| `core`       | Framework-agnostic domain entities (`Candle`, `LiquidityZone`, `MarketStructure`, `RetailBias`) and shared enums | nothing |
| `data`       | Market data acquisition, repositories, persistence adapters                    | `core`                                |
| `indicators` | Stateless derived series computed from `Candle` data                           | `core`, `data`                        |
| `liquidity`  | Detection/modeling of `LiquidityZone` and `MarketStructure`                     | `core`, `data`, `indicators`          |
| `psychology` | Modeling of `RetailBias` from sentiment/positioning data                        | `core`, `data`                        |
| `scoring`    | Composite, descriptive scoring combining `liquidity` and `psychology` output    | `core`, `liquidity`, `psychology`     |
| `app`        | Composition root and orchestration                                              | all of the above                      |
| `dashboard`  | Presentation/visualization of `app` output                                      | `app`, `core`                         |
| `config`     | Application settings (environment-driven)                                      | nothing                               |

## Domain entities

All domain entities live in `liquidity_hunter.core.domain`, are implemented
as immutable Pydantic models (`DomainModel`), and describe *observations*
about a market rather than decisions:

- **`Candle`** — a single OHLCV price bar.
- **`LiquidityZone`** — a price region identified as holding resting
  liquidity (e.g. equal highs/lows, order blocks, fair value gaps).
- **`MarketStructure`** — a discrete structural observation (e.g. break of
  structure, change of character) with a directional bias.
- **`RetailBias`** — a measurement of retail participant sentiment or
  positioning from a given source.

## SOLID notes

- **Single Responsibility**: each domain entity and each layer package has
  one reason to change.
- **Open/Closed**: new zone types, structure events, and bias sources are
  added via the enums in `core.domain.enums` without modifying model logic.
- **Liskov Substitution**: all domain entities share the `DomainModel` base
  and are interchangeable wherever a Pydantic model is expected.
- **Interface Segregation**: layers expose only what downstream layers need
  via their package `__init__`.
- **Dependency Inversion**: higher layers (`scoring`, `app`, `dashboard`)
  depend on `core` abstractions, not on concrete implementations in `data`.
