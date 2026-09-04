# SGLang delta relevant to DS4FVis-exp

_Reviewed 2026-09-04 against the campaign’s pinned source revision `40b3e15ddbd9a1067e181283d9900dd3f4d76ed7`. This note covers the fresh pull requests examined for the 2026-09-03 campaign request._

## Decision summary

The fresh merged changes do not supply a clean runtime-code delta to drop into the current pinned single-GB300 comparison. Two are cookbook-only changes, one adds CI coverage, and the large multimodal implementation remains open. Keep the current image/source pin for inner-loop comparability; treat a later image/source refresh as its own candidate with full gates rather than mixing it into a flag experiment.

## Pull-request audit

- **#37253 — `[Model] Support DeepSeek-V4-Flash-Vision multimodal`** is still open. Its file set spans model configuration, DSV4 attention and scheduling, DSpark, OpenAI encoding/chat serving, and new vision-model and multimodal-processor modules. That is a broad runtime change, not a one-knob inner-loop candidate.[5]
- **#37665 — `[CI] Add dspark + dsv4 e2e test`** merged on 2026-09-03. The diff changes two registered B200 end-to-end test files; it strengthens upstream coverage but does not itself offer a serving-performance lever.[6]
- **#37492 — `[Cookbook] Verify DeepSeek-V4 Flash Vision on GB300`** merged on 2026-09-02. Its diff is confined to cookbook configuration and benchmark snippets, so it is deployment evidence rather than engine code.[7]
- **#37737 — `[Cookbook] DeepSeek-V4 DGX Spark: v2 image + Flash Official NVFP4 and Flash Vision FP4 cells`** merged on 2026-09-03. Its diff is also confined to cookbook pages/snippets. It is useful for recipe lineage but does not justify changing the pinned runtime by itself.[8]
- **#37301 — `[Cookbook] Enable DSpark on the DeepSeek-V4 Flash Vision low-latency recipes`** merged on 2026-08-31. It establishes the upstream recipe direction, but the change is documentation/configuration rather than a new kernel or scheduler implementation.[9]

## Inner-loop consequence

The safe near-term candidate was therefore the checkpoint-configuration override already exposed by the pinned runtime, not a source/image jump. Iteration 15 tested one NextN prediction layer under an otherwise identical compact verify-all recipe and failed the frozen throughput comparison. The immediate checkpoint-default reversal is the necessary adjacent control before attributing the full observed loss to that override.

## Sources

[5] https://github.com/sgl-project/sglang/pull/37253 — SGLang PR #37253
[6] https://github.com/sgl-project/sglang/pull/37665 — SGLang PR #37665
[7] https://github.com/sgl-project/sglang/pull/37492 — SGLang PR #37492
[8] https://github.com/sgl-project/sglang/pull/37737 — SGLang PR #37737
[9] https://github.com/sgl-project/sglang/pull/37301 — SGLang PR #37301
