"""Tests for cost-governor."""

from __future__ import annotations

import json

import pytest

import cost_governor as cg
from scoutkit.io import EvidenceError


def usage(run_id, automation="Alpha", model="m-a", inp=1000, out=200, tools=2, artifacts=("a",)):
    return {"run_id": run_id, "automation": automation, "model": model,
            "input_tokens": inp, "output_tokens": out, "tool_calls": tools,
            "artifacts": list(artifacts), "duration_s": 10}


def analyze(records, tmp_path, *, rate_card=None, budget=None, scope_budget=(),
            outlier=5.0, concentration=0.5):
    path = tmp_path / "u.json"
    path.write_text(json.dumps(records), encoding="utf-8")
    card_path = None
    if rate_card is not None:
        card_path = tmp_path / "rates.json"
        card_path.write_text(json.dumps(rate_card), encoding="utf-8")
    return cg.analyze(cg.argparse.Namespace(
        input=str(path), rate_card=str(card_path) if card_path else None,
        budget=budget, scope_budget=list(scope_budget),
        outlier_multiple=outlier, concentration_threshold=concentration,
    ))


def codes(report) -> set[str]:
    return {f.code for f in report.findings}


PRICED = {"currency": "USD", "rates": {"m-a": {"input_per_million": 3.0, "output_per_million": 15.0}}}


class TestPricingHonesty:
    def test_shipped_rate_card_is_deliberately_empty(self):
        card = cg.load_rate_card(None)
        assert card["configured"] is False
        assert card["rates"] == {}

    def test_unconfigured_card_raises_the_governance_finding(self, tmp_path):
        report = analyze([usage("r1")], tmp_path)
        assert "CG001" in codes(report)
        assert report.summary["priced_spend"] == "not priced"

    def test_no_currency_total_is_invented(self, tmp_path):
        report = analyze([usage("r1")], tmp_path)
        assert "not priced" in str(report.summary["priced_spend"])
        assert report.summary["total_tokens"] == 1200

    def test_unpriced_model_is_reported_not_estimated(self, tmp_path):
        report = analyze([usage("r1", model="unknown-model")], tmp_path, rate_card=PRICED)
        assert "CG005" in codes(report)
        assert report.summary["unpriced_runs"] == 1
        assert report.sections["runs"][0]["cost"] is None

    def test_partial_rate_entry_is_ignored(self, tmp_path):
        card = {"currency": "USD", "rates": {"m-a": {"input_per_million": 3.0}}}
        report = analyze([usage("r1")], tmp_path, rate_card=card)
        assert "CG001" in codes(report)


class TestPricing:
    def test_cost_is_computed_from_the_supplied_rates(self, tmp_path):
        report = analyze([usage("r1", inp=1_000_000, out=1_000_000)], tmp_path, rate_card=PRICED)
        assert report.sections["runs"][0]["cost"] == pytest.approx(18.0)

    def test_price_run_returns_none_for_unknown_models(self):
        cost, model = cg.price_run({"model": "nope", "input_tokens": 10}, PRICED["rates"])
        assert cost is None and model == "nope"

    def test_negative_and_garbage_token_counts_become_zero(self):
        cost, _ = cg.price_run({"model": "m-a", "input_tokens": -5, "output_tokens": "abc"}, PRICED["rates"])
        assert cost == 0.0


class TestBudgets:
    def test_exceeded_budget_blocks(self, tmp_path):
        report = analyze([usage("r1", inp=1_000_000, out=1_000_000)], tmp_path,
                         rate_card=PRICED, budget=1.0)
        assert "CG002" in codes(report)
        assert report.verdict == "block"

    def test_budget_at_risk_is_flagged(self, tmp_path):
        report = analyze([usage("r1", inp=1_000_000, out=0)], tmp_path, rate_card=PRICED, budget=3.5)
        assert "CG003" in codes(report)
        assert "CG002" not in codes(report)

    def test_comfortable_budget_is_silent(self, tmp_path):
        report = analyze([usage("r1", inp=1_000_000, out=0)], tmp_path, rate_card=PRICED, budget=100.0)
        assert {"CG002", "CG003"} & codes(report) == set()

    def test_scope_budget_applies_per_automation(self, tmp_path):
        records = [usage("r1", automation="Alpha", inp=1_000_000, out=0),
                   usage("r2", automation="Beta", inp=1000, out=0)]
        report = analyze(records, tmp_path, rate_card=PRICED, scope_budget=["Alpha=1.0"])
        breaches = [f for f in report.findings if f.code == "CG002"]
        assert breaches and breaches[0].locator == "Alpha"

    def test_unknown_scope_budget_is_noted_not_failed(self, tmp_path):
        report = analyze([usage("r1")], tmp_path, rate_card=PRICED, scope_budget=["Ghost=1.0"])
        assert any("Ghost" in note for note in report.notes)

    def test_malformed_scope_budget_is_rejected(self, tmp_path):
        with pytest.raises(EvidenceError):
            analyze([usage("r1")], tmp_path, scope_budget=["Alpha=notanumber"])


