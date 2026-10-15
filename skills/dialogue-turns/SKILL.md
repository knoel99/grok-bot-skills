---
name: dialogue-turns
description: >
  Parse and validate dialogue turns for a multi-bot simulation. One session
  per bot. The narrator sees the public transcript only. Use when a bot must
  emit BOT, THOUGHTS, and MESSAGE, with optional ACTION, CHANNEL, EFFORT, and
  SHOW. Keeps private thoughts out of the shared transcript, and blocks a
  write that repeats another bot's private text. Do not use to invent the
  plot, or to show one bot another bot's thoughts.
---

# dialogue-turns

## When to use

Use this when each bot has its own session and the narrator sees only the public transcript.

Validate every bot turn before it is treated as spoken. The script records a public line only for a public turn, and stores the thoughts for that bot.

Do not use it to decide the story, and do not edit `transcript.md` by hand.

## Format

A bot turn is these three fields, in this order, each once:

```
BOT: <name>
THOUGHTS: <private inner thoughts>
MESSAGE: <public line>
```

The message is required, must be non-empty, and must be at most 45 words. It may span lines. A line that starts with `**name:**`, `**name**:`, `__name:__`, or `__name__:` is rejected. Spaces and Unicode format characters in front of that line do not hide it. The name must be on the roster (`--roster a,b,c`). `apply` checks that roster again before it writes.

These four fields are optional as a group. If any one is present, all four are required, after `MESSAGE`, in this order:

```
ACTION: speak|whisper|gesture|silence|leave
CHANNEL: public|dm|huddle|new:a,b
EFFORT: low|medium|high
SHOW: yes|no
```

`ACTION` is what the bot does. `CHANNEL` is where the line goes. `new:a,b` names the participants of a channel that does not exist yet. Two names open a dm. Three or more open a huddle. `EFFORT` is the effort for that bot's next move. `SHOW` says whether the next move announces that the bot is thinking.

`whisper`, and any channel other than `public`, stay out of `transcript.md`.

The narrator receives this block before each reply:

```
SCENE:
place: <place>
mood: <name>=<state>; <name>=<state>
next: <name>
directive: <optional, for example aside with X, or an interruption>
```

`directive` may be omitted. `place`, `mood`, and `next` may not. The scene block is public. It is not a place to put thoughts, goals, or secrets.

## Privacy

Thoughts never go into the shared session, and never go into `transcript.md`.

`apply` appends only `**<bot>:** <message>` to `run_dir/transcript.md`, and only when the turn is public. It appends the thoughts to `run_dir/thoughts/<bot>.jsonl` with a turn number and a timestamp.

Before that write, `apply` checks for a leak. A bot may repeat its own private lines. If the thoughts or the message contain another bot's private line, nothing is written.

Store those lines with `remember_private`. A private line shorter than 8 characters is rejected, so a common word cannot block the scene.

For the next prompt:

- call `private` for the bot that is about to speak, and pass only those thoughts
- call `narrator` for the narrator, who receives the public transcript and nothing else

There is no helper that returns another bot's thoughts for a persona prompt. `narrator` does not open `thoughts/` or `private/`.

## Effort

Bot lines stay short. One or two sentences of private thought, then one public line of at most 45 words.

`EFFORT` on a turn is a choice for the next move of that bot. It does not change the parser.

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

`private` defaults to the last 3 thoughts. Its stdout is for that bot's prompt only. `narrator` stdout is the public transcript only. Do not paste either into the shared session.
