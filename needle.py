#!/usr/bin/env python3
"""needle.py — long-context ladder for DSF Vision-Exp. Random-word filler (defeats prefix cache), two unique
markers (early + middle), exact recall at end, immediate post-probe. Prints one line per rung."""
import json, os, random, sys, time, urllib.request, uuid
base = os.getenv("BASE_URL", "http://127.0.0.1:30003/v1"); model = os.getenv("MODEL", "dsf-vision-exp")
H = {"Authorization": f"Bearer {os.environ['API_KEY']}", "Content-Type": "application/json"}
RUNGS = [int(x) for x in os.getenv("RUNGS", "32000 128000 256000 512000 1000000").split()]
WORDS = "apple river stone cloud iron velvet copper meadow lantern orbit cedar prism harbor tundra quartz fable".split()
def post(p, timeout=3600):
    t0 = time.monotonic()
    with urllib.request.urlopen(urllib.request.Request(base + "/chat/completions", data=json.dumps(p).encode(), headers=H), timeout=timeout) as r:
        return json.load(r), time.monotonic() - t0
def filler(n_words, seed):
    rnd = random.Random(seed); return " ".join(rnd.choice(WORDS) for _ in range(n_words))
for target in RUNGS:
    words = int(target * 0.72)  # ~1.4 tok/word on this tokenizer for random words; verified via prompt_tokens
    a, b = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]
    seed = random.randrange(10**9)
    body = filler(words // 2, seed) + f"\nSECRET_ALPHA={a}\n" + filler(words // 2, seed + 1)
    body = f"NOTE_BETA={b}\n" + body
    prompt = body + "\n\nQuestion: What are the exact values of NOTE_BETA and SECRET_ALPHA? Reply as 'BETA=<v> ALPHA=<v>' only."
    try:
        r, dt = post({"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0, "max_tokens": 64,
                      "chat_template_kwargs": {"thinking": False}})
        c = r["choices"][0]["message"].get("content") or ""; pt = r["usage"]["prompt_tokens"]
        ok = (b in c) and (a in c)
        r2, dt2 = post({"model": model, "messages": [{"role": "user", "content": "Reply with the word OK."}], "temperature": 0, "max_tokens": 8, "chat_template_kwargs": {"thinking": False}})
        post_ok = "ok" in (r2["choices"][0]["message"].get("content") or "").lower()
        print(f"RUNG target={target} prompt_tokens={pt} ttft_total={dt:.1f}s prefill_tok_s={pt/dt:.0f} recall={'PASS' if ok else 'FAIL'} post_probe={'PASS' if post_ok else 'FAIL'} content={c.strip()[:60]!r}", flush=True)
        if not ok: break
    except Exception as e:
        print(f"RUNG target={target} ERROR {type(e).__name__}: {str(e)[:200]}", flush=True); break
