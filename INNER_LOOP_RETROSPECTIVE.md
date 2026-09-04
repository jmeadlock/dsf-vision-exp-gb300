# testing the inner loop on DSFVE

*Overnight campaign retrospective, September 3–4, 2026*

The experiment did not produce a faster production recipe. It produced something I trust more: a loop that could reject its own results, followed by an outer pass that turned the strongest admissible control into a simpler Recipe v2 without pretending it was a speed win.

We took the live single-GB300 DeepSeek-V4-Flash-Vision-Exp service offline, preserved its exact container as `dsfv-dspark-prod-hold-20260903-1900`, pinned the image digest and SGLang source revision, then let the inner loop work one card at a time. The campaign used 20 iteration numbers. Ten runs produced admissible performance evidence, two produced calibration artifacts only, and eight were blocked or aborted because the harness could not prove what had happened. No candidate was promoted on throughput.

That is a successful test of the process described in [the earlier inference recipe generator design note][15]. The process learned, narrowed the search, caught several of its own bugs, stopped without inventing a win, and still produced a cleaner default launch: static verification, no SPS table, checkpoint-default NextN, immutable image/model pins, and the required DSML encoding patch.

The machine receipts, harness, generated public-safe audit, and Recipe v2 artifacts live in [the companion repository][16]. `research/inner-loop-campaign-audit.json` is the canonical compact accounting; `research/build_campaign_audit.py` rebuilds it from selected receipt fields. `recipe-v2.json` and `research/recipe-v2-evidence.json` record the selected recipe and its claim boundary.

## What was frozen

The run target stayed fixed:

| Surface | Frozen value |
|---|---|
| Model | `deepseek-ai/DeepSeek-V4-Flash-Vision-Exp` |
| SGLang image | `lmsysorg/sglang:dev-dsv4-flash-vision` |
| Image digest | `sha256:7ac467a50508b7029a23e846c150998fdd26d95c1cfd377ea7e74e28374486a6` |
| SGLang source | `40b3e15ddbd9a1067e181283d9900dd3f4d76ed7` |
| Hardware | One NVIDIA GB300 DGX Station, TP=1 |
| Primary throughput rows | C8, C16, C32, C64 geometric mean |
| Materiality band | ±3% plus per-row non-regression gates |
| Cold-prefill ladder | 8K, 32K, 64K, 128K, 256K targets |

Each card was immutable after dispatch. It named one intended delta, its reference run, expected runtime state, source and artifact hashes, correctness gates, performance rows, and the decision rule. A Station-side lock allowed one container at a time. Every exit path stopped the experiment container and left production alone.

`STOP` and `restore` meant different things. `STOP` cancelled the active experiment, stopped its container, and started nothing else. Restoration would have relaunched the locked production script and run production gates. We never issued that separate instruction. Production remains preserved and stopped.

## What counted as evidence

The runner did not accept a launch command as proof that a setting took effect. It required runtime receipts.

A normal performance run had to prove all of the following before its numbers were eligible:

1. The local contract schema and frozen hashes passed before SSH or Docker.
2. The container reported the requested ragged-verification, SPS, and NextN state.
3. Text, parsed tool calls, real vision/OCR, and a six-turn replay passed.
4. Sixty-four opaque identifiers came back byte-for-byte unchanged.
5. The C64 repetition audit flagged 0 of 128 samples.
6. The GPU/error scan was clean.
7. Throughput and cold-prefill parsers found every frozen row.
8. The container stopped, port 30003 went offline, the GPU returned idle, and the experiment lock was free.

The opaque-identifier gate was not decorative. SGLang issue #34959 documented plausible-looking single-character identifier corruption under DSpark, including at temperature zero.[17] An identifier that is almost right is wrong. Any changed, invented, duplicated, or missing token was an immediate loss.

The longer replay gate came from an earlier failure in this same serving stack. A preview-image mismatch around SGLang's tool-call argument normalization let short tool smokes pass while longer agent sessions progressively nested `arguments` inside `arguments`.[18][19] The inner loop therefore tested replayed history, not just a first-turn tool call.

## The night, honestly classified

