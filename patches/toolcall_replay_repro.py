import json, os, sys, urllib.request

BASE = os.environ.get("BASE_URL", "http://192.168.1.9:30003/v1")
KEY = os.environ.get("API_KEY", "")
MODEL = os.environ.get("MODEL", "dsf-vision-exp")

TOOLS = [
    {"type": "function", "function": {"name": "terminal", "description": "Run a shell command",
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Write a file",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
]


def chat(messages, **kw):
    body = {"model": MODEL, "messages": messages, "tools": TOOLS, "temperature": 0, "max_tokens": 600}
    body.update(kw)
    req = urllib.request.Request(BASE + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"})
    return json.load(urllib.request.urlopen(req, timeout=180))


def show(tag, resp):
    m = resp["choices"][0]["message"]
    tcs = m.get("tool_calls") or []
    print(f"\n== {tag} == finish={resp['choices'][0]['finish_reason']} n_tool_calls={len(tcs)}")
    if m.get("content"):
        print("content:", m["content"][:300].replace("\n", "\\n"))
    for tc in tcs:
        print("  name:", tc["function"]["name"])
        print("  raw args:", tc["function"]["arguments"][:300])
    return tcs


sysmsg = {"role": "system", "content": "You are a terse ops agent. Use tools; do not explain."}

# Turn 1: single-shot tool call
msgs = [sysmsg, {"role": "user", "content": "Run `uname -a` using the terminal tool."}]
r1 = chat(msgs)
tcs = show("T1 single-shot", r1)
if not tcs:
    sys.exit("no tool call on T1; stop")

# Turn 2: replay history with FLAT args exactly as Hermes would, add tool result, ask for another call
assistant_msg = {"role": "assistant", "content": r1["choices"][0]["message"].get("content"),
                 "tool_calls": [{"id": tcs[0]["id"], "type": "function",
                                 "function": {"name": tcs[0]["function"]["name"],
                                              "arguments": tcs[0]["function"]["arguments"]}}]}
if r1["choices"][0]["message"].get("reasoning_content"):
    assistant_msg["reasoning_content"] = r1["choices"][0]["message"]["reasoning_content"]
msgs2 = msgs + [assistant_msg,
                {"role": "tool", "tool_call_id": tcs[0]["id"], "content": "Linux gb300 6.17.0 aarch64 GNU/Linux"},
                {"role": "user", "content": "Now write the text 'hello' to /tmp/x.txt using write_file."}]
r2 = chat(msgs2)
tcs2 = show("T2 after one replayed tool turn", r2)

# Turn 3: replay again
if tcs2:
    a2 = {"role": "assistant", "content": r2["choices"][0]["message"].get("content"),
          "tool_calls": [{"id": tcs2[0]["id"], "type": "function",
                          "function": {"name": tcs2[0]["function"]["name"], "arguments": tcs2[0]["function"]["arguments"]}}]}
    msgs3 = msgs2 + [a2, {"role": "tool", "tool_call_id": tcs2[0]["id"], "content": '{"success": true}'},
                     {"role": "user", "content": "Now run `df -h /` with terminal."}]
    r3 = chat(msgs3)
    show("T3 after two replayed tool turns", r3)

# Dump the prompt as the server renders it, if the endpoint exposes it
try:
    body = {"model": MODEL, "messages": msgs2, "tools": TOOLS}
    req = urllib.request.Request(BASE.replace("/v1", "") + "/v1/chat/completions", data=json.dumps(body | {"max_tokens": 1, "return_text_in_logprobs": False}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"})
except Exception as e:
    print("prompt dump skipped:", e)
