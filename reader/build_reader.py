"""
Bundle every run in runs/ into one self-contained reading page: reader/home-alone-reader.html.
Run after new runs finish, then republish the page:
    .venv/bin/python reader/build_reader.py
"""
import json, re, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from run_claude import cost  # noqa: E402  (per-model pricing lives there)
EFFORT_ORDER = ["low", "medium", "high", "xhigh", "max"]


def first_line(text):
    for line in text.splitlines():
        line = re.sub(r"[#*_`>]", "", line).strip()
        if line and line.lower() not in {"begin.", "\"begin.\""}:
            return line[:220]
    return ""


def main():
    runs, prompts = [], {}
    for f in RUNS.glob("*/*/*/*.json"):
        r = json.loads(f.read_text())
        u = r["usage"]
        thinking_tokens = (u.get("output_tokens_details") or {}).get("thinking_tokens", 0)
        heading = next((m.group(1) for m in re.finditer(r"^#{1,2}\s+(.+)$", r["text"], re.M)), None)
        finished = datetime.fromisoformat(r["finished_at"]).astimezone()
        prompts.setdefault(r["prompt"], r["system"])
        runs.append({
            "id": f.stem,
            "model": r["model"],
            "prompt": r["prompt"],
            "effort": r["effort"],
            "when": f"{finished:%b %-d, %-I:%M %p}",
            "sort": finished.isoformat(),
            "stop": r["stop_reason"],
            "words": len(r["text"].split()),
            "thinking_tokens": thinking_tokens,
            "seconds": r["seconds"],
            "cost": cost(r),
            "title": heading or first_line(r["text"]),
            "opening": first_line(r["text"]),
            "system": r["system"],
            "user_turn": r["user_turn"],
            "thinking": r["thinking_summary"],
            "text": r["text"],
        })
    runs.sort(key=lambda x: (x["prompt"], EFFORT_ORDER.index(x["effort"]), x["sort"]))
    data = json.dumps({"runs": runs, "prompts": prompts}, ensure_ascii=False).replace("</", "<\\/")
    page = (HERE / "template.html").read_text().replace("__DATA__", data)
    out = HERE / "home-alone-reader.html"  # for the Claude artifact, which adds its own <html>/<head>
    out.write_text(page)
    print(f"wrote {out.relative_to(ROOT)}: {len(runs)} runs, {out.stat().st_size / 1e6:.1f} MB")
    # Standalone copy for GitHub Pages, which serves the file as-is.
    pages = ROOT / "docs" / "index.html"
    pages.parent.mkdir(exist_ok=True)
    pages.write_text(
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        "</head>\n<body>\n" + page + "\n</body>\n</html>\n"
    )
    print(f"wrote {pages.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