| Iterations | What happened | Evidence class |
|---|---|---|
| 0–2 | Reproduced the incumbent, tested compact verify-all, then activated the inherited SPS table | Three valid performance runs; one baseline, one inconclusive, one loss |
| 3–8 | Rebuilt profiling after synthetic correctness, auth, receipt-schema, and profiler-boundary failures | Five blocked/aborted harness runs; one calibration-only success |
| 9–14 | Caught a Bash portability bug and malformed contract, rejected the calibrated table, then bracketed compact vs static | Four valid performance/control runs; two blocked prelaunch runs |
| 15–16 | Tested one NextN layer and immediately reversed to the checkpoint default | One candidate loss; one confirming control win |
| 17–19 | Fit an additive SPS table, caught an evaluator-schema hole, then ran the corrected candidate | One calibration success, one blocked harness run, one candidate loss |

The accounting matters. Iteration 18 completed the workload and every visible correctness gate, but its frozen contract used `decision_rule` while the evaluator consumed `winner_rule`. The evaluator could not issue the contracted decision. We classified the run as `BLOCKED` and did not reuse its raw numbers as a win or loss. Iteration 19 was a new immutable card with the corrected schema.

The same rule applied to every earlier failure. A 401 caused by the runner is not a model loss. A parser crash before Docker is not a model loss. A synthetic profiler run is not semantic evidence. Keeping those categories separate is the difference between an experiment ledger and a scoreboard with made-up certainty.

## Compact verify-all was neutral

Iteration 1 looked promising at first: the primary C8/C16/C32/C64 geometric mean improved 5.280% over the incumbent. The row shape was already suspicious. C8 rose 21.449%, while C16 fell 6.303%, crossing the per-row guardrail. The frozen verdict was `INCONCLUSIVE`, not `WIN`.

The repeat did not reproduce the shape. Iteration 12 was 3.883% below Iteration 1 and only 1.192% above the original static baseline. We then ran static between two compact runs, giving a compact → static → compact ABA sequence across Iterations 12–14.

| Bracketed effect | Primary C8–C64 | Cold prefill |
|---|---:|---:|
| Compact verify-all vs intervening static | **+0.901%** | **+0.217%** |

Both values sit inside the frozen ±3% band. The alternating controls also exposed where the noise lived: C1 and C4 moved wildly, while C32, C64, and prefill stayed much tighter. Compact verify-all was closed as neutral. We stopped spending iterations on it.

## A good calibration table can still be a bad recipe

The image defaulted to static ragged verification, where the configured SPS table was ineffective. That made the first inherited-table comparison easy to misread. Iteration 2 deliberately moved to compact mode with the inherited table and lost 3.561% on the primary geometric mean against compact verify-all, including an 11.796% C8 regression.

The first profiler attempt then contaminated ordinary correctness with `SGLANG_SIMULATE_ACC_LEN`. The model failed the semantic gates, and the runner correctly stopped before profiling. We split the work into two runtimes:

- an authenticated ordinary static Tier-0 container for semantic correctness;
- a separate unauthenticated, loopback-only compact container for synthetic calibration.

Iteration 8 produced a valid 33-round calibration artifact. Iteration 10 mounted the exact artifact read-only, verified its digest at every boundary, and still lost 4.327% against the older compact reference.

That was not enough to blame the table. The immediate table-off control in Iteration 12 improved only 0.464% over Iteration 10. The older comparison had drifted, especially at C8. The honest conclusion was narrower: the generated table had not earned promotion, and the campaign did not establish that SPS alone caused the whole older-reference loss.

We then tested the profiler's richer additive model, `T(bs, M) = bias + alpha(bs) + theta(M)`. Iteration 17 collected all 132 requested cells: 11 batch-size probes × 4 verify-budget fractions × 3 repeats. The fit looked excellent on paper:

| Additive calibration gate | Result |
|---|---:|
| Match fraction | 1.000 |
| R² | 0.9996 |
| P95 relative error | 0.0200 |
| Maximum relative error | 0.0487 |

The artifact also contained a warning the headline fit could hide. The largest `theta(M)` coefficient was only 0.7196% of median predicted step time, and predicted cost decreased across 22 of 33 increasing-budget transitions. The table fit the samples, but the budget term was tiny and often physically non-monotonic.

Iteration 19 supplied the only admissible runtime verdict for that artifact:

| Additive SPS vs checkpoint-default control | C8 | C16 | C32 | C64 | Primary geomean | Prefill geomean |
|---|---:|---:|---:|---:|---:|---:|
| Iteration 19 vs Iteration 16 | **−21.170%** | −1.248% | −3.783% | −0.750% | **−7.145%** | −0.833% |

The table passed calibration and lost in service. Those statements do not conflict. Calibration answers whether the profiler produced a coherent measurement artifact under its own gates. Promotion asks whether that artifact improves the real workload without harming correctness. It did not.

## One NextN layer failed the reversal

