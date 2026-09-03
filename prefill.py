#!/usr/bin/env python3
"""prefill.py — cold-prefill probe. Nonce at prompt START (defeats prefix cache), max_tokens=1,
rate = prompt_tokens / total request time (TTFT-equivalent when generating one token)."""
import json, os, random, time, urllib.request, uuid
base = os.getenv("BASE_URL", "http://127.0.0.1:30003/v1"); model = os.getenv("MODEL", "dsf-vision-exp")
H = {"Authorization": f"Bearer {os.environ['API_KEY']}", "Content-Type": "application/json"}
SIZES = [int(x) for x in os.getenv("SIZES", "8000 32000 64000 128000 256000").split()]
N = int(os.getenv("N", "3"))
WORDS = "apple river stone cloud iron velvet copper meadow lantern orbit cedar prism harbor tundra quartz fable".split()
def post(p, timeout=3600):
    t0 = time.monotonic()
    with urllib.request.urlopen(urllib.request.Request(base + "/chat/completions", data=json.dumps(p).encode(), headers=H), timeout=timeout) as r:
        return json.load(r), time.monotonic() - t0
# warm-up
post({"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1})
for target in SIZES:
    rates = []; ptoks = []
    for i in range(N):
        rnd = random.Random(); words = int(target * 0.72)
        body = f"NONCE {uuid.uuid4().hex}\n" + " ".join(rnd.choice(WORDS) for _ in range(words))
        r, dt = post({"model": model, "messages": [{"role": "user", "content": body + "\nReply with one word."}], "max_tokens": 1, "temperature": 0, "chat_template_kwargs": {"thinking": False}})
        pt = r["usage"]["prompt_tokens"]; rates.append(pt / dt); ptoks.append((pt, dt))
    print(f"PREFILL target={target} prompt_tokens={ptoks[0][0]} ttft_s={[round(d,2) for _,d in ptoks]} tok_s={[round(x) for x in rates]} mean_tok_s={sum(rates)/len(rates):.0f}", flush=True)
