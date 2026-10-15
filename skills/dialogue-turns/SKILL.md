---
name: dialogue-turns
description: >
  Parse and validate dialogue turns for a multi-bot simulation in which one
  session plays several persona bots plus an omniscient narrator. Use when a
  bot must emit BOT, THOUGHTS, and MESSAGE, or when the narrator must receive
  a SCENE block. Keeps private thoughts out of the shared transcript. Do not
  use to invent the plot, or to show one bot another bot's thoughts.
---

# dialogue-turns

## When to use

Use this when one Grok Build session plays several persona bots and an omniscient narrator.

Validate every bot turn before it is treated as spoken. Give the narrator a SCENE block before each narrator reply. The script records the public line and stores the thoughts.

Do not use it to decide the story, and do not edit `transcript.md` by hand.

## Format

A bot turn is exactly these three fields, in this order, each once:

```
BOT: <name>
THOUGHTS: <private inner thoughts>
MESSAGE: <public line>
```

The message is required and must be non-empty. It may span lines. A line that starts with `**<name>:**` is rejected. The name must be on the roster (`--roster a,b,c`). `apply` checks that roster again before it writes.

The narrator receives this block before each reply:

```
SCENE:
place: <place>
mood: <name>=<state>; <name>=<state>
next: <name>
directive: <optional, for example aside with X, or an interruption>
```

`directive` may be omitted. `place`, `mood`, and `next` may not.

## Privacy

Thoughts never go into the shared session, and never go into `transcript.md`.

`apply` appends only `**<bot>:** <message>` to `run_dir/transcript.md`. It appends the thoughts to `run_dir/thoughts/<bot>.jsonl` with a turn number and a timestamp.

For the next prompt:

- call `private` for the bot that is about to speak, and pass only those thoughts
- call `narrator` only for the narrator, who is omniscient

There is no helper that returns another bot's thoughts for a persona prompt.

## Effort

Bot lines: low. A sentence or two of private thought, then one public line.

Narrator turns: higher. Read the transcript and every thought, then write `place`, `mood`, `next`, and `directive` with care.

## CLI

From this skill's directory:

```bash
python3 scripts/dialogue_turns.py apply \
  --run-dir /workspace/runs/<id> \
  --roster alice,bob,cara \
  < turn.txt
```

Invalid input prints a message on stderr and exits non-zero. Nothing is written.

```bash
python3 scripts/dialogue_turns.py private --run-dir /workspace/runs/<id> --bot alice
python3 scripts/dialogue_turns.py narrator --run-dir /workspace/runs/<id>
python3 scripts/dialogue_turns.py scene < scene.txt
python3 scripts/dialogue_turns.py render-scene \
  --place "the cafe" \
  --mood "alice=wary; bob=cheerful" \
  --next alice \
  --directive "aside with bob"
```

`private` defaults to the last 3 thoughts. Its stdout is for that bot's prompt only. `narrator` stdout is for the narrator's prompt only. Do not paste either into the shared session.
