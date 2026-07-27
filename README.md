# Cost Governor

Track token and tool spend per session and automation against your own rate card.

> ## How to run it
> See [setup.md](setup.md), then run `/cost-governor`.

## Included

- `/cost-governor` Scout skill
- `scripts/cost_governor.py` — the deterministic engine
- `lib/scoutkit/` — vendored shared library, so this repo runs on its own
- `templates/` — a bundled, fabricated example input
- `tests/` — the full pytest suite for this skill

## Quick start

Requires Python 3.10 or later. No third-party packages.

```
python scripts/cost_governor.py \
  --input templates/usage.example.json \
  --outdir out/cost-governor
```

Input: Usage records as JSON or JSONL.

## Artifacts

- `cost-governor.json`
- `cost-governor.md`
- `cost-governor.html`

Canonical JSON validates against `references/report-schema.json`. The HTML is
self-contained — embedded CSS, no scripts, no external references — so it renders
identically offline and inside a SharePoint or OneDrive preview sandbox.

## Exit codes

`0` pass · `1` review · `2` block · `3` evidence error

Gating is opt-in via `--fail-on never|review|block`, so this never fails a pipeline
unless you ask it to.

## One-time configuration

`references/rate-card.json` ships with every rate **unset**, on purpose. Fill in
`input_per_million` and `output_per_million` for the models you use, taking the
numbers from your own billing page. Until you do, spend is reported in tokens only
and finding `CG001` explains why. This tool will not guess a price.

## What it does not do

It does not fix anything. This skill is read-only by construction: it reports,
classifies, and recommends, and a human decides. That is why it is safe to run
unattended, and why a `pass` verdict is never permission to proceed.

## Data safety

This shared package contains no customer names, account identifiers, contact emails,
secrets, internal pricing, or deal strategy. Every bundled file under `templates/` is
fabricated — example addresses use `.test` and `.invalid` reserved domains, example
secrets are non-functional literals, and example paths point at `C:\Users\example`.

## 🔍 What this skill accesses

Shown as **capability badges** on the catalog card — passive transparency, no prompt
on install. This skill can:

- 📁 reads local files you point it at
- 💾 writes files locally
- ⌨️ runs shell / Node / Python locally

_Nothing else. It never sends data to third parties, performs no network I/O, writes
nothing to a tenant, and respects Scout's runtime permission model for every action._

## Provenance

Built as part of the [Scout AgentOps Kit](scout-agentops-kit) — eight deterministic governance and
reliability skills — and published here as a standalone entry. Version 1.1.0.
