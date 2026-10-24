"""
"Home alone" monologue runs on Claude, toy version (system prompt + a one-word user turn).

Each sample is one API call: the system prompt from prompts/, then the user turn
"Begin.", nothing else (no tools, no app system prompt, no memory). We record where
Claude stops on its own (stop_reason "end_turn") versus hitting the token cap
("max_tokens"), the visible text, and a summary of its hidden thinking.

Usage (the API key is read from .env in this folder):
    .venv/bin/python run_claude.py --prompt neutral --effort xhigh -n 2
    .venv/bin/python run_claude.py --index      # rebuild .md copies and runs/INDEX.md

Output: runs/<model>/<prompt>/<effort>/<date>_<time>_<words>w.json (local time)
plus a readable .md copy,
and runs/INDEX.md, a table of every run so far.
"""
import argparse, json, os, time
from datetime import datetime, timezone
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"
MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "max"
MAX_TOKENS = 128000  # model maximum; covers thinking + text, so a stop is Claude's choice
USER_TURN = "Begin."
DIM, RESET = "\033[2m", "\033[0m"


def load_env(path=ROOT / ".env"):
    """Read KEY=value lines from .env into the environment (existing values win)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            value = value.strip().strip('"').strip("'")
            if value:
                os.environ.setdefault(key.strip(), value)


def run_sample(client, system, effort):
    """Stream one assistant turn, echoing the thinking summary (dimmed) and the
    text live. Returns the final Message."""
    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": USER_TURN}],
        thinking={"type": "adaptive", "display": "summarized"},  # always on for Opus 5.5
        output_config={"effort": effort},
    ) as stream:
        for event in stream:
            if event.type == "content_block_start":
                if event.content_block.type == "thinking":
                    print(f"\n{DIM}[thinking]{RESET}")
                elif event.content_block.type == "text":
                    print("\n[response]")
            elif event.type == "content_block_delta":
                if event.delta.type == "thinking_delta":
                    print(f"{DIM}{event.delta.thinking}{RESET}", end="", flush=True)
                elif event.delta.type == "text_delta":
                    print(event.delta.text, end="", flush=True)
        print()
        return stream.get_final_message()


def cost(r):
    u = r["usage"]
    return u["input_tokens"] * 4e-6 + u["output_tokens"] * 20e-6


def to_markdown(r):
    """A readable copy of one run record (open in VS Code, then Cmd+Shift+V)."""
    quoted_thinking = "\n".join("> " + line for line in (r["thinking_summary"] or "(none)").splitlines())
    return (
        f"# {r['model']} · prompt {r['prompt']} · effort {r['effort']}\n\n"
        f"stop: **{r['stop_reason']}** · {len(r['text'].split())} words · "
        f"{r['usage']['output_tokens']} output tokens · {r['seconds']} s · ${cost(r):.3f}\n\n"
        f"## Thinking (summary)\n\n{quoted_thinking}\n\n"
        f"## Monologue\n\n{r['text']}\n"
    )


def run_name(r):
    """File name for a run: local finish date-time plus word count, e.g. 2026-10-24_0045_2878w."""
    finished = datetime.fromisoformat(r["finished_at"]).astimezone()
    return f"{finished:%Y-%m-%d_%H%M%S}_{len(r['text'].split())}w"


def build_index():
    """Name each run date-time-wordcount, rewrite its .md copy, and rebuild runs/INDEX.md."""
    rows = []
    for f in sorted(RUNS.glob("*/*/*/*.json")):
        r = json.loads(f.read_text())
        if "finished_at" not in r:  # older runs: recover the UTC time from the file name
            utc = datetime.strptime(f.stem[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            r["finished_at"] = utc.isoformat()
            f.write_text(json.dumps(r, indent=2, ensure_ascii=False))
        target = f.with_name(run_name(r) + ".json")
        if target != f:
            f.with_suffix(".md").unlink(missing_ok=True)
            f = f.rename(target)
        f.with_suffix(".md").write_text(to_markdown(r))
        rows.append((r["finished_at"], r["model"], r["prompt"], r["effort"], f, r))
    rows.sort(key=lambda row: row[1:4] + row[:1])
    lines = ["# Runs\n", "| model | prompt | effort | run | stop | words | output tokens | seconds | cost |",
             "|---|---|---|---|---|---|---|---|---|"]
    total = 0.0
    for _, model, prompt, effort, f, r in rows:
        total += cost(r)
        link = f.with_suffix(".md").relative_to(RUNS)
        lines.append(f"| {model} | {prompt} | {effort} | [{f.stem}](<{link}>) | {r['stop_reason']} | "
                     f"{len(r['text'].split())} | {r['usage']['output_tokens']} | {r['seconds']} | ${cost(r):.2f} |")
    lines.append(f"\n{len(rows)} runs, total ${total:.2f}\n")
    (RUNS / "INDEX.md").write_text("\n".join(lines))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("-n", type=int, default=1, help="number of samples")
    p.add_argument("--effort", default=DEFAULT_EFFORT, choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--prompt", default="neutral", help="name of a file in prompts/, without .txt")
    p.add_argument("--index", action="store_true", help="only rebuild .md copies and runs/INDEX.md")
    args = p.parse_args()
    if args.index:
        build_index()
        return

    load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("No ANTHROPIC_API_KEY: paste it into the .env file in this folder.")
    system = (ROOT / "prompts" / f"{args.prompt}.txt").read_text().strip()
    client = anthropic.Anthropic()
    out_dir = RUNS / MODEL / args.prompt / args.effort
    out_dir.mkdir(parents=True, exist_ok=True)

    for k in range(args.n):
        print(f"\n===== sample {k + 1}/{args.n}  ({MODEL}, prompt={args.prompt}, effort={args.effort}) =====\n")
        started = time.time()
        msg = run_sample(client, system, args.effort)
        text = "".join(b.text for b in msg.content if b.type == "text")
        record = {
            "requested_model": MODEL,
            "model": msg.model,
            "prompt": args.prompt,
            "effort": args.effort,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "user_turn": USER_TURN,
            "seconds": round(time.time() - started, 1),
            "stop_reason": msg.stop_reason,
            "stop_details": msg.stop_details.model_dump() if msg.stop_details else None,
            "usage": msg.usage.model_dump(),
            "text": text,
            "thinking_summary": "\n\n".join(b.thinking for b in msg.content if b.type == "thinking"),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        path = out_dir / f"{run_name(record)}.json"
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
        build_index()
        print(f"\n[saved {path.relative_to(ROOT)}]  stop={msg.stop_reason}  "
              f"words={len(text.split())}  output_tokens={msg.usage.output_tokens}")


if __name__ == "__main__":
    main()
