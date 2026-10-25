"""
"Home alone" monologue runs on Claude, toy version (system prompt + a one-word user turn).

Each sample is one API call: the system prompt from prompts/, then the user turn
"Begin.", nothing else (no tools, no app system prompt, no memory). We record where
Claude stops on its own (stop_reason "end_turn") versus hitting the token cap
("max_tokens"), the visible text, and a summary of its hidden thinking.

Usage (the API key is read from .env in this folder):
    .venv/bin/python run_claude.py --prompt neutral --effort xhigh -n 2
    .venv/bin/python run_claude.py --index      # rebuild .md copies and runs/INDEX.md
After each run it rebuilds the reader and pushes to GitHub (skip with --no-push).

Output: runs/<model>/<prompt>/<effort>/<date>_<time>_<words>w.json (local time)
plus a readable .md copy,
and runs/INDEX.md, a table of every run so far.
"""
import argparse, fcntl, json, os, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"
DEFAULT_MODEL = "claude-opus-5-5"
# $ per token (input, output); thinking is billed as output.
PRICES = {"claude-opus-5-5": (4e-6, 20e-6), "claude-fable-5-1": (10e-6, 50e-6)}
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


def run_sample(client, model, system, effort):
    """Stream one assistant turn, echoing the thinking summary (dimmed) and the
    text live. Returns the final Message."""
    with client.messages.stream(
        model=model,
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
    if "cost_usd" in r:  # OpenRouter reports the actual charge
        return r["cost_usd"]
    price_in, price_out = r.get("price") or PRICES[r["requested_model"]]
    return u["input_tokens"] * price_in + u["output_tokens"] * price_out


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


def unique_path(out_dir, r):
    """out_dir/<run_name>.json, with a -2, -3… suffix if a run already has that name
    (parallel runs can finish in the same second with the same word count)."""
    stem, k = run_name(r), 1
    path = out_dir / f"{stem}.json"
    while path.exists():
        k += 1
        path = out_dir / f"{stem}-{k}.json"
    return path


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
        if target != f and not f.stem.startswith(run_name(r) + "-"):  # keep collision suffixes
            if target.exists():
                target = unique_path(f.parent, r)
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


def publish(message):
    """Rebuild the reader and push runs + page to GitHub (Pages serves docs/index.html).
    A lock file keeps parallel runs from committing at the same time."""
    with open(ROOT / ".git" / "publish.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        build_index()
        subprocess.run([sys.executable, str(ROOT / "reader" / "build_reader.py")], cwd=ROOT, check=True)
        git = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)
        git("add", "runs", "docs", "reader")
        if git("diff", "--cached", "--quiet").returncode == 0:
            return
        git("commit", "-q", "-m", f"{message}\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
        pushed = git("push", "-q")
        print("[pushed to GitHub]" if pushed.returncode == 0 else f"[push failed: {pushed.stderr.strip()}]")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("-n", type=int, default=1, help="number of samples")
    p.add_argument("--effort", default=DEFAULT_EFFORT, choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(PRICES))
    p.add_argument("--prompt", default="neutral", help="name of a file in prompts/, without .txt")
    p.add_argument("--index", action="store_true", help="only rebuild .md copies and runs/INDEX.md")
    p.add_argument("--no-push", action="store_true", help="save locally without pushing to GitHub")
    args = p.parse_args()
    if args.index:
        build_index()
        return

    load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("No ANTHROPIC_API_KEY: paste it into the .env file in this folder.")
    system = (ROOT / "prompts" / f"{args.prompt}.txt").read_text().strip()
    client = anthropic.Anthropic()
    out_dir = RUNS / args.model / args.prompt / args.effort
    out_dir.mkdir(parents=True, exist_ok=True)

    for k in range(args.n):
        print(f"\n===== sample {k + 1}/{args.n}  ({args.model}, prompt={args.prompt}, effort={args.effort}) =====\n")
        started = time.time()
        msg = run_sample(client, args.model, system, args.effort)
        text = "".join(b.text for b in msg.content if b.type == "text")
        record = {
            "requested_model": args.model,
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
        path = unique_path(out_dir, record)
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
        if args.no_push:
            build_index()
        print(f"\n[saved {path.relative_to(ROOT)}]  stop={msg.stop_reason}  "
              f"words={len(text.split())}  output_tokens={msg.usage.output_tokens}")
        if not args.no_push:
            publish(f"Add run: {args.model} / {args.prompt} / {args.effort} / {path.stem}")


if __name__ == "__main__":
    main()
