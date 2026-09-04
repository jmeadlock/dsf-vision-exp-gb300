#!/usr/bin/env python3
"""Catid-compatible throughput bench plus per-cell DSpark gauges."""
import json
import os
import re
import threading
import time
import urllib.request
import uuid

base = os.getenv("BASE_URL", "http://127.0.0.1:30003/v1").rstrip("/")
model = os.getenv("MODEL", "dsf-vision-exp")
key = os.environ["API_KEY"]
H = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
CONCS = [int(x) for x in os.getenv("CONCS", "1 4 8 16 32 64").split()]
REPS = int(os.getenv("REPS", "3"))
OUT = int(os.getenv("OUT_TOKENS", "1024"))
FILLER = "The quick brown fox jumps over the lazy dog near the riverbank while autumn leaves drift slowly past the old stone bridge. "
PROMPT_BODY = FILLER * 290
METRIC_NAMES = {
    "sglang:spec_accept_length": "accept_len_gauge",
    "sglang:spec_accept_rate": "accept_rate_gauge",
    "sglang:spec_cap_length": "cap_len_gauge",
    "sglang:spec_block_accept_length": "block_accept_len_gauge",
    "sglang:spec_num_steps": "spec_num_steps_gauge",
    "sglang:spec_num_draft_tokens": "draft_tokens_gauge",
}


def metrics():
    try:
        text = urllib.request.urlopen(
            urllib.request.Request(base.replace("/v1", "") + "/metrics", headers=H),
            timeout=20,
        ).read().decode()
    except Exception:
        return {}
    values = {}
    for line in text.splitlines():
        match = re.match(r"(sglang:[A-Za-z0-9_]+)\{[^}]*\}\s+([-+\d.eE]+)$", line)
        if match:
            values[match.group(1)] = float(match.group(2))
    return values


def one(out):
    nonce = uuid.uuid4().hex
    payload = {
        "model": model,
        "stream": True,
        "temperature": 0,
        "max_tokens": OUT,
        "ignore_eos": True,
        "stream_options": {"include_usage": True},
        "chat_template_kwargs": {"reasoning_effort": "low"},
        "messages": [{
            "role": "user",
            "content": f"[{nonce}] " + PROMPT_BODY + " Summarize the passage above in detail.",
        }],
    }
    request = urllib.request.Request(
        base + "/chat/completions", data=json.dumps(payload).encode(), headers=H
    )
    t0 = time.monotonic()
    first = None
    usage = None
    with urllib.request.urlopen(request, timeout=3600) as response:
        for line in response:
            line = line.decode().strip()
            if not line.startswith("data:") or line.endswith("[DONE]"):
                continue
            data = json.loads(line[5:])
            if data.get("usage"):
                usage = data["usage"]
            choices = data.get("choices") or []
            if choices and (choices[0].get("delta") or {}):
                delta = choices[0]["delta"]
                if first is None and (
                    delta.get("content")
                    or delta.get("reasoning_content")
                    or delta.get("reasoning")
                ):
                    first = time.monotonic()
    t1 = time.monotonic()
    out.append({
        "ttft": (first or t1) - t0,
        "ptok": usage["prompt_tokens"] if usage else 0,
        "ctok": usage["completion_tokens"] if usage else 0,
        "decode_s": t1 - (first or t0),
    })


def run(concurrency, count):
    results = []
    wall_t0 = time.monotonic()
    claimed = 0
    lock = threading.Lock()

    def worker():
        nonlocal claimed
        while True:
            with lock:
                if claimed >= count:
                    return
                claimed += 1
            one(results)

    threads = [threading.Thread(target=worker) for _ in range(concurrency)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wall = time.monotonic() - wall_t0
    total = sum(row["ctok"] for row in results)
    per_stream = [row["ctok"] / row["decode_s"] for row in results if row["decode_s"] > 0]
    return {
        "C": concurrency,
        "n": count,
        "agg_tok_s": round(total / wall, 1),
        "per_stream_tok_s": round(sum(per_stream) / len(per_stream), 1),
        "mean_ttft_s": round(sum(row["ttft"] for row in results) / len(results), 3),
        "ptok": results[0]["ptok"],
        "ctok_total": total,
        "wall_s": round(wall, 1),
    }


for concurrency in CONCS:
    run(concurrency, concurrency)
    before = metrics()
    result = run(concurrency, REPS * concurrency)
    after = metrics()

    accept_sum = after.get("sglang:spec_accept_length_sum", 0) - before.get(
        "sglang:spec_accept_length_sum", 0
    )
    accept_count = after.get("sglang:spec_accept_length_count", 0) - before.get(
        "sglang:spec_accept_length_count", 0
    )
    if accept_count:
        result["accept_len_counter"] = round(accept_sum / accept_count, 3)
    for metric_name, field_name in METRIC_NAMES.items():
        if metric_name in after:
            result[field_name] = round(after[metric_name], 4)
    print("BENCH", json.dumps(result), flush=True)