The official checkpoint declares three NextN prediction layers.[1]

A Hugging Face discussion suggested that forcing one layer made SGLang faster, but supplied no reproducible single-GB300 table.[2]

The pinned SGLang runtime exposed a real JSON model override path, so the claim was testable without changing the image or source revision.[3][4]

Iteration 15 changed exactly one field to `num_nextn_predict_layers=1`. The harness verified the live `/server_info` value and retained Docker command before accepting the run. It lost 4.700% against the immediately preceding checkpoint-default run.

We reverted immediately instead of moving on. Iteration 16 restored the checkpoint default and beat the one-layer run by 3.608% on the primary geometric mean. The full default → one → default ABA estimate put one layer at **−4.093% primary** and −1.538% prefill.

That is the cleanest causal result from the night. One layer was rejected. The checkpoint default of three stayed.

## Upstream research stayed outside the causal comparison

The SGLang changes reviewed on September 3 did not provide a clean one-line runtime delta to mix into this campaign. The broad multimodal implementation PR remained open.[5]

One merged PR added DSpark/DSV4 end-to-end CI.[6]

The other relevant merged changes were cookbook or configuration updates rather than isolated engine code.[7][8][9]

We kept the pinned runtime. A future image or source refresh should be its own candidate with the full gate stack. Mixing an upstream code jump into a flag experiment would make both results less useful.

Mia AI Lab and `@plotarmordev` posts were handled the same way as the one-layer suggestion: candidate leads with canonical URLs, not evidence. They entered a research queue with questions that require commit-level corroboration and single-GB300 applicability. None of those posts changed the live recipe overnight.

## The harness failures were the useful part

The loop got more trustworthy because it failed in specific ways:

| Failure | What changed afterward |
|---|---|
| Synthetic acceptance ran during semantic checks | Correctness and profiling became separate runtimes under one lock |
| A redaction placeholder reached executable auth | Credentials load only at runtime; receipts sanitize at the boundary |
| Tier-0 required a profile-only field | Runtime predicates now match the evidence each mode actually emits |
| The stock profiler could not authenticate | Synthetic profiling runs unauthenticated on `127.0.0.1` only; ordinary Tier-0 stays authenticated |
| macOS Bash 3.2 lacked `readarray` | Portable tab-delimited parsing replaced the assumption before Station launch |
| `target.container` was misplaced | Contract schema validation moved before SSH, staging, ledger mutation, Docker, or benchmarks |
| `decision_rule` did not match `winner_rule` | Validation now checks the exact evaluator-consumed schema before dispatch |
| Long benchmark supervision exceeded an agent turn | The recurring supervisor became a decision edge; detached runners own long work and persist identity |

A fail-closed harness does not make failures disappear. It labels them correctly and leaves enough evidence to fix the harness without smearing the failure onto the model.

## What I would keep for the next campaign

1. **Preserve production before touching the recipe.** Rename and hold the exact container; do not rebuild the rollback path from memory.
2. **Freeze one delta and its evaluator together.** Validate the fields the evaluator actually reads, then hash the validator, runner, and contract.
3. **Prove runtime state.** Requested flags are intent. `/server_info`, container arguments, environment state, mounted artifact digests, and profile-mode receipts are evidence.
4. **Put correctness before throughput.** Real vision, replayed tool history, opaque identifiers, repetition, and GPU errors must all stay hard gates.
5. **Isolate synthetic calibration.** A simulated acceptance profile can measure scheduling cost. It cannot prove semantic correctness.
6. **Bracket plausible wins and losses.** Adjacent reversals and alternating controls beat comparisons to a run from hours earlier.
7. **Keep evidence classes explicit.** Performance experiment, control, calibration, harness failure, community lead, and upstream primary source are different things.
8. **Make stop deterministic and restoration separate.** An autonomous loop should be able to halt safely without deciding on its own to bring production back.
9. **Treat generated artifacts as proposals.** A fitted table does not promote itself. It gets one frozen runtime test, then an immediate control if it appears to win.
10. **Publish the losses.** The rejected SPS tables and one-layer override are more useful than a cherry-picked speedup because they narrow the next search.

## What the campaign established

- No candidate earned a measured throughput promotion.
- Iteration 13 became Recipe v2 because it preserved equivalent performance while removing ineffective machinery.
- Compact verify-all had no reproducible material advantage over static on this workload.
- The inherited and generated SPS tables did not earn promotion.
- `num_nextn_predict_layers=1` was a reproducible loss; the checkpoint default of three remained.
- The additive SPS table passed calibration and then lost 7.145% in its admissible runtime comparison.
- Every valid performance run passed the frozen correctness and teardown gates.
- The campaign ended with no experiment container, port 30003 offline, the GPU idle, the lock free, and the held production container still stopped.

