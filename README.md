# skills-grok

Skills in this repository use one folder per skill. Each folder has a `SKILL.md` with `name` and `description`, and code under `scripts/` when the skill runs a program.

- grok-status follows a grok streaming-json log and writes status files while a headless grok run is in progress.
  - Folder: `skills/grok-status`
  - Script: `skills/grok-status/scripts/grok-status.py`
- dialogue-turns parses a bot turn, keeps private thoughts out of the shared transcript, and blocks a line that repeats another bot's private text.
  - Folder: `skills/dialogue-turns`
  - Script: `skills/dialogue-turns/scripts/dialogue_turns.py`
- agora-scene runs a timed multi-bot scene. One headless grok session per bot. The user gives the duration in minutes or hours.
  - Folder: `skills/agora-scene`
  - Script: `skills/agora-scene/scripts/agora.py`

Sotopia and Concordia are inspirations for private goals, secrets, and a narrator who sees only a partial view of the room. This project is independent of both.

Download a script only after a yes.
