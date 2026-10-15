---
name: agora-scene
description: >
  Run a multi-bot scene with one headless grok session per bot. Use when the
  user wants a scene sheet, a short pilot, a timed scene, or a report. The
  user gives the duration in minutes or hours. Turns stay inside the runner.
  Do not use to pick a side, to write the story, or to pass an API key.
---

# agora-scene

## When to use

Use this when several bots share a room and each bot has its own headless grok session.

The user gives the duration in minutes or hours. The runner turns that into a clock. Do not ask the user for a turn count.

Do not pass an API key. Do not read one. The child process does not inherit an API key variable.

## Flow

1. Write the sheet.
2. Stop. The user reads `sheet.md` and sets `approved: no` to `approved: yes`.
3. Run the pilot.
4. Run the scene for the duration on the sheet.
5. Write the report.

`pilot` and `run` exit with code 2 until the sheet is approved. `run` also exits with code 2 until the pilot has finished.

## Commands

From this skill's directory:

```bash
python3 scripts/agora.py sheet \
  --scene /tmp/scene \
  --situation "whether to send the note" \
  --cast "ada:writes the note,bo:reads the note" \
  --duration "30 m" \
  --grok /path/to/grok

python3 scripts/agora.py pilot --scene /tmp/scene --grok /path/to/grok
python3 scripts/agora.py run --scene /tmp/scene --grok /path/to/grok
python3 scripts/agora.py report --scene /tmp/scene
python3 scripts/agora.py selftest
```

`duration` accepts minutes (`30 m`, `45 minutes`) or hours (`2 hours`). `--grok` is the grok binary. Tests point it at a fake program. A missing approval is not a reason to start the binary.

## Scene folder

```
sheet.md
channels/public.jsonl
channels/dm-*.jsonl
channels/huddle-*.jsonl
minds/<bot>.jsonl
sessions.json
cursor.json
transcript.md
turns.csv
report.md
```

`sheet.md` is public and frozen after the user approves it. Goals, secrets, and witnesses live under `minds/` and `private/`. The narrator never receives those files.

## What the runner does

Each bot is one `grok -p` session, resumed with `--resume` and the effort that bot chose for this move. The output format is `streaming-json`. A move sends the unread lines from that bot's own channels.

Bots run at the same time. The scheduler publishes the first finished move. It does not wait for a slower bot.

The script picks the floor. An interruption rewrites the open public line down to a shorter line, adds `The line was cut.`, and leaves the speaker's session id and the full line in that bot's mind file.

`leave` removes the bot for the rest of the scene. That bot receives no later lines.

High effort is capped at 2 uses per bot. A later request uses medium. A scene holds at most 4 private channels. Each move has a time limit. The default is 90 seconds.

If a move hits that limit, the runner reads the partial stdout, keeps `sessionId`, and resumes. It does not send the seed again.

A reply that repeats another bot's private text is not written. The bot may say their own secret.

## Prompts

`prompts/director.md`, `prompts/narrator.md`, and `prompts/participant.md` stay fixed. The runner appends the input for that call. The narrator is called when the room changes (an interruption, a departure, a silence, a gesture, or a private channel) and otherwise every 5 to 8 turns. The narrator sees the public sheet and the public channel only.
