---
name: cost-governor
description: Track token and tool-call spend per session and automation against a rate card you supply, flagging budget breaches, runaway automations, outlier runs, and runs that consumed budget without producing output. Never invents or fetches a price — unpriced models are reported as unpriced. Trigger when the user says "/cost-governor", "what is this costing me", "which automation burns the most tokens", "set a budget", "why did my usage spike", or asks for a spend report.
---

# Cost Governor

Spend attribution and budget enforcement over usage records.

## The pricing rule

**This skill never invents a price.** The bundled rate card at
`references/rate-card.json` ships with every rate unset. Until you fill it in from your
own billing page, spend is reported in tokens and tool calls only, and `CG001` is raised
to say so. Models missing from the card are reported as unpriced and excluded from
currency totals rather than estimated.

Fill in each model you use:

```json
{
  "currency": "USD",
  "rates": {
    "<model-id>": {
      "input_per_million": 0.00,
      "output_per_million": 0.00,
      "source": "where you got this",
      "as_of": "YYYY-MM-DD"
    }
  }
}
```

## Inputs

Usage records as JSON array or JSONL. Each record may carry `run_id`, `automation` (or
`session`), `model`, `input_tokens`, `output_tokens`, `tool_calls`, `duration_s`,
`started_at`, and `artifacts`. Missing numeric fields are treated as zero, never guessed.

## How to run it

```
python scripts/cost_governor.py \
  --input <usage.json> \
  --rate-card <your-rates.json> \
  --budget 25.00 \
  --scope-budget "Daily Digest=2.00" "Research Queue=10.00" \
  --outdir out/cost-governor
```

Tune anomaly sensitivity with `--outlier-multiple` (default 5x median tokens) and
`--concentration-threshold` (default 0.5).

## What it flags

- `CG001` no rate card configured — you cannot govern what you cannot price.
- `CG002` budget exceeded (blocking) and `CG003` budget at 80% or more.
- `CG004` outlier runs above a multiple of the median token count.
- `CG005` models present in usage but absent from the rate card.
- `CG006` one automation consuming more than half of all tokens.
- `CG007` runs that consumed tokens but recorded no artifacts — usually an idle poll
  that should have exited early.

## How to read the result

`sections.by_scope` is ranked by token consumption, so the top row is your biggest
consumer. `summary.priced_runs` versus `unpriced_runs` tells you how much of the
picture is actually priced.

## Limits — state these when you report

- Accuracy is entirely bounded by the rate card you supply and the completeness of the
  usage records. Neither is validated against a billing system.
- Outlier detection needs at least five runs to be meaningful.
- Token counts are exact; currency figures are arithmetic on your own numbers.

## Guardrails

No network. No pricing lookup. No cloud writes. Reads usage records only.
