#!/usr/bin/env python3
"""dsfv_smoke.py — protocol + vision smoke for DeepSeek-V4-Flash-Vision-Exp.
Asserts: models list, parsed tool_calls, reasoning separated, REAL image understanding (2 synthetic images with
known content), multi-image, post-image short probe. Prints PASS/FAIL lines only."""
import base64, io, json, os, sys, time, urllib.request
base = os.getenv("BASE_URL", "http://127.0.0.1:30003/v1"); model = os.getenv("MODEL", "dsf-vision-exp")
H = {"Authorization": f"Bearer {os.environ['API_KEY']}", "Content-Type": "application/json"}
def post(p, timeout=900):
    t0 = time.monotonic()
    with urllib.request.urlopen(urllib.request.Request(base + "/chat/completions", data=json.dumps(p).encode(), headers=H), timeout=timeout) as r:
        return json.load(r), time.monotonic() - t0
def chat(content, **kw):
    p = {"model": model, "messages": [{"role": "user", "content": content}], "temperature": 0, "max_tokens": kw.pop("max_tokens", 400),
         "chat_template_kwargs": {"thinking": kw.pop("thinking", False)}}
    p.update(kw); return post(p)
from PIL import Image, ImageDraw, ImageFont
def img_b64(draw_fn, size=(640, 400)):
    im = Image.new("RGB", size, "white"); d = ImageDraw.Draw(im); draw_fn(d); b = io.BytesIO(); im.save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()
try: font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
except Exception: font = ImageFont.load_default()
def draw_text(d): d.text((40, 150), "ZEBRA 7391", fill="black", font=font)
def draw_shapes(d):
    d.rectangle((60, 60, 260, 260), fill="red"); d.ellipse((360, 60, 580, 280), fill="blue"); d.polygon([(100,330),(200,290),(300,330)], fill="green")
def draw_chart(d):
    vals = {"A": 30, "B": 90, "C": 55}; x = 80
    for k, v in vals.items():
        d.rectangle((x, 350 - v * 3, x + 100, 350), fill="gray"); d.text((x + 30, 355), k, fill="black", font=font); x += 180
    d.text((20, 10), "Bar heights: A=30 B=90 C=55", fill="black", font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20) if font else None)
fails = 0
def check(name, ok, detail=""):
    global fails; print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)
    if not ok: fails += 1
# 1 models
m = json.load(urllib.request.urlopen(urllib.request.Request(base + "/models", headers=H), timeout=30))
check("models", model in [x["id"] for x in m["data"]], str([x["id"] for x in m["data"]]))
# 2 text + reasoning separation (thinking on)
r, dt = chat("What is 17*19? Answer with just the integer.", thinking=True, max_tokens=2000)
msg = r["choices"][0]["message"]; c = (msg.get("content") or "").strip(); rc = msg.get("reasoning_content") or msg.get("reasoning") or ""
check("text_answer", "323" in c, f"content={c[:60]!r} {dt:.1f}s")
check("reasoning_separated", bool(rc) and "<think" not in c, f"reasoning_len={len(rc)}")
# 3 tool call
tools = [{"type": "function", "function": {"name": "get_weather", "description": "Get current weather", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]
r, dt = chat("You must call get_weather for Paris now. Do not answer directly.", tools=tools, tool_choice="auto")
tc = r["choices"][0]["message"].get("tool_calls") or []
check("tool_calls_parsed", bool(tc) and tc[0]["function"]["name"] == "get_weather" and "Paris" in tc[0]["function"]["arguments"], json.dumps(tc)[:160])
# 4 vision: text in image
r, dt = chat([{"type": "image_url", "image_url": {"url": img_b64(draw_text)}}, {"type": "text", "text": "Transcribe exactly the text in this image. Reply with the text only."}])
c = r["choices"][0]["message"].get("content") or ""; ptok = r.get("usage", {}).get("prompt_tokens")
check("vision_ocr", "ZEBRA" in c.upper() and "7391" in c, f"content={c[:80]!r} prompt_tokens={ptok} {dt:.1f}s")
check("vision_tokens_present", (ptok or 0) > 100, f"prompt_tokens={ptok} (text-only fallback would be ~30)")
# 5 vision: shapes/colors
r, dt = chat([{"type": "image_url", "image_url": {"url": img_b64(draw_shapes)}}, {"type": "text", "text": "List the shapes and their colors in this image, comma separated."}])
c = (r["choices"][0]["message"].get("content") or "").lower()
check("vision_shapes", "red" in c and "blue" in c and "green" in c and ("square" in c or "rectangle" in c) and ("circle" in c or "ellipse" in c), c[:120])
# 6 vision: chart reading
r, dt = chat([{"type": "image_url", "image_url": {"url": img_b64(draw_chart)}}, {"type": "text", "text": "Which bar is tallest? Reply with the single letter."}])
c = (r["choices"][0]["message"].get("content") or "").strip()
check("vision_chart", c.upper().startswith("B"), c[:40])
# 7 multi-image
r, dt = chat([{"type": "image_url", "image_url": {"url": img_b64(draw_text)}}, {"type": "image_url", "image_url": {"url": img_b64(draw_shapes)}}, {"type": "text", "text": "How many images did I send, and which one contains text? Answer briefly."}])
c = (r["choices"][0]["message"].get("content") or "").lower()
check("multi_image", ("two" in c or "2" in c) and "first" in c, c[:120])
# 8 post-probe
r, dt = chat("Reply with the word OK.")
check("post_probe", "ok" in (r["choices"][0]["message"].get("content") or "").lower(), f"{dt:.1f}s")
print("SMOKE_RESULT", "PASS" if fails == 0 else f"FAIL({fails})")
sys.exit(1 if fails else 0)
