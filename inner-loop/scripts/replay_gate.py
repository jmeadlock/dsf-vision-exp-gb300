#!/usr/bin/env python3
"""Six sequential tool calls with the complete prior DSML history replayed."""
import json
import os
import sys
import urllib.request
import uuid

BASE = os.getenv("BASE_URL", "http://127.0.0.1:30003/v1")
MODEL = os.getenv("MODEL", "dsf-vision-exp")
KEY = os.environ["API_KEY"]
HEADERS = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
TOOLS = [{
    "type": "function",
    "function": {
        "name": "record_identifier",
        "description": "Record an opaque identifier exactly as supplied.",
        "parameters": {
            "type": "object",
            "properties": {"identifier": {"type": "string"}},
            "required": ["identifier"],
            "additionalProperties": False,
        },
    },
}]


def chat(messages):
    body = {
        "model": MODEL,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "temperature": 0,
        "max_tokens": 400,
        "chat_template_kwargs": {"thinking": False},
    }
    request = urllib.request.Request(
        BASE + "/chat/completions",
        data=json.dumps(body).encode(),
        headers=HEADERS,
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


messages = [{"role": "system", "content": "Use the requested tool. Preserve every identifier byte-for-byte."}]
for turn in range(6):
    token = f"REPLAY-{turn}-{uuid.uuid4().hex}-Aa9Z"
    messages.append({"role": "user", "content": f"Call record_identifier with exactly this identifier: {token}"})
    response = chat(messages)
    message = response["choices"][0]["message"]
    calls = message.get("tool_calls") or []
    if len(calls) != 1 or calls[0]["function"]["name"] != "record_identifier":
        print(f"REPLAY_RESULT FAIL turn={turn + 1} reason=tool_call_shape")
        sys.exit(1)
    raw_args = calls[0]["function"]["arguments"]
    try:
        args = json.loads(raw_args)
    except Exception as exc:
        print(f"REPLAY_RESULT FAIL turn={turn + 1} reason=json:{type(exc).__name__}")
        sys.exit(1)
    if args != {"identifier": token}:
        print(f"REPLAY_RESULT FAIL turn={turn + 1} expected={token!r} got={args!r}")
        sys.exit(1)
    assistant = {
        "role": "assistant",
        "content": message.get("content"),
        "tool_calls": calls,
    }
    if message.get("reasoning_content") is not None:
        assistant["reasoning_content"] = message["reasoning_content"]
    messages.extend([
        assistant,
        {"role": "tool", "tool_call_id": calls[0]["id"], "content": json.dumps({"recorded": token})},
    ])
    print(f"REPLAY_TURN PASS turn={turn + 1}", flush=True)

print("REPLAY_RESULT PASS turns=6")
