#!/usr/bin/env python3
"""repaudit.py — C64 natural-decode repetition audit (catid rule): temp 0, EOS respected, ~8K wiki-ish prompt,
flag 4 consecutive phrase repeats or repeated-8gram fraction >= 0.20. Prints rate + throughput."""
import json, os, time, urllib.request, concurrent.futures as cf, random, collections
base = os.getenv("BASE_URL", "http://127.0.0.1:30003/v1"); model = os.getenv("MODEL", "dsf-vision-exp")
H = {"Authorization": f"Bearer {os.environ['API_KEY']}", "Content-Type": "application/json"}
C = int(os.getenv("C", "64")); N = int(os.getenv("N", str(C*2)))
TOPICS = ["the history of the Roman aqueducts","how lithium-ion batteries degrade","the economics of container shipping","plate tectonics and mountain formation","the development of the printing press","how vaccines train the immune system","the architecture of Gothic cathedrals","the water cycle in arid climates","the origins of jazz in New Orleans","how GPS trilateration works","the domestication of the horse","the chemistry of bread leavening","the Silk Road trade network","how coral reefs form","the invention of the telegraph","glacial retreat since the Little Ice Age"]
def prompt(i):
    rnd = random.Random(i); t = rnd.choice(TOPICS)
    filler = " ".join(f"Section {k}: background note {rnd.randrange(10**6)} on {rnd.choice(TOPICS)}." for k in range(700))
    return f"Reference notes:\n{filler}\n\nWrite a detailed, well-structured 900-word essay about {t}. Use paragraphs, no headings."
def audit(text):
    w = text.split(); n8 = [" ".join(w[i:i+8]) for i in range(max(0, len(w)-7))]
    frac = 1 - len(set(n8))/len(n8) if n8 else 0
    sents = [s.strip() for s in text.replace("\n"," ").split(".") if s.strip()]
    run = 1; worst = 1
    for a, b in zip(sents, sents[1:]):
        run = run+1 if a == b else 1; worst = max(worst, run)
    return frac, worst, (frac >= 0.20 or worst >= 4)
def one(i):
    p = {"model": model, "messages": [{"role": "user", "content": prompt(i)}], "temperature": 0, "max_tokens": 1500, "chat_template_kwargs": {"thinking": False}}
    t0 = time.monotonic()
    with urllib.request.urlopen(urllib.request.Request(base+"/chat/completions", data=json.dumps(p).encode(), headers=H), timeout=1800) as r:
        j = json.load(r)
    c = j["choices"][0]["message"].get("content") or ""; u = j["usage"]
    return dict(i=i, dt=time.monotonic()-t0, ptok=u["prompt_tokens"], ctok=u["completion_tokens"], finish=j["choices"][0]["finish_reason"], audit=audit(c), text=c)
t0 = time.monotonic()
with cf.ThreadPoolExecutor(C) as ex: res = list(ex.map(one, range(N)))
wall = time.monotonic()-t0
flag = sum(1 for r in res if r["audit"][2]); ctok = sum(r["ctok"] for r in res)
print(f"REPAUDIT C={C} N={N} flagged={flag} rate={flag/N:.1%} agg_out_tok_s={ctok/wall:.0f} mean_ptok={sum(r['ptok'] for r in res)/N:.0f} mean_ctok={ctok/N:.0f} finish={collections.Counter(r['finish'] for r in res)} worst_8gram={max(r['audit'][0] for r in res):.3f}")
json.dump([{k: v for k, v in r.items() if k != 'text'} | {"text_head": r["text"][:300]} for r in res], open(os.getenv("OUT", "repaudit.json"), "w"), indent=1)
