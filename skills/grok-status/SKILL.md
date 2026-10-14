---
name: grok-status
description: >
  Follow a Grok Build streaming-json log and write status.txt and messages.jsonl
  while a headless grok run is in progress. Use when launching or resuming a
  Grok Heavy job, or when someone asks how that job is going. Do not use to
  write the research, the decision, or output.md. Do not use when the status
  program is already installed and no run is in progress.
---

# grok-status

## When to use

Use this at the start of a Grok Heavy job if `~/.grok/heavy/status.py` is missing, and again beside every `grok` launch so the log is followed while the process runs.

Do not use it to research, judge, or rewrite `output.md`.

## Install once

Ask once before any download. Show this URL:

`https://github.com/knoel99/skills-grok/tree/main/skills/grok-status`

Do not download before a yes. On no, stop.

On yes, download only this file:

`https://raw.githubusercontent.com/knoel99/skills-grok/main/skills/grok-status/scripts/grok-status.py`

Write it to `~/.grok/heavy/status.py`. Do not rewrite it on a later job.

Replace `knoel99/skills-grok` before publishing.

If this folder is already on disk (Cursor, Claude, or Grok Build), skip the download and run `scripts/grok-status.py` from the skill directory.

## Run

Start it beside grok. It is not a second grok.

```bash
python3 ~/.grok/heavy/status.py \
  --log /workspace/runs/<id>/run.log \
  --out /workspace/runs/<id>/status.txt \
  --messages /workspace/runs/<id>/messages.jsonl \
  --session /workspace/runs/<id>/session \
  --output /workspace/runs/<id>/output.md \
  --pid-file /workspace/runs/<id>/pid
```

The grok command must use `--output-format streaming-json`. Do not add `--include-partial-messages`.

## What it writes

- `status.txt` — four lines. Read this when asked for progress.
- `messages.jsonl` — one record per tool call, assistant text, and final result. It skips `stream_event` lines and thinking text.
- `session` — the session id, once seen.
- `output.md` — only if that file is missing or empty when the process exits. Never replace an `output.md` that already has text.

## Progress answer

Read `status.txt` and return those four lines. Do not scan `run.log`. Do not add an opinion.