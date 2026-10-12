# Pro implementation handoff

This branch publishes the current source baseline for GPT-6 Pro to inspect and change. The Windows operator owns execution, evidence collection, tests and final acceptance; Pro owns implementation. The user is currently playing the game, so this handoff authorizes offline implementation only. Do not launch or control the game.

## Goal and first patch

The full goal remains a real standard Currency Wars match through settlement, with ordinary decisions handled by local rules, the catalogue, joint evaluation and bounded execution. The LLM should handle uncovered mechanics, consequential strategic ambiguity and occasional correction. A preparation node is a milestone, not a replacement for the full-match goal.

Choose and implement one concrete connection that lets existing local decisions proceed through a normal preparation path toward independent battle acceptance. Reuse the existing economic loop and named deployment proposal. Inspect actual call sites before deciding what is missing. Do not rebuild a state machine, Resource Planner, broker or simulation environment. Do not deliver logging alone as the completed functional change.

Required output:

1. Explain the precise routine path improved, its old return-to-operator reason, and its new observable behavior.
2. Commit the smallest useful patch on this branch or a new `codex/` branch based on it. Return the commit/branch or a PR URL. If your current Chat tools cannot write GitHub, say so explicitly and provide an exact patch against this baseline; do not claim that code was pushed.
3. Run the existing focused checks that exercise the changed behavior. If a local native asset or Windows service is unavailable, identify that check as unavailable; never loosen guards or replace it with a synthetic success assertion.
4. Leave a short implementation note with changed files, checks, dependencies and live verification still required.

## Current capabilities — do not rebuild them

| Area | Current source |
| --- | --- |
| Original workflow, stage proofs, business continuity, receipts and execution | `tools/currency_wars_runner.py` |
| Cumulative economic budget, effect classification and explicit macro limits | `tools/currency_wars_economy.py` |
| MCP state/proposals/original submission | `tools/currency_wars_mcp.py` |
| Conditional character/bond/economic evaluation | `tools/currency_wars_advisor.py`, `currency_wars_bond_model.py`, `currency_wars_bond_proxy.py` |
| Local decision inputs and catalogue | `tools/currency_wars_decision.py`, `currency_wars_database.py`, `currency_wars_rules.py` |
| Original named deployment proposal and preconditions | `tools/currency_wars_deployment.py` |
| Paired economic views and dependency evidence | `tools/currency_wars_economic_views.py` |
| Native observations, partial read scope, identities and shop | `tools/currency_wars_perception.py`, `currency_wars_state_reader.py`, `currency_wars_shop_reader.py`, unit inspection/cache modules |
| Existing timing and root-return events | `tools/currency_wars_profile.py` |

The joint evaluator already compares current shop purchase subsets, experience and bounded refresh. It uses approximate power and bond-count DP; those scores are not calibrated combat probabilities. Equipment/investment combat interactions and effective finite-pool mechanics remain incomplete. A public data field is not proof that its mechanic is enabled in the current match.

## Execution boundaries

- Preserve the single original broker, source checks, authenticated owner, match/node/epoch, pause/takeover/stop and bounded leases.
- Preserve current rewards → complete shop read → unwanted inventory → joint economy → equipment/lineup → independent battle acceptance relationships. A proposed dependency optimization must retain actual reward/capacity/resource dependencies; do not silently skip required evidence.
- `advance_local_economy` remains one step by default. The separately typed `advance_local_economy_macro` has explicit economic-action/spend/paid-refresh/navigation/time caps and independently verifies each effect before continuing. It does not authorize rewards, selling, investment selection, equipment, deployment or battle.
- MCP submission acknowledgement is not game execution. Original receipt completion proves input delivery; purchase, experience, equipment and deployment effects require actual readback. An unknown receipt/effect must stop subsequent mutations and retain the original ID. Do not replay unknown input. A rejection is not automatically zero input.
- The native limited reward detector does not prove that the entire field is clear. Do not manufacture `all_claimed`, current inventory, overflow, gear, stage completion or battle approval to make a normal path pass.
- Necessary missing fields should request a bounded local observation. Do not require unrelated fields to be complete, and do not infer a required unknown field from a past frame.
- Low-risk nearly equivalent candidates use a reproducible preference; a small score gap alone does not trigger an LLM. Escalate for uncovered consequential mechanics or sensitivity to plausible assumptions, not every tie.
- Static definitions can be cached by source/version. Purchase/auto-combine/sale/equipment/investment/population changes invalidate affected value and layout dependencies. Fresh input still uses the original current-frame locator.
- User-controlled foreground time is not an optimization target. Never resume or steal input while the user is playing.

## Software and live evidence are different

The operator ran 150 focused checks after combining the economic macro and committed-resolution reload projection. A different reviewer inspected the exact complete local runtime and original source publication succeeded. No new functional source change has occurred since that audit. This is baseline software evidence, not approval for a new patch or proof of full-game automation.

One original economic action was independently verified: 6 coins / level 3 / XP 0/4 → 2 coins / level 4 / XP 0/6, actual spend 4. The original archived action is never replayed. A reload projection now preserves the already committed result without changing its original ledger/receipt/images.

The macro still lacks a real one-authority/two-effect trial. The latest operator trial completed same-match continuity, a supervised full-field reward check and native five-slot shop reading, then yielded to the user's foreground use. It issued two navigation inputs and zero new economic/deployment/battle inputs. No new automated match has settled.

Its measured interval was 943.906 seconds: manual wait 509.093, supervisor wait 181.313, capture 80.299, measured OCR engine 83.208, remaining perception 67.740, decision category 0.657. These are measured categories, not pure LLM/DP timings. It is an incomplete node and not an A/B experiment.

## Source and assets

Local credentials, controller journals, current readiness approvals, original user screenshots, the native identity bank, private observation imports and compiled binaries are intentionally absent from this public handoff. Their absence is not permission to bypass runtime checks.

The public static-client import scripts and release normalization are provided separately. They use fixed released-data revisions; keep provenance and missing/unknown fields. Do not download future/test releases for the active candidate pool. The SQLite catalogue can be rebuilt locally from the normalized import with the database build CLI. The public import contains game definitions, not this user's inventory or progress.

Git normalizes most text to LF while the locally audited bytes may include CRLF. Exact local source/provider/resource approval must be regenerated for any applied new patch. Do not hand-edit readiness records or publish an old approval against different bytes.

Focused local acceptance previously used:

```text
python -B -X utf8 -m unittest test_currency_wars_economy test_currency_wars_preparation_replay test_currency_wars_mcp test_currency_wars_business test_local_runtime_compatibility test_currency_wars_source_runtime
```

The command runs from `tools`; native/local resources and installed Windows provider are required for some cases. Tests needing omitted local assets are not claimed to pass in a clean cloud checkout. Add durable tests only for the particular public invariant/regression changed; do not create a new broad test framework.

## Acceptance of the next patch

The operator will pull or apply Pro's actual patch, inspect the scope and original input boundaries, run relevant checks, obtain the required exact-source independent review, publish through the original entrypoint, then test a fresh real preparation → battle → result after the user hands back the game. Log automatic versus supervised actions and every return reason. Only that later evidence can establish live benefit or full-match progress.
