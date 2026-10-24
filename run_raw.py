"""
Strict "home alone" condition: no user turn at all. We render each model's official chat
template (templates/*.jinja, from its Hugging Face repo) with only a system message, open
the assistant turn ourselves, and send the result as a raw prompt to a provider that
passes it through untemplated (see data/raw_completion_probe.json).

Usage (OPENROUTER_API_KEY is read from .env):
    .venv/bin/python run_raw.py --model z-ai/glm-5.3 --prompt "neutral strict" -n 3

Records go to runs/<model>/<prompt>/<effort>/ like every other run, with the exact raw
prompt saved alongside, and are pushed to GitHub afterwards (skip with --no-push).
"""
import argparse, json, os, time
from datetime import datetime, timezone
from pathlib import Path

import jinja2
import requests

from run_claude import DIM, RESET, ROOT, RUNS, build_index, load_env, publish, run_name

URL = "https://openrouter.ai/api/v1/completions"
MAX_TOKENS = 128000
THINK_END = "</think>"

# How to open the assistant turn after the rendered system block. GLM's template does it
# itself with add_generation_prompt; DeepSeek's only adds the header after a user turn.
MODELS = {
    "z-ai/glm-5.3": {"template": "GLM-5.3.jinja", "open_assistant": ""},
    "deepseek/deepseek-v4.1-flash": {"template": "DeepSeek-V4.1-Flash.jinja", "open_assistant": "<｜Assistant｜><think>"},
}


def render(model, system):
    env = jinja2.Environment(trim_blocks=True, lstrip_blocks=True, extensions=["jinja2.ext.loopcontrols"])
    env.globals["raise_exception"] = lambda m: (_ for _ in ()).throw(ValueError(m))
    spec = MODELS[model]
    tpl = env.from_string((ROOT / "templates" / spec["template"]).read_text())
    out = tpl.render(messages=[{"role": "system", "content": system}], add_generation_prompt=True, bos_token="", tools=None)
    if spec["open_assistant"] and not out.endswith(spec["open_assistant"]):
        out += spec["open_assistant"]
    return out


def run_sample(key, model, provider, prompt):
    """Stream a raw completion; the model's reasoning ends at </think>, the rest is the monologue."""
    body = {
        "model": model, "prompt": prompt, "max_tokens": MAX_TOKENS, "stream": True,
        "usage": {"include": True}, "provider": {"only": [provider], "allow_fallbacks": False},
    }
    r = requests.post(URL, headers={"Authorization": f"Bearer {key}"}, json=body, stream=True, timeout=(30, 600))
    r.raise_for_status()
    r.encoding = "utf-8"
    out, info, in_response = [], {}, False
    print(f"\n{DIM}[thinking]{RESET}")
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: ") or line == "data: [DONE]":
            continue
        chunk = json.loads(line[6:])
        if "error" in chunk:
            info["error"] = chunk["error"]
            break
        if chunk.get("usage"):
            info["usage"] = chunk["usage"]
        for choice in chunk.get("choices", []):
            piece = choice.get("text") or ""
            out.append(piece)
            if not in_response and THINK_END in "".join(out[-3:]):
                in_response = True
                print(f"\n[response]")
            print(piece if in_response else f"{DIM}{piece}{RESET}", end="", flush=True)
            if choice.get("finish_reason"):
                info["finish_reason"] = choice["finish_reason"]
    print()
    full = "".join(out)
    reasoning, sep, text = full.partition(THINK_END)
    return (text.strip(), reasoning.strip(), info) if sep else ("", full.strip(), info)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True, choices=sorted(MODELS))
    p.add_argument("--provider", default="Together")
    p.add_argument("-n", type=int, default=1)
    p.add_argument("--prompt", default="neutral strict", help="name of a file in prompts/, without .txt")
    p.add_argument("--show-prompt", action="store_true", help="print the raw prompt and exit")
    p.add_argument("--no-push", action="store_true")
    args = p.parse_args()

    system = (ROOT / "prompts" / f"{args.prompt}.txt").read_text().strip()
    raw = render(args.model, system)
    if args.show_prompt:
        print(raw)
        return
    load_env()
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("No OPENROUTER_API_KEY: paste it into the .env file in this folder.")
    name = args.model.split("/")[-1]
    effort = "template-default"  # reasoning effort as the official template sets it, no user turn to carry a setting
    out_dir = RUNS / name / args.prompt / effort
    out_dir.mkdir(parents=True, exist_ok=True)

    for k in range(args.n):
        print(f"\n===== sample {k + 1}/{args.n}  ({args.model} raw via {args.provider}, prompt={args.prompt}, no user turn) =====\n")
        started = time.time()
        text, reasoning, info = run_sample(key, args.model, args.provider, raw)
        u = info.get("usage", {})
        fin = info.get("finish_reason")
        stop = "error" if "error" in info else {"stop": "end_turn", "length": "max_tokens", "content_filter": "refusal"}.get(fin, fin)
        record = {
            "requested_model": args.model,
            "model": name,
            "provider": args.provider,
            "prompt": args.prompt,
            "effort": effort,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "user_turn": None,
            "raw_prompt": raw,
            "seconds": round(time.time() - started, 1),
            "stop_reason": stop,
            "stop_details": info.get("error"),
            "usage": {
                "input_tokens": u.get("prompt_tokens", 0),
                "output_tokens": u.get("completion_tokens", 0),
                "output_tokens_details": {"thinking_tokens": None},  # not reported for raw completions
            },
            "cost_usd": u.get("cost", 0.0),
            "text": text,
            "thinking_summary": reasoning,  # the model's full reasoning, up to </think>
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        path = out_dir / f"{run_name(record)}.json"
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
        if args.no_push:
            build_index()
        print(f"\n[saved {path.relative_to(ROOT)}]  stop={stop}  words={len(text.split())}  "
              f"output_tokens={record['usage']['output_tokens']}  cost=${record['cost_usd']:.3f}")
        if not args.no_push:
            publish(f"Add run: {name} / {args.prompt} / no user turn / {path.stem}")


if __name__ == "__main__":
    main()
