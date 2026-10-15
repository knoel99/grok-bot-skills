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

_TURN_FIELDS = ("BOT", "PENSEES", "MESSAGE")
_TURN_HEADER = re.compile(r"^(BOT|PENSEES|MESSAGE):(.*)$")
_SCENE_HEADER = re.compile(r"^(lieu|humeur|prochain|consigne):(.*)$")


def parse_turn(text, roster):
    """Validate one bot turn. Return {bot, thoughts, message}.

    Exactly one BOT, PENSEES, and MESSAGE, in that order. The message is
    non-empty. The bot is on the roster (a comma-separated string or a list).
    """
    if text is None or not str(text).strip():
        raise ValueError("turn is empty")
    allowed = _roster_names(roster)
    blocks = _blocks(str(text).splitlines(), _TURN_HEADER, "unexpected line before BOT")
    names = [name for name, _ in blocks]
    _require_fields(names, _TURN_FIELDS, "fields must be BOT, PENSEES, MESSAGE in that order")
    values = {name: value for name, value in blocks}
    bot = values["BOT"]
    thoughts = values["PENSEES"]
    message = values["MESSAGE"]
    if not bot:
        raise ValueError("BOT is empty")
    if not thoughts:
        raise ValueError("PENSEES is empty")
    if not message:
        raise ValueError("MESSAGE is empty")
    _validate_bot_name(bot)
    if bot not in allowed:
        raise ValueError(f"unknown bot: {bot}")
    return {"bot": bot, "thoughts": thoughts, "message": message}


def apply_turn(run_dir, turn):
    """Append the public line to transcript.md and the thoughts to that bot's jsonl.

    The transcript receives only `**<bot>:** <message>`. Thoughts stay in
    run_dir/thoughts/<bot>.jsonl with a turn number and a timestamp.
    """
    if not run_dir or not str(run_dir).strip():
        raise ValueError("run_dir is required")
    bot, thoughts, message = _turn_parts(turn)
    thoughts_path = _thoughts_path(run_dir, bot)
    os.makedirs(os.path.dirname(thoughts_path), exist_ok=True)
    number = _next_turn_number(run_dir)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {"turn": number, "timestamp": timestamp, "bot": bot, "thoughts": thoughts}
    _append_text(thoughts_path, json.dumps(record, ensure_ascii=False))
    _append_text(os.path.join(run_dir, "transcript.md"), _public_line(bot, message))
    return {"turn": number, "timestamp": timestamp, "bot": bot}


def render_scene(lieu, humeur, prochain, consigne=None):
    """Render a narrator SCENE block. consigne is omitted when empty.

    humeur is a name-to-state mapping, or a string `name=state; ...`.
    """
    lieu = _clean_line(lieu, "lieu")
    prochain = _clean_line(prochain, "prochain")
    humeur_text = _format_humeur(humeur)
    lines = [
        "SCENE:",
        f"lieu: {lieu}",
        f"humeur: {humeur_text}",
        f"prochain: {prochain}",
    ]
    if consigne is not None and str(consigne).strip():
        lines.append(f"consigne: {_clean_line(consigne, 'consigne')}")
    return "\n".join(lines) + "\n"


def parse_scene(text):
    """Parse a SCENE block. Return lieu, humeur, prochain, and consigne.

    humeur is a dict of name to state. consigne is None when omitted.
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
    expected = ["lieu", "humeur", "prochain"]
    if "consigne" in names:
        expected.append("consigne")
    _require_fields(names, expected, "scene fields must be lieu, humeur, prochain, then optional consigne")
    values = {name: value for name, value in blocks}
    lieu = _clean_line(values["lieu"], "lieu")
    prochain = _clean_line(values["prochain"], "prochain")
    humeur = _parse_humeur(values["humeur"])
    consigne = None
    if "consigne" in values and values["consigne"].strip():
        consigne = _clean_line(values["consigne"], "consigne")
    return {"lieu": lieu, "humeur": humeur, "prochain": prochain, "consigne": consigne}


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
    render_parser.add_argument("--lieu", required=True)
    render_parser.add_argument("--humeur", required=True, help="name=state; name=state")
    render_parser.add_argument("--prochain", required=True)
    render_parser.add_argument("--consigne")

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
            apply_turn(args.run_dir, turn)
            sys.stdout.write(_public_line(turn["bot"], turn["message"]) + "\n")
        elif args.command == "scene":
            _print_json(parse_scene(sys.stdin.read()))
        elif args.command == "render-scene":
            sys.stdout.write(
                render_scene(args.lieu, args.humeur, args.prochain, args.consigne)
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
        raise ValueError("PENSEES is empty")
    if not message:
        raise ValueError("MESSAGE is empty")
    return bot, thoughts, message


def _public_line(bot, message):
    return f"**{bot}:** {message}"


def _clean_line(value, label):
    text = " ".join(str(value).split())
    if not text:
        raise ValueError(f"{label} is empty")
    return text


def _format_humeur(humeur):
    if isinstance(humeur, str):
        return _join_humeur(_parse_humeur(humeur))
    if isinstance(humeur, dict):
        if not humeur:
            raise ValueError("humeur is empty")
        pairs = []
        for name, state in humeur.items():
            name = " ".join(str(name).split())
            state = " ".join(str(state).split())
            if not name or not state or any(char in name or char in state for char in ";="):
                raise ValueError(f"invalid humeur entry: {name}={state}")
            pairs.append((name, state))
        return _join_humeur(dict(pairs))
    raise ValueError("humeur must be a string or a mapping")


def _parse_humeur(text):
    flat = " ".join(str(text).split())
    if not flat:
        raise ValueError("humeur is empty")
    parsed = {}
    for part in flat.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"invalid humeur entry: {part}")
        name, state = part.split("=", 1)
        name = name.strip()
        state = state.strip()
        if not name or not state or "=" in name or ";" in name:
            raise ValueError(f"invalid humeur entry: {part}")
        if name in parsed:
            raise ValueError(f"duplicate humeur: {name}")
        parsed[name] = state
    if not parsed:
        raise ValueError("humeur is empty")
    return parsed


def _join_humeur(mapping):
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
