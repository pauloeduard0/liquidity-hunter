"""The measured universe, and the split between search and holdout.

Recorded here because it was not recorded before, and that cost a
reproduction. The block-reclaim study reported "43 symbols (search)" and
"30 symbols (held out)" and the two lists lived only in a shell history: the
numbers could be read but not re-derived, so a corrected rule could not be
compared against the old one on the same halves.

The split is a **deterministic hash of the symbol name**, so it is a property
of the symbol rather than of the order a run happened to take, reproduces on
any machine, and cannot be nudged after seeing a result. It is fixed from this
point: a symbol added to the universe later lands in whichever half its own
name puts it in, and never moves.

Note what this split can and cannot support. It is honest for the *corrected*
rule -- neither half was looked at while the block's lifetime and the R floor
were being fixed. It is not a fresh holdout for the `1.0 x ATR` threshold,
which was read off a curve on a set this one does not reproduce. That
limitation is the same one `docs/block_reclaim.md` already states, and it is
answered by time, not by symbols: `research/vwap_walkforward.py`.
"""

from __future__ import annotations

import hashlib

#: Every USDT perpetual the study has ever pulled at M15, in one place so a
#: run cannot silently measure a different universe than the one reported.
UNIVERSE: tuple[str, ...] = (
    "AAVEUSDT", "ADAUSDT", "ALGOUSDT", "ANKRUSDT", "APTUSDT", "ARBUSDT",
    "ATOMUSDT", "AVAXUSDT", "AXSUSDT", "BANDUSDT", "BATUSDT", "BNBUSDT",
    "BTCUSDT", "CELRUSDT", "CHZUSDT", "COMPUSDT", "CRVUSDT", "DASHUSDT",
    "DOGEUSDT", "DOTUSDT", "EGLDUSDT", "ENJUSDT", "EOSUSDT", "ETCUSDT",
    "ETHUSDT", "FILUSDT", "GALAUSDT", "GRTUSDT", "HBARUSDT", "ICPUSDT",
    "IMXUSDT", "INJUSDT", "IOSTUSDT", "IOTAUSDT", "KAVAUSDT", "KNCUSDT",
    "LDOUSDT", "LINKUSDT", "LRCUSDT", "LTCUSDT", "MANAUSDT", "MKRUSDT",
    "NEARUSDT", "NEOUSDT", "ONEUSDT", "ONTUSDT", "OPUSDT", "QTUMUSDT",
    "RUNEUSDT", "RVNUSDT", "SANDUSDT", "SEIUSDT", "SNXUSDT", "SOLUSDT",
    "STORJUSDT", "STXUSDT", "SUIUSDT", "SUSHIUSDT", "THETAUSDT", "TIAUSDT",
    "TRXUSDT", "UNIUSDT", "VETUSDT", "WAVESUSDT", "WLDUSDT", "XLMUSDT",
    "XRPUSDT", "XTZUSDT", "YFIUSDT", "ZECUSDT", "ZILUSDT", "ZRXUSDT",
)

#: Roughly the 43/30 proportion the original study reported.
_SEARCH_SHARE = 0.6


def _bucket(symbol: str) -> float:
    """A stable [0, 1) position for a symbol, independent of run order."""
    digest = hashlib.sha256(symbol.encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


SEARCH: tuple[str, ...] = tuple(s for s in UNIVERSE if _bucket(s) < _SEARCH_SHARE)
HOLDOUT: tuple[str, ...] = tuple(s for s in UNIVERSE if _bucket(s) >= _SEARCH_SHARE)


def sample_of(symbol: str) -> str:
    return "search" if _bucket(symbol) < _SEARCH_SHARE else "holdout"


if __name__ == "__main__":
    print(f"universe {len(UNIVERSE)}")
    print(f"search  ({len(SEARCH):>2}): {' '.join(SEARCH)}")
    print(f"holdout ({len(HOLDOUT):>2}): {' '.join(HOLDOUT)}")