class TestAnomalies:
    def test_outlier_run_is_flagged(self, tmp_path):
        records = [usage(f"r{i}", inp=1000, out=100) for i in range(6)]
        records.append(usage("big", inp=500_000, out=10_000))
        assert "CG004" in codes(analyze(records, tmp_path))

    def test_uniform_usage_has_no_outlier(self, tmp_path):
        records = [usage(f"r{i}", inp=1000, out=100) for i in range(6)]
        assert "CG004" not in codes(analyze(records, tmp_path))

    def test_concentration_is_flagged(self, tmp_path):
        records = [usage("r1", automation="Hog", inp=900_000), usage("r2", automation="Mouse", inp=1000)]
        report = analyze(records, tmp_path)
        assert "CG006" in codes(report)
        assert next(f for f in report.findings if f.code == "CG006").locator == "Hog"

    def test_single_scope_is_never_flagged_as_concentrated(self, tmp_path):
        assert "CG006" not in codes(analyze([usage("r1"), usage("r2")], tmp_path))

    def test_zero_yield_runs_are_flagged(self, tmp_path):
        records = [usage(f"r{i}", artifacts=()) for i in range(4)]
        assert "CG007" in codes(analyze(records, tmp_path))

    def test_productive_runs_are_not_flagged(self, tmp_path):
        assert "CG007" not in codes(analyze([usage(f"r{i}") for i in range(4)], tmp_path))


class TestAggregation:
    def test_scopes_are_rolled_up_and_ordered_by_tokens(self, tmp_path):
        records = [usage("r1", automation="Small", inp=100),
                   usage("r2", automation="Large", inp=100_000)]
        scopes = analyze(records, tmp_path).sections["by_scope"]
        assert [s["scope"] for s in scopes] == ["Large", "Small"]

    def test_unattributed_runs_get_their_own_bucket(self, tmp_path):
        path = tmp_path / "u.json"
        path.write_text(json.dumps([{"run_id": "r", "model": "m-a", "input_tokens": 10}]), encoding="utf-8")
        report = cg.analyze(cg.argparse.Namespace(
            input=str(path), rate_card=None, budget=None, scope_budget=[],
            outlier_multiple=5.0, concentration_threshold=0.5))
        assert report.sections["by_scope"][0]["scope"] == "<unattributed>"


class TestLoading:
    def test_bundled_example_loads_and_summarizes(self, template, tmp_path):
        report = cg.analyze(cg.argparse.Namespace(
            input=str(template("cost-governor", "usage.example.json")),
            rate_card=None, budget=None, scope_budget=[],
            outlier_multiple=5.0, concentration_threshold=0.5))
        assert report.summary["runs"] == 7
        assert report.summary["scopes"] == 3
        assert "CG001" in codes(report)

    def test_empty_usage_is_an_evidence_error(self, write):
        with pytest.raises(EvidenceError):
            cg.load_usage(str(write("u.json", "[]")))

    def test_non_object_records_are_rejected(self, write):
        with pytest.raises(EvidenceError):
            cg.load_usage(str(write("u.json", json.dumps(["a", "b"]))))


class TestCli:
    def test_writes_artifacts(self, template, tmp_path):
        code = cg.main(["--input", str(template("cost-governor", "usage.example.json")),
                        "--outdir", str(tmp_path / "o"), "--quiet"])
        assert code == 0
        assert (tmp_path / "o" / "cost-governor.json").is_file()
