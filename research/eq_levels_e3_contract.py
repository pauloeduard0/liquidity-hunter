"""E3.1 operational shadow contract, research-only."""

from __future__ import annotations

from math import isfinite

from research.eq_levels_e1_2 import ContractError


class ShadowContractError(ContractError):
    """A shadow report cannot support a safe rollout decision."""


def validate_shadow_report(report: dict, *, expected_charts: int) -> None:
    if type(expected_charts) is not int or expected_charts <= 0:
        raise ShadowContractError("invalid expected chart count")
    if not isinstance(report, dict) or report.get("schema") != 1:
        raise ShadowContractError("unsupported shadow report")
    rows = report.get("rows")
    if not isinstance(rows, list) or len(rows) != expected_charts:
        raise ShadowContractError("incomplete shadow panel")
    runtime = report.get("elapsed_seconds")
    if type(runtime) not in (int, float) or not isfinite(runtime) or runtime < 0:
        raise ShadowContractError("invalid shadow runtime")
    identities = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ShadowContractError("invalid chart row")
        identity = (row.get("symbol"), row.get("timeframe"))
        if any(not isinstance(value, str) or not value.strip() for value in identity):
            raise ShadowContractError("incomplete chart identity")
        if identity in identities:
            raise ShadowContractError("duplicate or incomplete chart identity")
        identities.add(identity)
        for field in (
            "snapshots_with_R_N_difference",
            "max_R_N_difference",
            "sum_R_N_difference",
        ):
            if type(row.get(field)) is not int or row[field] < 0:
                raise ShadowContractError("invalid shadow divergence")
        if (
            not isinstance(row.get("block_divergent_snapshots"), list)
            or len(row["block_divergent_snapshots"]) != 4
            or any(
                type(value) is not int or value < 0 for value in row["block_divergent_snapshots"]
            )
        ):
            raise ShadowContractError("invalid shadow block metrics")


def rollout_decision(report: dict, *, expected_charts: int, max_seconds: float) -> str:
    """Return a fail-closed operational decision, never an indicator decision."""
    if type(max_seconds) not in (int, float) or not isfinite(max_seconds) or max_seconds <= 0:
        raise ShadowContractError("invalid runtime budget")
    validate_shadow_report(report, expected_charts=expected_charts)
    if report["elapsed_seconds"] > max_seconds:
        return "rollback"
    if any(row["snapshots_with_R_N_difference"] for row in report["rows"]):
        return "shadow-only"
    return "eligible-for-review"
