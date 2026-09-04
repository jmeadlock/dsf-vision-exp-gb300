#!/usr/bin/env python3
"""Concurrent exact opaque-ID fidelity through parsed tool calls."""
import concurrent.futures as futures
import json
import os
import sys
import urllib.request
import uuid

BASE = os.getenv("BASE_URL", "http://127.0.0.1:30003/v1")
MODEL = os.getenv("MODEL", "dsf-vision-exp")
KEY = os.environ["API_KEY"]
C = int(os.getenv("C", "64"))
N = int(os.getenv("N", "64"))
HEADERS = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
TOOLS = [{
    "type": "function",
    "function": {
        "name": "return_identifier",
        "description": "Return an opaque identifier without alteration.",
        "parameters": {
            "type": "object",
            "properties": {"identifier": {"type": "string"}},
            "required": ["identifier"],
            "additionalProperties": False,
        },
    },
}]


def one(index):
    token = f"OPAQUE-{index:03d}-{uuid.uuid4().hex}-zY9Q"
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": f"Call return_identifier with exactly this identifier: {token}"}],
        "tools": TOOLS,
        "tool_choice": "auto",
        "temperature": 0,
        "max_tokens": 256,
        "chat_template_kwargs": {"thinking": False},
    }
    request = urllib.request.Request(
        BASE + "/chat/completions",
        data=json.dumps(body).encode(),
        headers=HEADERS,
    )
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            message = json.load(response)["choices"][0]["message"]
        calls = message.get("tool_calls") or []
        if len(calls) != 1 or calls[0]["function"]["name"] != "return_identifier":
            return {"index": index, "expected": token, "error": "tool_call_shape"}
        args = json.loads(calls[0]["function"]["arguments"])
        got = args.get("identifier") if isinstance(args, dict) else None
        if got != token or set(args) != {"identifier"}:
            return {"index": index, "expected": token, "got": got, "args": args}
        return None
    except Exception as exc:
        return {"index": index, "expected": token, "error": f"{type(exc).__name__}: {exc}"}


with futures.ThreadPoolExecutor(max_workers=C) as pool:
    failures = [failure for failure in pool.map(one, range(N)) if failure is not None]

if failures:
    print(f"OPAQUE_RESULT FAIL n={N} mismatches={len(failures)}")
    print(json.dumps(failures[:10], indent=2))
    sys.exit(1)
print(f"OPAQUE_RESULT PASS n={N} mismatches=0")
