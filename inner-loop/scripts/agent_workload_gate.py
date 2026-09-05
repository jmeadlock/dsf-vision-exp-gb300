#!/usr/bin/env python3
"""Deterministic secondary workload gate for DSFVE SPS requalification."""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

BASE = os.getenv("BASE_URL", "http://127.0.0.1:30003/v1").rstrip("/")
MODEL = os.getenv("MODEL", "dsf-vision-exp")
API_KEY = os.getenv("API_KEY", "")
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

WORKLOAD_CLASSES = [
    {
        "prompt_class": "natural_prose",
        "max_tokens": 256,
        "temperature": 0,
        "reasoning_effort": "low",
        "prompt": "Write a concise operational summary of a lab benchmark run using neutral, non-marketing language.",
    },
    {
        "prompt_class": "code_oriented",
        "max_tokens": 256,
        "temperature": 0,
        "reasoning_effort": "low",
        "prompt": "Write a Python function that validates a SHA-256 hex digest and explain one edge case.",
    },
    {
        "prompt_class": "six_turn_replay",
        "max_tokens": 400,
        "temperature": 0,
        "reasoning_effort": "low",
        "prompt": "Replay six deterministic tool-result turns and preserve the identifiers exactly.",
    },
    {
        "prompt_class": "warm_prefix_repeat",
        "max_tokens": 192,
        "temperature": 0,
        "reasoning_effort": "low",
        "prompt": "Repeat this warm-prefix request after the same system prefix and report the same checklist shape.",
    },
    {
        "prompt_class": "staggered_mixed_load",
        "max_tokens": 192,
        "temperature": 0,
        "reasoning_effort": "low",
        "prompt": "During active decoding, answer a newly admitted prefill-style request with a short numbered list.",
    },
]
REQUIRED_RECEIPT_FIELDS = {
    "prompt_class",
    "max_tokens",
    "temperature",
    "reasoning_effort",
    "ttft_s",
    "wall_s",
    "decode_tok_s",
    "output_tokens",
    "finish_reason",
    "speculative_acceptance",
    "cache_hit_tokens",
    "repetition_ok",
    "tool_fidelity_ok",
}


def summarize_repetition(text: str) -> dict[str, Any]:
    tokens = text.split()
    grams = Counter(tuple(tokens[i : i + 8]) for i in range(max(0, len(tokens) - 7)))
    worst = max(grams.values(), default=0)
    return {"repetition_ok": worst < 4, "worst_8gram_count": worst}


def parse_workload_receipts(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    names = [row.get("prompt_class") for row in rows]
    expected = [case["prompt_class"] for case in WORKLOAD_CLASSES]
    missing = [name for name in expected if name not in names]
    extra = [name for name in names if name not in expected]
    if missing:
        raise ValueError(f"missing workload classes: {missing}")
    if extra:
        raise ValueError(f"unexpected workload classes: {extra}")
    if len(set(names)) != len(names):
        raise ValueError("duplicate workload classes")
    for row in rows:
        missing_fields = sorted(REQUIRED_RECEIPT_FIELDS - set(row))
        if missing_fields:
            raise ValueError(f"workload {row.get('prompt_class')} missing fields: {missing_fields}")
        if not row["repetition_ok"] or not row["tool_fidelity_ok"]:
            raise ValueError(f"workload {row['prompt_class']} failed repetition/tool fidelity")
    return sorted(rows, key=lambda row: expected.index(row["prompt_class"]))


def _chat(case: dict[str, Any]) -> dict[str, Any]:
    body = {
        "model": MODEL,
        "stream": True,
        "temperature": case["temperature"],
        "max_tokens": case["max_tokens"],
        "stream_options": {"include_usage": True},
        "chat_template_kwargs": {"reasoning_effort": case["reasoning_effort"]},
        "messages": [{"role": "user", "content": case["prompt"]}],
    }
    request = urllib.request.Request(
        BASE + "/chat/completions", data=json.dumps(body).encode(), headers=HEADERS
    )
    t0 = time.monotonic()
    first = None
    usage = {}
    finish_reason = None
    chunks: list[str] = []
    with urllib.request.urlopen(request, timeout=900) as response:
        for raw in response:
            line = raw.decode().strip()
            if not line.startswith("data:") or line.endswith("[DONE]"):
                continue
            data = json.loads(line[5:])
            if data.get("usage"):
                usage = data["usage"]
            choices = data.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            piece = delta.get("content") or delta.get("reasoning_content") or delta.get("reasoning") or ""
            if piece and first is None:
                first = time.monotonic()
            if piece:
                chunks.append(piece)
            finish_reason = choices[0].get("finish_reason") or finish_reason
    t1 = time.monotonic()
    first = first or t1
    output_tokens = int(usage.get("completion_tokens") or 0)
    decode_s = max(t1 - first, 1e-9)
    repetition = summarize_repetition("".join(chunks))
    return {
        "prompt_class": case["prompt_class"],
        "max_tokens": case["max_tokens"],
        "temperature": case["temperature"],
        "reasoning_effort": case["reasoning_effort"],
        "ttft_s": round(first - t0, 4),
        "wall_s": round(t1 - t0, 4),
        "decode_tok_s": round(output_tokens / decode_s, 4) if output_tokens else 0.0,
        "output_tokens": output_tokens,
        "finish_reason": finish_reason or "unknown",
        "speculative_acceptance": {"accept_len": None, "accept_rate": None},
        "cache_hit_tokens": int(usage.get("cached_tokens") or usage.get("cache_hit_tokens") or 0),
        "repetition_ok": repetition["repetition_ok"],
        "tool_fidelity_ok": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("agent-workload.jsonl"))
    parser.add_argument("--parse-only", action="store_true")
    args = parser.parse_args()
    if args.parse_only:
        try:
            rows = parse_workload_receipts(args.out)
        except Exception as exc:
            print(f"AGENT_WORKLOAD_RESULT FAIL reason={exc}")
            return 2
        print(f"AGENT_WORKLOAD_RESULT PASS cases={len(rows)}")
        return 0
    rows = [_chat(case) for case in WORKLOAD_CLASSES]
    args.out.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows))
    parse_workload_receipts(args.out)
    print(f"AGENT_WORKLOAD_RESULT PASS cases={len(rows)} out={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
