#!/usr/bin/env python3
"""cost-governor — track token and tool spend per session and automation.

Aggregates usage records, prices them against a rate card **you** supply, and
flags budget breaches, runaway automations, statistical outliers, and runs that
consumed budget without producing anything.

This tool never invents a price. Models absent from the rate card are reported as
unpriced, with their token volume shown, and are excluded from currency totals.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from scoutkit import Finding, Report, Severity, read_json, read_jsonl  # noqa: E402
from scoutkit.cli import run  # noqa: E402
from scoutkit.io import EvidenceError  # noqa: E402

SKILL = "cost-governor"
TITLE = "Cost Governor — spend, budgets, and outliers"

DEFAULT_RATE_CARD = Path(__file__).resolve().parents[1] / "references" / "rate-card.json"

USAGE_FIELDS = ("input_tokens", "output_tokens", "tool_calls")


def _num(value: Any) -> float:
    """Coerce to a non-negative float; anything unusable becomes 0.0."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if result > 0 else 0.0


def load_usage(path: str) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.is_file():
        raise EvidenceError(f"no such file: {p}")
    records = read_jsonl(p) if p.suffix.lower() == ".jsonl" else read_json(p)
    if isinstance(records, dict):
        records = records.get("runs") or records.get("usage") or [records]
    if not isinstance(records, list) or not records:
        raise EvidenceError("usage evidence must be a non-empty list of run records")
    if not all(isinstance(r, dict) for r in records):
        raise EvidenceError("every usage record must be a JSON object")
    return records


def load_rate_card(path: str | None) -> dict[str, Any]:
    target = Path(path) if path else DEFAULT_RATE_CARD
    if not target.is_file():
        return {"currency": None, "rates": {}, "configured": False}
    card = read_json(target)
    if not isinstance(card, dict):
        raise EvidenceError("rate card must be a JSON object")
    rates = card.get("rates") or {}
    if not isinstance(rates, dict):
        raise EvidenceError("rate card 'rates' must be an object keyed by model name")
    priced = {
        model: spec for model, spec in rates.items()
        if isinstance(spec, dict)
        and isinstance(spec.get("input_per_million"), (int, float))
        and isinstance(spec.get("output_per_million"), (int, float))
    }
    return {"currency": card.get("currency"), "rates": priced, "configured": bool(priced)}


def price_run(record: dict[str, Any], rates: dict[str, Any]) -> tuple[float | None, str]:
    """Return (cost, model). Cost is None when the model has no configured rate."""
    model = str(record.get("model") or "<unspecified>")
    spec = rates.get(model)
    if not spec:
        return None, model
    cost = (
        _num(record.get("input_tokens")) / 1_000_000 * float(spec["input_per_million"])
        + _num(record.get("output_tokens")) / 1_000_000 * float(spec["output_per_million"])
    )
    return round(cost, 6), model


def _budget_findings(spend: float, budget: float, scope: str, report: Report, currency: str) -> None:
    if budget <= 0:
        return
    ratio = spend / budget
    if ratio >= 1.0:
        report.add(Finding(
            code="CG002", severity=Severity.CRITICAL, title=f"Budget exceeded: {scope}",
            detail=f"Spend {spend:.4f} {currency} against a budget of {budget:.4f} {currency} ({ratio:.0%}).",
            locator=scope,
            recommendation="Disable or throttle the highest consumers now, then re-baseline the budget deliberately.",
        ))
    elif ratio >= 0.8:
        report.add(Finding(
            code="CG003", severity=Severity.HIGH, title=f"Budget at risk: {scope}",
            detail=f"Spend {spend:.4f} {currency} is {ratio:.0%} of the {budget:.4f} {currency} budget.",
            locator=scope,
            recommendation="Review the top consumers before the remaining headroom is gone.",
        ))


