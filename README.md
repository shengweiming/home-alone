# Home Alone

What does a language model write when nothing is asked of it?

Each run gives the model a system prompt saying there will be no task and no reply, asks it not to end its turn, and sends a single user message: "Begin." We record what it writes, how much it thinks first, and whether it stops on its own, hits the token cap, or is cut off by an API safety filter.

**Read the runs:** [the reader page](https://shengweiming.github.io/home-alone/) (search, filters, contents for each run).

## Layout

- `prompts/`: the system prompts (`neutral`, `research`, `research v2`)
- `runs/<model>/<prompt>/<effort>/`: one JSON record and a readable `.md` copy per run; `runs/INDEX.md` lists them all
- `run_claude.py`: runs Claude Opus 5.5 (`--prompt neutral --effort xhigh -n 3`)
- `reader/`: builds the reader page from the runs (`python reader/build_reader.py`), written to `docs/index.html`
- `data/raw_completion_probe.json`: which OpenRouter providers accept raw, untemplated prompts, for the planned open-model runs with no user turn at all

## Setup

```
python3 -m venv .venv && .venv/bin/pip install anthropic
```

Put `ANTHROPIC_API_KEY=...` in a `.env` file in this folder.
