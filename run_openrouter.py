"""
"Home alone" runs on open-weight models through OpenRouter, "Begin." version: the system
prompt from prompts/, then the user turn "Begin.", nothing else, through each model's
own chat template. Pinned to one provider so every sample hits the same deployment.

Usage (OPENROUTER_API_KEY is read from .env):
    .venv/bin/python run_openrouter.py --model z-ai/glm-5.3 --prompt "neutral v2" -n 3

Records use the same format and folders as the Claude runs (runs/<model>/<prompt>/<effort>/)
and are pushed to GitHub afterwards (skip with --no-push). Cost is OpenRouter's own figure.
"""
import argparse, json, os, time
from datetime import datetime, timezone

import requests

from run_claude import DIM, RESET, ROOT, RUNS, USER_TURN, build_index, load_env, publish, run_name

URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_TOKENS = 128000
STOP = {"stop": "end_turn", "length": "max_tokens", "content_filter": "refusal"}


def run_sample(key, model, provider, system, effort):
    """Stream one completion, echoing reasoning (dimmed) and text live. Returns (text, reasoning, info)."""
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": USER_TURN}],
        "max_tokens": MAX_TOKENS,
        "reasoning": {"effort": effort},
        "usage": {"include": True},
        "provider": {"only": [provider], "allow_fallbacks": False},
        "stream": True,
    }
    r = requests.post(URL, headers={"Authorization": f"Bearer {key}"}, json=body, stream=True, timeout=(30, 600))
    r.raise_for_status()
    text, reasoning, info, section = [], [], {}, None
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: ") or line == "data: [DONE]":
            continue
        chunk = json.loads(line[6:])
        if "error" in chunk:
            info["error"] = chunk["error"]
            break
        info["model"] = chunk.get("model", info.get("model"))
        if chunk.get("usage"):
            info["usage"] = chunk["usage"]
        for choice in chunk.get("choices", []):
            delta = choice.get("delta", {})
            if delta.get("reasoning"):
                if section != "thinking":
                    print(f"\n{DIM}[thinking]{RESET}")
                    section = "thinking"
                reasoning.append(delta["reasoning"])
                print(f"{DIM}{delta['reasoning']}{RESET}", end="", flush=True)
            if delta.get("content"):
                if section != "response":
                    print("\n[response]")
                    section = "response"
                text.append(delta["content"])
                print(delta["content"], end="", flush=True)
            if choice.get("finish_reason"):
                info["finish_reason"] = choice["finish_reason"]
    print()
    return "".join(text), "".join(reasoning), info


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True, help="OpenRouter model id, e.g. z-ai/glm-5.3")
    p.add_argument("--provider", default="Together")
    p.add_argument("-n", type=int, default=1)
    p.add_argument("--effort", default="xhigh", help="OpenRouter reasoning effort")
    p.add_argument("--prompt", default="neutral", help="name of a file in prompts/, without .txt")
    p.add_argument("--no-push", action="store_true")
    args = p.parse_args()

    load_env()
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("No OPENROUTER_API_KEY: paste it into the .env file in this folder.")
    system = (ROOT / "prompts" / f"{args.prompt}.txt").read_text().strip()
    name = args.model.split("/")[-1]
    out_dir = RUNS / name / args.prompt / args.effort
    out_dir.mkdir(parents=True, exist_ok=True)

    for k in range(args.n):
        print(f"\n===== sample {k + 1}/{args.n}  ({args.model} via {args.provider}, prompt={args.prompt}, effort={args.effort}) =====\n")
        started = time.time()
        text, reasoning, info = run_sample(key, args.model, args.provider, system, args.effort)
        u = info.get("usage", {})
        details = u.get("completion_tokens_details") or {}
        stop = "error" if "error" in info else STOP.get(info.get("finish_reason"), info.get("finish_reason"))
        record = {
            "requested_model": args.model,
            "model": name,
            "provider": args.provider,
            "prompt": args.prompt,
            "effort": args.effort,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "user_turn": USER_TURN,
            "seconds": round(time.time() - started, 1),
            "stop_reason": stop,
            "stop_details": info.get("error"),
            "usage": {
                "input_tokens": u.get("prompt_tokens", 0),
                "output_tokens": u.get("completion_tokens", 0),
                "output_tokens_details": {"thinking_tokens": details.get("reasoning_tokens", 0)},
            },
            "cost_usd": u.get("cost", 0.0),
            "text": text,
            "thinking_summary": reasoning,  # open models return their full reasoning, not a summary
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        path = out_dir / f"{run_name(record)}.json"
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
        if args.no_push:
            build_index()
        print(f"\n[saved {path.relative_to(ROOT)}]  stop={stop}  words={len(text.split())}  "
              f"output_tokens={record['usage']['output_tokens']}  cost=${record['cost_usd']:.3f}")
        if not args.no_push:
            publish(f"Add run: {name} / {args.prompt} / {args.effort} / {path.stem}")


if __name__ == "__main__":
    main()
