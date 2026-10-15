#!/usr/bin/env python3
"""Parse multi-bot dialogue turns and keep private thoughts out of the transcript.

Public thread (run_dir/transcript.md), one line per accepted turn:
    **<bot>:** <message>

Private thoughts (run_dir/thoughts/<bot>.jsonl), append-only:
    {"turn": N, "timestamp": "...", "bot": "<bot>", "thoughts": "..."}

private_context reads one bot's file. narrator_context is the only reader
that returns every bot's thoughts. Nothing in this module writes thoughts
into transcript.md.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

__all__ = [
    "parse_turn",
    "apply_turn",
    "render_scene",
    "parse_scene",
    "private_context",
    "narrator_context",
    "main",
]


def _label_pattern(labels):
    inner = "|".join(re.escape(label) for label in labels)
    return re.compile(rf"^({inner}):(.*)$")


_TURN_FIELDS = ("BOT", "THOUGHTS", "MESSAGE")
_SCENE_LABELS = ("place", "mood", "next", "directive")
_TURN_HEADER = _label_pattern(_TURN_FIELDS)
_SCENE_HEADER = _label_pattern(_SCENE_LABELS)
_SPEAKER_LINE = re.compile(r"^\*\*.+?:\*\*")


def parse_turn(text, roster):
    """Validate one bot turn. Return {bot, thoughts, message}.

    Exactly one BOT, THOUGHTS, and MESSAGE, in that order. The message is
    non-empty. The bot is on the roster (a comma-separated string or a list).
    """
    if text is None or not str(text).strip():
        raise ValueError("turn is empty")
    allowed = _roster_names(roster)
    blocks = _blocks(str(text).splitlines(), _TURN_HEADER, "unexpected line before BOT")
    names = [name for name, _ in blocks]
    _require_fields(names, _TURN_FIELDS, "fields must be " + ", ".join(_TURN_FIELDS) + " in that order")
    values = {name: value for name, value in blocks}
    bot = values["BOT"]
    thoughts = values["THOUGHTS"]
    message = values["MESSAGE"]
    if not bot:
        raise ValueError("BOT is empty")
    if not thoughts:
        raise ValueError("THOUGHTS is empty")
    if not message:
        raise ValueError("MESSAGE is empty")
    _reject_speaker_line(message)
    _validate_bot_name(bot)
    if bot not in allowed:
        raise ValueError(f"unknown bot: {bot}")
    return {"bot": bot, "thoughts": thoughts, "message": message}


def apply_turn(run_dir, turn, roster):
    """Append the public line to transcript.md and the thoughts to that bot's jsonl.

    The transcript receives only `**<bot>:** <message>`. Thoughts stay in
    run_dir/thoughts/<bot>.jsonl with a turn number and a timestamp.
    The bot is checked against roster again before anything is written.
    """
    if not run_dir or not str(run_dir).strip():
        raise ValueError("run_dir is required")
    bot, thoughts, message = _turn_parts(turn)
    if bot not in _roster_names(roster):
        raise ValueError(f"unknown bot: {bot}")
    thoughts_path = _thoughts_path(run_dir, bot)
    os.makedirs(os.path.dirname(thoughts_path), exist_ok=True)
    number = _next_turn_number(run_dir)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {"turn": number, "timestamp": timestamp, "bot": bot, "thoughts": thoughts}
    _append_text(thoughts_path, json.dumps(record, ensure_ascii=False))
    _append_text(os.path.join(run_dir, "transcript.md"), _public_line(bot, message))
    return {"turn": number, "timestamp": timestamp, "bot": bot}


def render_scene(place, mood, next_speaker, directive=None):
    """Render a narrator SCENE block. directive is omitted when empty.

    mood is a name-to-state mapping, or a string `name=state; ...`.
    """
    place = _clean_line(place, "place")
    next_speaker = _clean_line(next_speaker, "next")
    mood_text = _format_mood(mood)
    lines = [
        "SCENE:",
        f"place: {place}",
        f"mood: {mood_text}",
        f"next: {next_speaker}",
    ]
    if directive is not None and str(directive).strip():
        lines.append(f"directive: {_clean_line(directive, 'directive')}")
    return "\n".join(lines) + "\n"


def parse_scene(text):
    """Parse a SCENE block. Return place, mood, next, and directive.

    mood is a dict of name to state. directive is None when omitted.
    """
    if text is None or not str(text).strip():
        raise ValueError("scene is empty")
    lines = str(text).splitlines()
    index = 0
    while index < len(lines) and lines[index].strip() == "":
        index += 1
    if index >= len(lines) or lines[index].strip() != "SCENE:":
        raise ValueError("scene must start with SCENE:")
    blocks = _blocks(lines[index + 1 :], _SCENE_HEADER, "unexpected line in scene")
    names = [name for name, _ in blocks]
    required = list(_SCENE_LABELS[:-1])
    optional = _SCENE_LABELS[-1]
    expected = required + ([optional] if optional in names else [])
    order = "scene fields must be " + ", ".join(required) + ", then optional " + optional
    _require_fields(names, expected, order)
    values = {name: value for name, value in blocks}
    place = _clean_line(values["place"], "place")
    next_speaker = _clean_line(values["next"], "next")
    mood = _parse_mood(values["mood"])
    directive = None
    if optional in values and values[optional].strip():
        directive = _clean_line(values[optional], optional)
    return {"place": place, "mood": mood, "next": next_speaker, "directive": directive}


def private_context(run_dir, bot, last_n=3):
    """Return this bot's own recent thought records, oldest of the window first.

    Reads only run_dir/thoughts/<bot>.jsonl. It does not open any other bot's file.
    """
    _validate_bot_name(bot)
    try:
        count = int(last_n)
    except (TypeError, ValueError) as exc:
        raise ValueError("last_n must be an integer") from exc
    if count < 0:
        raise ValueError("last_n must be >= 0")
    records = _read_jsonl(_thoughts_path(run_dir, bot))
    if count == 0:
        return []
    return records[-count:]


def narrator_context(run_dir):
    """Return the public transcript and every bot's thoughts.

    For the omniscient narrator only. Persona prompts use private_context.
    """
    thoughts_dir = os.path.join(run_dir, "thoughts")
    records = []
    if os.path.isdir(thoughts_dir):
        for name in sorted(os.listdir(thoughts_dir)):
            if not name.endswith(".jsonl"):
                continue
            bot = name[: -len(".jsonl")]
            path = os.path.join(thoughts_dir, name)
            for record in _read_jsonl(path):
                item = dict(record)
                item.setdefault("bot", bot)
                records.append(item)
    records.sort(key=lambda record: (record.get("turn", 0), str(record.get("bot", ""))))
    return {"transcript": _read_text(os.path.join(run_dir, "transcript.md")), "thoughts": records}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="dialogue_turns.py",
        description="Validate multi-bot dialogue turns and keep thoughts out of the transcript.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    apply_parser = commands.add_parser("apply", help="validate a turn on stdin and record it")
    apply_parser.add_argument("--run-dir", required=True)
    apply_parser.add_argument("--roster", required=True, help="comma-separated bot names")

    commands.add_parser("scene", help="parse a SCENE block on stdin")

    render_parser = commands.add_parser("render-scene", help="print a SCENE block")
    render_parser.add_argument("--place", required=True)
    render_parser.add_argument("--mood", required=True, help="name=state; name=state")
    render_parser.add_argument("--next", required=True, dest="next_speaker")
    render_parser.add_argument("--directive")

    private_parser = commands.add_parser("private", help="print one bot's recent thoughts")
    private_parser.add_argument("--run-dir", required=True)
    private_parser.add_argument("--bot", required=True)
    private_parser.add_argument("--last-n", type=int, default=3)

    narrator_parser = commands.add_parser("narrator", help="print the narrator's full context")
    narrator_parser.add_argument("--run-dir", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "apply":
            turn = parse_turn(sys.stdin.read(), args.roster)
            apply_turn(args.run_dir, turn, args.roster)
            sys.stdout.write(_public_line(turn["bot"], turn["message"]) + "\n")
        elif args.command == "scene":
            _print_json(parse_scene(sys.stdin.read()))
        elif args.command == "render-scene":
            sys.stdout.write(
                render_scene(args.place, args.mood, args.next_speaker, args.directive)
            )
        elif args.command == "private":
            _print_json(private_context(args.run_dir, args.bot, args.last_n))
        elif args.command == "narrator":
            _print_json(narrator_context(args.run_dir))
        else:
            parser.error(f"unknown command: {args.command}")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _blocks(lines, pattern, unexpected):
    blocks = []
    current = None
    for line in lines:
        match = pattern.match(line)
        if match:
            current = [match.group(2)]
            blocks.append((match.group(1), current))
            continue
        if current is None:
            if line.strip() == "":
                continue
            raise ValueError(f"{unexpected}: {line!r}")
        current.append(line)
    return [(name, "\n".join(parts).strip()) for name, parts in blocks]


def _require_fields(names, required, order_message):
    required = list(required)
    for field in required:
        count = names.count(field)
        if count == 0:
            raise ValueError(f"missing {field}")
        if count > 1:
            raise ValueError(f"duplicate {field}")
    if names == required:
        return
    for name in names:
        if names.count(name) > 1:
            raise ValueError(f"duplicate {name}")
        if name not in required:
            raise ValueError(f"unexpected {name}")
    raise ValueError(order_message)


def _roster_names(roster):
    if roster is None:
        raise ValueError("roster is required")
    if isinstance(roster, str):
        raw = roster.split(",")
    else:
        try:
            raw = list(roster)
        except TypeError as exc:
            raise ValueError("roster is required") from exc
    names = []
    seen = set()
    for item in raw:
        name = str(item).strip()
        if not name or name in seen:
            continue
        _validate_bot_name(name)
        seen.add(name)
        names.append(name)
    if not names:
        raise ValueError("roster is empty")
    return names


def _validate_bot_name(bot):
    if not isinstance(bot, str) or not bot or bot.strip() != bot:
        raise ValueError("invalid bot name" if not bot else f"invalid bot name: {bot!r}")
    if bot in (".", "..") or os.path.basename(bot) != bot:
        raise ValueError(f"invalid bot name: {bot}")
    if any(ord(char) < 32 or char in "\\/\0" for char in bot):
        raise ValueError(f"invalid bot name: {bot}")


def _turn_parts(turn):
    if not isinstance(turn, dict):
        raise ValueError("turn must be a dict")
    for key in ("bot", "thoughts", "message"):
        if key not in turn:
            raise ValueError(f"turn missing {key}")
    bot = str(turn["bot"]).strip()
    thoughts = str(turn["thoughts"]).strip()
    message = str(turn["message"]).strip()
    if not bot:
        raise ValueError("BOT is empty")
    if not thoughts:
        raise ValueError("THOUGHTS is empty")
    if not message:
        raise ValueError("MESSAGE is empty")
    _reject_speaker_line(message)
    return bot, thoughts, message


def _reject_speaker_line(message):
    for line in message.splitlines():
        if _SPEAKER_LINE.match(line.lstrip()):
            raise ValueError("MESSAGE line looks like a transcript speaker line")


def _public_line(bot, message):
    return f"**{bot}:** {message}"


def _clean_line(value, label):
    text = " ".join(str(value).split())
    if not text:
        raise ValueError(f"{label} is empty")
    return text


def _format_mood(mood):
    if isinstance(mood, str):
        return _join_mood(_parse_mood(mood))
    if isinstance(mood, dict):
        if not mood:
            raise ValueError("mood is empty")
        pairs = []
        for name, state in mood.items():
            name = " ".join(str(name).split())
            state = " ".join(str(state).split())
            if not name or not state or any(char in name or char in state for char in ";="):
                raise ValueError(f"invalid mood entry: {name}={state}")
            pairs.append((name, state))
        return _join_mood(dict(pairs))
    raise ValueError("mood must be a string or a mapping")


def _parse_mood(text):
    flat = " ".join(str(text).split())
    if not flat:
        raise ValueError("mood is empty")
    parsed = {}
    for part in flat.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"invalid mood entry: {part}")
        name, state = part.split("=", 1)
        name = name.strip()
        state = state.strip()
        if not name or not state or "=" in name or ";" in name:
            raise ValueError(f"invalid mood entry: {part}")
        if name in parsed:
            raise ValueError(f"duplicate mood: {name}")
        parsed[name] = state
    if not parsed:
        raise ValueError("mood is empty")
    return parsed


def _join_mood(mapping):
    return "; ".join(f"{name}={state}" for name, state in mapping.items())


def _next_turn_number(run_dir):
    thoughts_dir = os.path.join(run_dir, "thoughts")
    total = 0
    if os.path.isdir(thoughts_dir):
        for name in os.listdir(thoughts_dir):
            if name.endswith(".jsonl"):
                total += len(_read_jsonl(os.path.join(thoughts_dir, name)))
    return total + 1


def _thoughts_path(run_dir, bot):
    _validate_bot_name(bot)
    directory = os.path.abspath(os.path.join(run_dir, "thoughts"))
    path = os.path.abspath(os.path.join(directory, bot + ".jsonl"))
    if os.path.commonpath([directory, path]) != directory:
        raise ValueError(f"invalid bot name: {bot}")
    return path


def _read_jsonl(path):
    if not os.path.exists(path):
        return []
    records = []
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid thoughts record {path}:{line_number}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"invalid thoughts record {path}:{line_number}")
            records.append(record)
    return records


def _read_text(path):
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _append_text(path, text):
    if not text.endswith("\n"):
        text += "\n"
    prefix = ""
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as handle:
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b"\n":
                prefix = "\n"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(prefix + text)


def _print_json(payload):
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    sys.exit(main())