The last point is deliberate. Calling the campaign a success did not authorize a production restore, and it did not turn the least-bad candidate into a speed winner.

The success was the refusal to lie. The loop found attractive numbers, then made them survive replication, reversal, runtime proof, and correctness. None became a speed promotion. The outer pass still generated a useful recipe by choosing the simplest equally supported runtime surface.

Next time, the machine should start with this stronger loop instead of relearning it at 3 a.m.

## The recipe the outer loop generated

Recipe v2 is the Iteration 13 static/no-SPS control, with the Iteration 16 reversal used to retain checkpoint-default three-layer NextN. It is current because it is simpler and better proven, not because it is faster.

```bash
SGLANG_RAGGED_VERIFY_MODE=static \
python3 -m sglang.launch_server --trust-remote-code --model-path /model --tp 1 \
  --mem-fraction-static 0.90 --context-length 1048576 \
  --chunked-prefill-size 8192 \
  --cuda-graph-max-bs-decode 64 --cuda-graph-bs-decode 1 2 4 8 16 32 64 \
  --max-running-requests 64 \
  --enable-metrics --host 0.0.0.0 --port 30003 \
  --served-model-name dsf-vision-exp --api-key YOUR_KEY \
  --tool-call-parser deepseekv4 --reasoning-parser deepseek-v4 \
  --speculative-algorithm DSPARK \
  --swa-full-tokens-ratio 0.1
```

Launcher defaults also pin `lmsysorg/sglang@sha256:7ac467a50508b7029a23e846c150998fdd26d95c1cfd377ea7e74e28374486a6`, use model revision `6821d6ad3681a4b137b066b76094fa82ebd0a380`, and bind-mount the required `patches/encoding_dsv4.py` DSML fix. The default launch omits the SPS table path/mount, omits `--json-model-override-args`, and omits any DSpark block-size override.

| Concurrency | Aggregate output tok/s |
|---:|---:|
| C1 | 329.2 |
| C4 | 748.7 |
| C8 | 1260.7 |
| C16 | 1786.0 |
| C32 | 2383.6 |
| C64 | 2985.4 |

| Prefill target | Mean tok/s |
|---:|---:|
| 8K | 33,935 |
| 32K | 32,561 |
| 64K | 35,669 |
| 128K | 35,662 |
| 256K | 31,650 |

The claim boundary is narrow: C8-C64 geometric mean was **2000.701 tok/s**, **+1.859%** versus Iteration 0, inside the ±3% materiality band. Recipe v2 therefore means equivalent performance with less runtime machinery and stronger evidence. It does not mean a speed promotion.

## Sources

[1] https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp/raw/main/config.json — DeepSeek-V4-Flash-Vision-Exp config.json
[2] https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp/discussions/11 — DSpark acceptance rate discussion #11
[3] https://raw.githubusercontent.com/sgl-project/sglang/40b3e15ddbd9a1067e181283d9900dd3f4d76ed7/python/sglang/srt/server_args.py — SGLang pinned server_args.py
[4] https://raw.githubusercontent.com/sgl-project/sglang/40b3e15ddbd9a1067e181283d9900dd3f4d76ed7/python/sglang/srt/configs/model_config.py — SGLang pinned model_config.py
[5] https://github.com/sgl-project/sglang/pull/37253 — SGLang PR #37253
[6] https://github.com/sgl-project/sglang/pull/37665 — SGLang PR #37665
[7] https://github.com/sgl-project/sglang/pull/37492 — SGLang PR #37492
[8] https://github.com/sgl-project/sglang/pull/37737 — SGLang PR #37737
[9] https://github.com/sgl-project/sglang/pull/37301 — SGLang PR #37301
[15] https://al-engr.com/designing-an-inference-recipe-generator.html — Designing an inference recipe generator
[16] https://github.com/jmeadlock/dsf-vision-exp-gb300 — DSF Vision-Exp single-GB300 recipe and harness
[17] https://github.com/sgl-project/sglang/issues/34959 — SGLang issue #34959: DSpark identifier corruption
[18] https://github.com/sgl-project/sglang/pull/28035 — SGLang PR #28035: normalize tool call arguments
[19] https://al-engr.com/dsf-vision-exp-single-gb300.html — DSF Vision-Exp on one GB300
