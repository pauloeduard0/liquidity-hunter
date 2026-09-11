"""E1 independent counterexamples, native parity, prefix and checkpoint invariants."""

import copy
import json

import pytest
from liquidity_hunter.core.domain import Candle
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle
from research.eq_levels_e1 import ROOT, Replay
from research.test_eq_levels_audit import series


def replay(c):
    engine = Replay()
    for bar in c:
        engine.feed(bar, verify=True)
    return engine


def eqh(engine):
    return [r for r in engine.records.values() if r["side"] == "EQH"]


def test_first_publication_waits_for_five_closed_right_bars():
    c = series((102.0, 102.0, 102.0))
    before = replay(c[:45])
    assert eqh(before) == []
    before.feed(c[45], closed=False)
    assert len(before.candles) == 45 and eqh(before) == []
    before.feed(c[45])
    r = eqh(before)[0]
    assert r["known_at"] == 45
    assert r["formed_at"] == c[40].timestamp.isoformat()
    assert r["known_time"] == c[45].timestamp.isoformat()


def test_future_volatility_alone_cannot_publish_N_cluster():
    c = series()
    c += [make_candle(i, high=101.0, low=90.0, close=100.0) for i in range(60, 120)]
    run = replay(c)
    assert len(run.raw["EQH"]) == 1
    assert eqh(run) == []
    assert run.diagnostics["EQH_R_changes_without_pivot"] > 0


def test_future_volume_changes_R_not_published_strength():
    c = series((102.0, 102.0, 102.0))
    run = replay(c)
    record = copy.deepcopy(eqh(run)[0])
    strength = next(iter(run.normal["EQH"].values())).strength
    for i in range(60, 80):
        run.feed(make_candle(i, high=100.1, low=99.9, close=100.0, volume=1000.0), verify=True)
    assert eqh(run)[0] == record
    assert next(iter(run.normal["EQH"].values())).strength == strength
    assert next(iter(run.raw["EQH"].values())).strength < strength


def test_growth_gets_version_without_erasing_consumed_parent():
    c = series((102.0, 102.01, 102.02, 102.03), count=70)
    run = replay(c)
    old, new = eqh(run)
    assert old["version"] in new["parents"]
    assert old["known_at"] == 45 and new["known_at"] == 60
    assert new["inherited_consumption"] == 55
    assert run.snapshots[-1]["sides"]["EQH"]["N"] == 1
    assert run.snapshots[-1]["sides"]["EQH"]["L"] == 0
    assert old["formed_at"] == c[40].timestamp.isoformat()
    assert any(e["kind"] == "sweep" and e["version"] == old["version"] for e in run.events)


def test_wick_then_close_are_separate_observed_events():
    c = series((102.0, 102.0, 102.0))
    c += [
        make_candle(60, high=103.0, low=99.0, close=101.0),
        make_candle(61, high=104.0, low=100.0, close=103.0),
    ]
    run = replay(c)
    vid = eqh(run)[0]["version"]
    life = [e for e in run.events if e["version"] == vid and e["kind"] in ["sweep", "breach"]]
    assert [e["kind"] for e in life] == ["sweep", "breach"]
    assert [e["observed_at"] for e in life] == [
        c[60].timestamp.isoformat(),
        c[61].timestamp.isoformat(),
    ]


def test_appending_future_preserves_publication_and_visibility_history():
    c = series((102.0, 102.01, 102.02, 102.03), count=70)
    short = replay(c[:50])
    full = replay(c)
    assert short.snapshots == full.snapshots[:50]
    assert short.events == [
        e for e in full.events if e["observed_at"] <= c[49].timestamp.isoformat()
    ]
    assert all(full.records[k] == v for k, v in short.records.items())


def test_checkpoint_roundtrip_resumes_with_rolling_delivery_without_origin_loss():
    c = series((102.0, 102.01, 102.02, 102.03), count=70)
    original = replay(c[:50])
    saved = json.loads(json.dumps(original.checkpoint()))
    resumed = Replay.restore(saved)
    # Transport now sends only new candles; origin survives in checkpoint.
    for bar in c[50:]:
        resumed.feed(bar)
    whole = replay(c)
    assert resumed.records == whole.records
    assert resumed.events == whole.events
    assert resumed.snapshots == whole.snapshots
    assert replay(c[50:]).snapshots[-1]["sides"]["EQH"]["N"] == 0
    assert whole.snapshots[-1]["sides"]["EQH"]["N"] == 1


def test_open_bar_revisions_do_not_mutate_state():
    engine = replay(series((102.0, 102.0, 102.0)))
    before = copy.deepcopy(engine.__dict__)
    engine.feed(make_candle(60, high=104.0, low=99.0, close=103.0), closed=False)
    engine.feed(make_candle(60, high=104.0, low=99.0, close=101.0), closed=False)
    assert engine.__dict__ == before


def test_duplicate_closed_delivery_is_rejected():
    engine = replay(series((102.0, 102.0, 102.0)))
    with pytest.raises(ValueError, match="strictly increasing"):
        engine.feed(engine.candles[-1])


@pytest.mark.parametrize("tf", ["15m", "1h", "4h"])
def test_native_parity_on_every_closed_bar_of_real_BTC_prefix(tf):
    path = ROOT / "frontend/research/fixtures" / f"BTCUSDT_{tf}.json"
    if not path.exists():
        pytest.skip("local E0 fixture not installed")
    data = json.loads(path.read_text())
    run = replay([Candle.model_validate(c) for c in data["candles"][:180]])
    assert run.diagnostics["native_parity_checks"] == 360


def test_historical_sweep_discovered_at_publication_is_not_backdated_knowledge():
    c = series()
    c += [make_candle(i, high=101.0, low=90.0, close=100.0) for i in range(60, 100)]
    c[65] = make_candle(65, high=110.0, low=90.0, close=105.0)
    run = replay(c)
    record = next(r for r in eqh(run) if r["formed_at"] == c[40].timestamp.isoformat())
    assert record["known_at"] == 70
    assert record["raw_invalidated_at"] == c[65].timestamp.isoformat()
    event = next(
        e for e in run.events if e["kind"] == "sweep" and e["version"] == record["version"]
    )
    assert event["observed_at"] == c[70].timestamp.isoformat()
    assert event["market_at"] == c[65].timestamp.isoformat()
    assert event["discovered"] is True
    assert not any(e["arm"] == "N" and e["version"] == record["version"] for e in run.sweeps)


def test_simultaneous_sweeps_use_common_prebar_eligibility():
    engine = replay(series((102.0, 102.0, 102.0)))
    key_a, zone = next(iter(engine.normal["EQH"].items()))
    key_b = tuple(engine.candles[i].timestamp.isoformat() for i in [11, 26, 41])
    engine.union("EQH", key_a + key_b)
    second = zone.model_copy(update={"price_low": 103.0, "price_high": 103.0})
    engine.candles.append(make_candle(60, high=104.0, low=99.0, close=101.0))
    engine.index[engine.candles[-1].timestamp] = 60
    reverse = copy.deepcopy(engine)
    engine.apply_lifecycle("EQH", {key_a: zone, key_b: second}, "N")
    reverse.apply_lifecycle("EQH", {key_b: second, key_a: zone}, "N")
    assert all(e["eligible_L"] for e in engine.sweeps if e["index"] == 60)
    assert all(e["eligible_L"] for e in reverse.sweeps if e["index"] == 60)