def analyze(args: argparse.Namespace) -> Report:
    records = load_usage(args.input)
    card = load_rate_card(args.rate_card)
    rates = card["rates"]
    currency = card["currency"] or "units"
    report = Report(skill=SKILL, subject=Path(args.input).name)

    priced_total = 0.0
    unpriced_models: dict[str, int] = {}
    per_run: list[dict[str, Any]] = []
    by_scope: dict[str, dict[str, Any]] = {}

    for index, record in enumerate(records, start=1):
        cost, model = price_run(record, rates)
        scope = str(record.get("automation") or record.get("session") or "<unattributed>")
        tokens = _num(record.get("input_tokens")) + _num(record.get("output_tokens"))
        artifacts = record.get("artifacts") or []

        if cost is None:
            unpriced_models[model] = unpriced_models.get(model, 0) + 1
        else:
            priced_total += cost

        entry = {
            "run_id": record.get("run_id") or f"run-{index}",
            "scope": scope,
            "model": model,
            "tokens": tokens,
            "tool_calls": _num(record.get("tool_calls")),
            "cost": cost,
            "priced": cost is not None,
            "artifact_count": len(artifacts) if isinstance(artifacts, list) else 0,
            "duration_s": _num(record.get("duration_s")),
        }
        per_run.append(entry)

        bucket = by_scope.setdefault(scope, {"runs": 0, "tokens": 0.0, "cost": 0.0, "priced_runs": 0})
        bucket["runs"] += 1
        bucket["tokens"] += tokens
        if cost is not None:
            bucket["cost"] += cost
            bucket["priced_runs"] += 1

    if not card["configured"]:
        report.add(Finding(
            code="CG001", severity=Severity.HIGH, title="No rate card configured",
            detail="Every rate is unset, so spend is reported in tokens only and no budget can be enforced.",
            locator=str(args.rate_card or DEFAULT_RATE_CARD),
            recommendation="Fill in input_per_million and output_per_million for the models you use, "
                           "taking the numbers from your own billing page. This tool will not guess them.",
        ))

    if unpriced_models:
        report.add(Finding(
            code="CG005", severity=Severity.MEDIUM, title="Unpriced models in usage",
            detail=f"{sum(unpriced_models.values())} run(s) used models absent from the rate card: "
                   f"{', '.join(sorted(unpriced_models))}.",
            locator="rate-card",
            recommendation="Add these models to the rate card so their spend is counted.",
            metadata={"models": unpriced_models},
        ))

    # Outliers, measured in tokens so they work with or without a rate card.
    token_values = [r["tokens"] for r in per_run if r["tokens"] > 0]
    if len(token_values) >= 5:
        median = statistics.median(token_values)
        threshold = median * args.outlier_multiple
        outliers = [r for r in per_run if r["tokens"] > threshold]
        if outliers:
            worst = max(outliers, key=lambda r: r["tokens"])
            report.add(Finding(
                code="CG004", severity=Severity.MEDIUM, title="Outlier runs",
                detail=f"{len(outliers)} run(s) exceeded {args.outlier_multiple}x the median of "
                       f"{median:,.0f} tokens. Largest: {worst['run_id']} at {worst['tokens']:,.0f} tokens.",
                locator=worst["scope"],
                recommendation="Inspect the outlier prompts; a single unbounded run usually explains a spend spike.",
            ))

    # Concentration: one automation dominating total consumption.
    total_tokens = sum(r["tokens"] for r in per_run)
    if total_tokens > 0 and len(by_scope) > 1:
        top_scope, top_bucket = max(by_scope.items(), key=lambda kv: kv[1]["tokens"])
        share = top_bucket["tokens"] / total_tokens
        if share > args.concentration_threshold:
            report.add(Finding(
                code="CG006", severity=Severity.HIGH, title="Consumption concentrated in one automation",
                detail=f"'{top_scope}' accounts for {share:.0%} of all tokens across {top_bucket['runs']} run(s).",
                locator=top_scope,
                recommendation="Verify the cadence is warranted; a frequent schedule is the usual cause.",
            ))

    zero_yield = [r for r in per_run if r["tokens"] > 0 and r["artifact_count"] == 0]
    if zero_yield and len(zero_yield) >= max(2, len(per_run) // 4):
        report.add(Finding(
            code="CG007", severity=Severity.MEDIUM, title="Runs consuming budget without output",
            detail=f"{len(zero_yield)} of {len(per_run)} run(s) consumed tokens but recorded no artifacts.",
            locator="<multiple>",
            recommendation="Add an early exit for the no-work case so idle polls cost nothing.",
        ))

    if args.budget:
        _budget_findings(priced_total, args.budget, "total", report, currency)
    for scope, budget in _parse_scope_budgets(args.scope_budget):
        bucket = by_scope.get(scope)
        if bucket is None:
            report.note(f"Scope budget declared for '{scope}' but no runs matched it.")
            continue
        _budget_findings(bucket["cost"], budget, scope, report, currency)

    scopes = sorted(
        ({"scope": k, **{kk: (round(vv, 6) if isinstance(vv, float) else vv) for kk, vv in v.items()}}
         for k, v in by_scope.items()),
        key=lambda s: -s["tokens"],
    )

    report.sections = {"by_scope": scopes, "runs": per_run,
                       "unpriced_models": sorted(unpriced_models), "currency": currency}
    report.summary = {
        "runs": len(per_run),
        "scopes": len(by_scope),
        "total_tokens": int(total_tokens),
        "total_tool_calls": int(sum(r["tool_calls"] for r in per_run)),
        "priced_runs": sum(1 for r in per_run if r["priced"]),
        "unpriced_runs": sum(1 for r in per_run if not r["priced"]),
        "priced_spend": f"{priced_total:.4f} {currency}" if card["configured"] else "not priced",
    }
    if not card["configured"]:
        report.note("Currency totals are omitted because no rates are configured. Token counts are exact.")
    report.note("Costs are computed from your rate card only. No pricing is inferred or fetched.")
    report.decide_verdict()
    return report


def _parse_scope_budgets(pairs: list[str] | None) -> list[tuple[str, float]]:
    parsed: list[tuple[str, float]] = []
    for item in pairs or []:
        name, _, raw = item.rpartition("=")
        if not name:
            raise EvidenceError(f"--scope-budget expects NAME=AMOUNT, got {item!r}")
        try:
            parsed.append((name, float(raw)))
        except ValueError as exc:
            raise EvidenceError(f"--scope-budget amount must be numeric in {item!r}") from exc
    return parsed


def _extend(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--rate-card", help="Rate card JSON. Defaults to the skill's references/rate-card.json.")
    parser.add_argument("--budget", type=float, help="Total budget in the rate card's currency.")
    parser.add_argument("--scope-budget", nargs="*", default=[], metavar="NAME=AMOUNT",
                        help="Per-automation budgets, for example 'Daily Digest=1.50'.")
    parser.add_argument("--outlier-multiple", type=float, default=5.0,
                        help="Flag runs exceeding this multiple of the median token count (default: 5).")
    parser.add_argument("--concentration-threshold", type=float, default=0.5,
                        help="Flag a scope consuming more than this share of total tokens (default: 0.5).")


def main(argv: list[str] | None = None) -> int:
    return run(argv, skill=SKILL, title=TITLE, analyze=analyze, extend=_extend,
               description="Track token and tool spend per session and automation against your own rate card.")


if __name__ == "__main__":
    raise SystemExit(main())
