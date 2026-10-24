"""
"Home alone" runs on OpenAI models, matching run_claude.py: the system prompt from
prompts/ (sent as `instructions`), then the user turn "Begin.", nothing else.

Usage (OPENAI_API_KEY is read from .env):
    .venv/bin/python run_openai.py --list-models
    .venv/bin/python run_openai.py --model <id> --prompt neutral --effort xhigh -n 3

Records use the same format and folders as the Claude runs, so runs/INDEX.md and
the reader pick them up; each run is pushed to GitHub afterwards (skip with --no-push).
OpenAI adds its own hidden system-level text on its side, which we cannot see.
"""
import argparse, json, os, time
from datetime import datetime, timezone

from openai import OpenAI

from run_claude import DIM, RESET, ROOT, RUNS, USER_TURN, build_index, load_env, publish, run_name

MAX_TOKENS = 128000
# $ per token (input, output); reasoning is billed as output. From OpenAI's launch posts, Oct 2026.
PRICES = {"gpt-6.1-sol": (2e-6, 10e-6), "gpt-6-astra": (10e-6, 50e-6)}


def run_sample(client, model, system, effort):
    """Stream one response, echoing the reasoning summary (dimmed) and the text live."""
    stream = client.responses.create(
        model=model,
        instructions=system,
        input=USER_TURN,
        reasoning={"effort": effort, "summary": "auto"},
        max_output_tokens=MAX_TOKENS,
        store=False,
        stream=True,
    )
    final, section = None, None
    for event in stream:
        if event.type == "response.reasoning_summary_text.delta":
            if section != "thinking":
                print(f"\n{DIM}[thinking]{RESET}")
                section = "thinking"
            print(f"{DIM}{event.delta}{RESET}", end="", flush=True)
        elif event.type == "response.output_text.delta":
            if section != "response":
                print("\n[response]")
                section = "response"
            print(event.delta, end="", flush=True)
        elif event.type in ("response.completed", "response.incomplete", "response.failed"):
            final = event.response
    print()
    return final


def stop_reason(resp):
    """Map OpenAI's status onto the Claude stop reasons the reader understands."""
    refused = any(c.type == "refusal" for item in resp.output if item.type == "message" for c in item.content)
    if refused:
        return "refusal"
    if resp.status == "completed":
        return "end_turn"
    reason = resp.incomplete_details.reason if resp.incomplete_details else resp.status
    return {"max_output_tokens": "max_tokens", "content_filter": "refusal"}.get(reason, reason)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model")
    p.add_argument("-n", type=int, default=1)
    p.add_argument("--effort", default="xhigh", choices=["none", "low", "medium", "high", "xhigh", "max"])
    p.add_argument("--prompt", default="neutral", help="name of a file in prompts/, without .txt")
    p.add_argument("--list-models", action="store_true")
    p.add_argument("--no-push", action="store_true")
    args = p.parse_args()

    load_env()
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("No OPENAI_API_KEY: add a line OPENAI_API_KEY=... to the .env file in this folder.")
    client = OpenAI()
    if args.list_models:
        for m in sorted(client.models.list(), key=lambda m: -m.created):
            print(datetime.fromtimestamp(m.created).date(), m.id)
        return
    if not args.model:
        raise SystemExit("Pass --model (see --list-models).")
    if args.model not in PRICES:
        raise SystemExit(f"Add {args.model}'s prices to PRICES in run_openai.py first.")

    system = (ROOT / "prompts" / f"{args.prompt}.txt").read_text().strip()
    out_dir = RUNS / args.model / args.prompt / args.effort
    out_dir.mkdir(parents=True, exist_ok=True)

    for k in range(args.n):
        print(f"\n===== sample {k + 1}/{args.n}  ({args.model}, prompt={args.prompt}, effort={args.effort}) =====\n")
        started = time.time()
        resp = run_sample(client, args.model, system, args.effort)
        text = resp.output_text or ""
        summary = "\n\n".join(s.text for item in resp.output if item.type == "reasoning" for s in (item.summary or []))
        u = resp.usage
        record = {
            "requested_model": args.model,
            "model": resp.model,
            "prompt": args.prompt,
            "effort": args.effort,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "user_turn": USER_TURN,
            "seconds": round(time.time() - started, 1),
            "stop_reason": stop_reason(resp),
            "stop_details": resp.incomplete_details.model_dump() if resp.incomplete_details else None,
            "usage": {
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
                "output_tokens_details": {"thinking_tokens": u.output_tokens_details.reasoning_tokens},
            },
            "price": PRICES[args.model],
            "text": text,
            "thinking_summary": summary,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        path = out_dir / f"{run_name(record)}.json"
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
        if args.no_push:
            build_index()
        print(f"\n[saved {path.relative_to(ROOT)}]  stop={record['stop_reason']}  "
              f"words={len(text.split())}  output_tokens={u.output_tokens}")
        if not args.no_push:
            publish(f"Add run: {args.model} / {args.prompt} / {args.effort} / {path.stem}")


if __name__ == "__main__":
    main()
