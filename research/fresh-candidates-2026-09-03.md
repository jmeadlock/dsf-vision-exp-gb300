# Fresh DS4FVis-exp inner-loop candidates

_Research refreshed 2026-09-04; original campaign request dated 2026-09-03._

## Candidate evaluated: one NextN prediction layer

The official DeepSeek-V4-Flash-Vision-Exp configuration declares `num_nextn_predict_layers: 3`, `dspark_block_size: 5`, and target layer IDs `[40, 41, 42]`.[1] A Hugging Face community participant then reported that SGLang “got faster with only 1 nextn,” supplying `--json-model-override-args {"num_nextn_predict_layers":1}` but no reproducible benchmark table or hardware-specific comparison.[2] That made the claim a useful experiment lead, not evidence suitable for adoption.

The pinned SGLang revision exposes `--json-model-override-args` as a server argument.[3] Its `ModelConfig` parses the JSON and passes the resulting dictionary into checkpoint configuration loading, so this is a real model-configuration override rather than a cosmetic launch flag.[4]

Iteration 15 tested the override as the only intended delta from compact verify-all Iteration 14. The harness verified both the effective `/server_info` value and exact Docker command before running semantic and performance gates. All correctness and operational gates passed, but C8/C16/C32/C64 throughput changed by −7.655%/−6.997%/−4.807%/+0.895%, a −4.700% geometric mean; prefill changed −1.586%. Under the frozen rule this is a **LOSS**, so the one-layer override is rejected for this single-GB300 recipe.

Because the same candidate was only −1.690% versus older compact Iteration 12, the loop selected an immediate checkpoint-default reversal to complete a default→one→default ABA bracket before assigning the whole adjacent loss to the override.

## Selection discipline

- Community and social reports are candidate generators, not promotion evidence.
- The production checkpoint, image digest, SGLang source revision, TP, GPU, prompts, repetitions, and gates stay fixed.
- Runtime configuration is accepted only when both `/server_info` and the retained Docker command prove the intended override.
- Any apparent win requires an adjacent confirmation control before retention.

## Sources

[1] https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp/raw/main/config.json — DeepSeek-V4-Flash-Vision-Exp config.json
[2] https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp/discussions/11 — DSpark acceptance rate discussion #11
[3] https://raw.githubusercontent.com/sgl-project/sglang/40b3e15ddbd9a1067e181283d9900dd3f4d76ed7/python/sglang/srt/server_args.py — SGLang pinned server_args.py
[4] https://raw.githubusercontent.com/sgl-project/sglang/40b3e15ddbd9a1067e181283d9900dd3f4d76ed7/python/sglang/srt/configs/model_config.py — SGLang pinned model_config.py
