#!/usr/bin/env python3
"""Run a multi-bot scene from a frozen sheet.

One headless grok session per bot. Workers return text. The scheduler is the
only writer of channel files, minds, sessions.json, and cursor.json.
The narrator prompt is built from the public sheet and the public channel.
"""

import argparse
import json
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
PROMPTS = os.path.join(SKILL_DIR, "prompts")
sys.path.insert(0, os.path.join(os.path.dirname(SKILL_DIR), "dialogue-turns", "scripts"))
import dialogue_turns as dt

HIGH_CAP = 2
PRIVATE_CAP = 4
MOVE_TIMEOUT = 90
PILOT_CAP_S = 120
SHEET_KEYS = (
    "title",
    "place",
    "register",
    "duration",
    "word_cap",
    "quorum",
    "end",
    "goals_conflict",
    "languages",
    "may_interrupt",
    "stakes",
    "present",
)
PRIVATE_KEYS = ("bot", "goal", "secret", "witness", "knows")
_DURATION = re.compile(
    r"^(\d+(?:\.\d+)?)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours)$",
    re.IGNORECASE,
)


class Completed:
    def __init__(self, text, session_id, timed_out, seconds):
        self.text = text or ""
        self.session_id = session_id
        self.timed_out = timed_out
        self.seconds = seconds


class WallClock:
    def now(self):
        return time.monotonic()


class SubprocessGrok:
    def __init__(self, binary, timeout=MOVE_TIMEOUT):
        self.binary = binary
        self.timeout = timeout

    def complete(self, prompt, session_id, effort, timeout):
        command = [
            self.binary,
            "-p",
            prompt,
            "--max-turns",
            "1",
            "--no-subagents",
            "--output-format",
            "streaming-json",
            "--effort",
            effort,
        ]
        if session_id:
            command[1:1] = ["--resume", session_id]
        env = {key: value for key, value in os.environ.items() if key != "XAI_API_KEY"}
        started = time.monotonic()
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
                env=env,
            )
            stdout = proc.stdout or ""
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", "replace")
            timed_out = True
        return Completed(
            _stream_text(stdout),
            _stream_session(stdout),
            timed_out,
            time.monotonic() - started,
        )


def parse_duration(text):
    match = _DURATION.match(" ".join(str(text).split()))
    if not match:
        raise ValueError(f"duration must be minutes or hours: {text}")
    amount = float(match.group(1))
    if amount <= 0:
        raise ValueError("duration must be positive")
    unit = match.group(2).lower()
    if unit.startswith("h"):
        return amount * 3600
    return amount * 60


def parse_director(text):
    """Split a director reply into a public sheet and private cards."""
    if text is None or not str(text).strip():
        raise ValueError("director reply is empty")
    lines = [line.rstrip() for line in str(text).splitlines()]
    start = _find_label(lines, "SHEET:")
    private_at = _find_label(lines, "PRIVATE:")
    if start is None or private_at is None or private_at <= start:
        raise ValueError("director reply needs SHEET and PRIVATE")
    sheet = _pairs(lines[start + 1 : private_at], SHEET_KEYS, "sheet")
    cards = []
    blocks = _split_bots(lines[private_at + 1 :])
    if not blocks:
        raise ValueError("director reply has no private cards")
    for block in blocks:
        card = _pairs(block, PRIVATE_KEYS, "private")
        cards.append(card)
    _reject_incomplete(sheet, cards)
    return sheet, cards


def write_sheet(scene_dir, sheet, cards):
    """Write the public sheet and each private card. Reject a second write."""
    os.makedirs(scene_dir, exist_ok=True)
    path = os.path.join(scene_dir, "sheet.md")
    if os.path.exists(path):
        raise ValueError("sheet already exists")
    _reject_cross(cards)
    present = _names(sheet["present"])
    if [card["bot"] for card in cards] != present:
        raise ValueError("private cards must follow present")
    body = ["approved: no"]
    for key in SHEET_KEYS:
        body.append(f"{key}: {sheet[key]}")
    _write(path, "\n".join(body) + "\n")
    os.makedirs(os.path.join(scene_dir, "channels"), exist_ok=True)
    os.makedirs(os.path.join(scene_dir, "minds"), exist_ok=True)
    _write(os.path.join(scene_dir, "channels", "public.jsonl"), "")
    _write(os.path.join(scene_dir, "transcript.md"), "")
    _write(
        os.path.join(scene_dir, "turns.csv"),
        "turn,time,speaker,action,channel,seconds,words,session,note\n",
    )
    _write(os.path.join(scene_dir, "sessions.json"), "{}\n")
    duration_s = parse_duration(sheet["duration"])
    quorum = int(sheet["quorum"])
    state = {
        "present": present,
        "left": [],
        "quorum": quorum,
        "duration_s": duration_s,
        "cursors": {name: {} for name in present},
        "effort": {name: "low" for name in present},
        "show": {name: "no" for name in present},
        "high_used": {name: 0 for name in present},
        "private_channels": [],
        "open_public_id": None,
        "open_speaker": None,
        "last_speaker": None,
        "counts": {name: 0 for name in present},
        "spoke_at": {},
        "turns": 0,
        "narrator_at": 0,
        "narrator_gap": 6,
        "pilot_done": False,
        "leaks_blocked": 0,
        "narrator_cursor": 0,
        "last_public_id": 0,
        "dirty": False,
    }
    _save_json(os.path.join(scene_dir, "cursor.json"), state)
    for card in cards:
        record = {
            "kind": "profile",
            "bot": card["bot"],
            "goal": card["goal"],
            "secret": card["secret"],
            "witness": card["witness"],
            "knows": _knows(card["knows"]),
        }
        _append_jsonl(os.path.join(scene_dir, "minds", card["bot"] + ".jsonl"), record)
        for field in ("goal", "secret", "witness"):
            dt.remember_private(scene_dir, card["bot"], card[field])
    return state


def sheet_text(scene_dir):
    return _read(os.path.join(scene_dir, "sheet.md"))


def sheet_approved(scene_dir):
    for line in sheet_text(scene_dir).splitlines():
        if line.startswith("approved:"):
            return line.split(":", 1)[1].strip() == "yes"
    return False


def load_state(scene_dir):
    return json.loads(_read(os.path.join(scene_dir, "cursor.json")))


def save_state(scene_dir, state):
    _save_json(os.path.join(scene_dir, "cursor.json"), state)


def load_sessions(scene_dir):
    path = os.path.join(scene_dir, "sessions.json")
    if not os.path.exists(path):
        return {}
    return json.loads(_read(path) or "{}")


def save_session(scene_dir, actor, session_id):
    if not session_id:
        return
    sessions = load_sessions(scene_dir)
    sessions[actor] = session_id
    _save_json(os.path.join(scene_dir, "sessions.json"), sessions)


def collect_lines(scene_dir, bot, state):
    """Unread lines from channels this bot belongs to. Left bots get nothing."""
    if bot in state["left"]:
        return []
    lines = []
    channel_dir = os.path.join(scene_dir, "channels")
    if not os.path.isdir(channel_dir):
        return []
    cursors = state["cursors"].get(bot, {})
    for name in sorted(os.listdir(channel_dir)):
        members = _members(name)
        if members is not None and bot not in members:
            continue
        key = name[: -len(".jsonl")] if name.endswith(".jsonl") else name
        seen = cursors.get(key, 0)
        for event in _read_events(os.path.join(channel_dir, name)):
            if event.get("id", 0) <= seen:
                continue
            lines.append(_format_event(key, event))
    return lines


def _cursor_snapshot(scene_dir, bot, state):
    """Ids of lines included in the prompt just built. This does not mark them read."""
    if bot in state["left"]:
        return {}
    channel_dir = os.path.join(scene_dir, "channels")
    if not os.path.isdir(channel_dir):
        return {}
    snapshot = {}
    for name in os.listdir(channel_dir):
        members = _members(name)
        if members is not None and bot not in members:
            continue
        key = name[: -len(".jsonl")] if name.endswith(".jsonl") else name
        events = _read_events(os.path.join(channel_dir, name))
        if events:
            snapshot[key] = events[-1]["id"]
    return snapshot


def _advance_cursor(state, bot, snapshot):
    if not snapshot or bot in state["left"]:
        return
    cursors = state["cursors"].setdefault(bot, {})
    for key, event_id in snapshot.items():
        cursors[key] = max(cursors.get(key, 0), event_id)


def narrator_prompt(sheet, public_lines, seed):
    """Public sheet plus public lines. Callers must not pass private text."""
    parts = []
    if seed:
        parts.append(_prompt("narrator.md").rstrip())
        parts.append("ROLE: narrator")
        parts.append("SHEET:\n" + sheet.strip())
    else:
        parts.append("ROLE: narrator")
    body = "\n".join(public_lines) if public_lines else "(nothing new in the room)"
    parts.append("PUBLIC:\n" + body)
    return "\n\n".join(parts)


def participant_prompt(sheet, profile, unread, seed):
    parts = []
    if seed:
        parts.append(_prompt("participant.md").rstrip())
        parts.append("ROLE: participant")
        parts.append(_profile_block(sheet, profile))
    else:
        parts.append("ROLE: participant")
    parts.append(f"SPEAKER: {profile['bot']}")
    heard = "\n".join(unread) if unread else "(nothing new)"
    parts.append("HEARD:\n" + heard)
    return "\n\n".join(parts)


def choose_mode(state, rng):
    """Pick who may take the floor. A bot who left is never chosen."""
    present = [bot for bot in state["present"] if bot not in state["left"]]
    if not present:
        return None
    last = state.get("last_speaker")
    roll = rng.random()
    if roll < 0.15 and last and len(present) > 1:
        bot = _weighted(present, state, rng, avoid=(last,))
        return {"bot": bot, "mode": "interrupt", "target": last}
    if roll < 0.30 and len(present) > 1:
        first = _weighted(present, state, rng, avoid=())
        second = _weighted(present, state, rng, avoid=(first,))
        return {"bot": first, "mode": "whisper", "target": second}
    avoid = (last,) if last and len(present) > 1 else ()
    bot = _weighted(present, state, rng, avoid=avoid)
    return {"bot": bot, "mode": "speak", "target": None}


def commit_move(scene_dir, state, bot, text, mode="speak", target=None, session_id=None, seconds=0):
    """Parse one reply and write it, or block it. Return a note."""
    roster = ",".join(state["present"])
    parsed = _parse_reply(text, roster, bot)
    if parsed is None:
        _csv(scene_dir, state, bot, "skip", "", seconds, 0, session_id, "parse")
        return "parse"
    try:
        dt.check_leak(scene_dir, bot, parsed["thoughts"])
        dt.check_leak(scene_dir, bot, parsed["message"])
    except ValueError:
        state["leaks_blocked"] = state.get("leaks_blocked", 0) + 1
        _csv(scene_dir, state, bot, parsed["action"], parsed["channel"], seconds, 0, session_id, "leak")
        save_state(scene_dir, state)
        return "leak"
    action = parsed["action"]
    if action == "leave":
        _write_public(scene_dir, state, bot, "speak", parsed["message"])
        if bot not in state["left"]:
            state["left"].append(bot)
        state["open_public_id"] = None
        state["open_speaker"] = None
        _mind(scene_dir, bot, parsed)
        _after(scene_dir, state, bot, parsed, seconds, session_id, "leave")
        return "leave"
    route, members = _route(parsed, bot, target, state)
    if route == "skip":
        _csv(scene_dir, state, bot, action, parsed["channel"], seconds, 0, session_id, "channel")
        return "skip"
    if route == "public" and action == "speak" and mode == "interrupt":
        _cut_open(scene_dir, state, bot)
        state["dirty"] = True
    if route == "public":
        _write_public(scene_dir, state, bot, action, parsed["message"])
        if action == "speak":
            state["open_public_id"] = state["last_public_id"]
            state["open_speaker"] = bot
            state["last_speaker"] = bot
            state["counts"][bot] = state["counts"].get(bot, 0) + 1
            state["spoke_at"][bot] = state["turns"] + 1
        else:
            state["open_public_id"] = None
            state["open_speaker"] = None
            if action in ("silence", "gesture"):
                state["dirty"] = True
    else:
        note = _write_private(scene_dir, state, bot, action, parsed["message"], members)
        if note == "cap":
            _csv(scene_dir, state, bot, action, parsed["channel"], seconds, 0, session_id, "cap")
            save_state(scene_dir, state)
            return "cap"
        state["open_public_id"] = None
        state["open_speaker"] = None
        if note == "ok" and _channel_key(members) in (state.get("private_channels") or []):
            state["dirty"] = True
    _mind(scene_dir, bot, parsed)
    state["effort"][bot] = parsed["effort"]
    state["show"][bot] = parsed["show"]
    state["turns"] = state.get("turns", 0) + 1
    _render(scene_dir)
    _csv(
        scene_dir,
        state,
        bot,
        action,
        route if route == "public" else _channel_key(members),
        seconds,
        len(parsed["message"].split()),
        session_id,
        "",
    )
    save_state(scene_dir, state)
    return "ok"


def run_scene(scene_dir, client, clock, duration_s, rng=None, move_timeout=MOVE_TIMEOUT, stop_when=None):
    """Advance the scene until the duration elapses. Do not wait for the slowest bot."""
    rng = rng or random.Random()
    state = load_state(scene_dir)
    if not state.get("narrator_gap"):
        state["narrator_gap"] = rng.choice((5, 6, 7, 8))
        save_state(scene_dir, state)
    deadline = clock.now() + duration_s
    stop_when = stop_when or (lambda _state: False)
    inbox = queue.Queue()
    inflight = {}
    while clock.now() < deadline and _quorum(state):
        if stop_when(state):
            break
        _launch_idle(scene_dir, state, client, inbox, inflight, rng, move_timeout)
        _launch_narrator(scene_dir, state, client, inbox, inflight, move_timeout)
        if not inflight:
            break
        ready = _take_ready(inbox, timeout=0.05)
        if not ready:
            continue
        for key, result in ready:
            item = inflight.pop(key)
            if isinstance(result, Exception):
                _fail_move(scene_dir, state, key, result)
                if hasattr(clock, "advance"):
                    clock.advance(1)
                continue
            if result.session_id:
                save_session(scene_dir, key, result.session_id)
            if key == "narrator":
                _apply_narrator(scene_dir, state, result)
            else:
                _apply_bot(scene_dir, state, key, item, result)
            clock_step = result.seconds if result.seconds and result.seconds > 0 else 0
            if clock_step and hasattr(clock, "advance"):
                clock.advance(clock_step)
        _launch_narrator(scene_dir, state, client, inbox, inflight, move_timeout)
        save_state(scene_dir, state)
        if stop_when(state) or not _quorum(state) or clock.now() >= deadline:
            _drain_narrator(scene_dir, state, inflight, inbox, move_timeout)
            break
    save_state(scene_dir, state)
    return state


def write_report(scene_dir):
    state = load_state(scene_dir)
    sheet = _sheet_map(sheet_text(scene_dir))
    transcript = _read(os.path.join(scene_dir, "transcript.md")).strip()
    left = ", ".join(state["left"]) if state["left"] else "nobody"
    lines = [
        "# Scene report",
        "",
        f"Place: {sheet.get('place', '')}",
        f"Duration asked: {sheet.get('duration', '')}",
        f"Public turns: {state.get('turns', 0)}",
        f"Left: {left}",
        f"Blocked writes: {state.get('leaks_blocked', 0)}",
        "",
        "## Public transcript",
        "",
        transcript or "(no public lines)",
        "",
        "The narrator received the public sheet and the public channel only.",
        "",
    ]
    text = "\n".join(lines)
    _write(os.path.join(scene_dir, "report.md"), text)
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(prog="agora.py")
    commands = parser.add_subparsers(dest="command", required=True)

    sheet = commands.add_parser("sheet", help="write a frozen scene sheet")
    sheet.add_argument("--scene", required=True)
    sheet.add_argument("--situation", required=True)
    sheet.add_argument("--cast", required=True, help="name:public fact,name:public fact")
    sheet.add_argument("--duration", required=True, help="minutes or hours, for example 30 m")
    sheet.add_argument("--grok", default=os.environ.get("AGORA_GROK", "grok"))
    sheet.add_argument("--move-timeout", type=float, default=MOVE_TIMEOUT)

    pilot = commands.add_parser("pilot", help="run a short scene after the sheet is approved")
    _scene_args(pilot)
    run = commands.add_parser("run", help="run the scene for the sheet duration")
    _scene_args(run)
    commands.add_parser("selftest", help="run the offline tests")
    report = commands.add_parser("report", help="write report.md from the scene folder")
    report.add_argument("--scene", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "sheet":
            return _cmd_sheet(args)
        if args.command == "pilot":
            return _cmd_pilot(args)
        if args.command == "run":
            return _cmd_run(args)
        if args.command == "selftest":
            return _cmd_selftest()
        if args.command == "report":
            write_report(args.scene)
            return 0
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    parser.error(f"unknown command: {args.command}")
    return 2


def _cmd_sheet(args):
    parse_duration(args.duration)
    cast = _parse_cast(args.cast)
    prompt = _prompt("director.md").rstrip() + "\n\nINPUT:\n" + "\n".join(
        [
            f"situation: {args.situation.strip()}",
            "cast: " + "; ".join(f"{name}={fact}" for name, fact in cast),
            f"duration: {args.duration.strip()}",
        ]
    )
    client = SubprocessGrok(args.grok, args.move_timeout)
    result = client.complete(prompt, None, "low", args.move_timeout)
    sheet, cards = parse_director(result.text)
    sheet["duration"] = " ".join(args.duration.split())
    sheet["word_cap"] = "45"
    names = [name for name, _fact in cast]
    if _names(sheet["present"]) != names:
        sheet["present"] = ", ".join(names)
    write_sheet(args.scene, sheet, cards)
    return 0


def _cmd_pilot(args):
    if not sheet_approved(args.scene):
        print("error: sheet is not approved", file=sys.stderr)
        return 2
    state = load_state(args.scene)
    duration = min(state["duration_s"], PILOT_CAP_S)
    client = SubprocessGrok(args.grok, args.move_timeout)
    run_scene(args.scene, client, WallClock(), duration, move_timeout=args.move_timeout)
    state = load_state(args.scene)
    state["pilot_done"] = True
    save_state(args.scene, state)
    write_report(args.scene)
    return 0


def _cmd_run(args):
    if not sheet_approved(args.scene):
        print("error: sheet is not approved", file=sys.stderr)
        return 2
    state = load_state(args.scene)
    if not state.get("pilot_done"):
        print("error: pilot has not been run", file=sys.stderr)
        return 2
    client = SubprocessGrok(args.grok, args.move_timeout)
    run_scene(
        args.scene,
        client,
        WallClock(),
        state["duration_s"],
        move_timeout=args.move_timeout,
    )
    write_report(args.scene)
    return 0


def _cmd_selftest():
    import unittest

    suite = unittest.defaultTestLoader.discover(SCRIPT_DIR, pattern="test_agora.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


def _scene_args(parser):
    parser.add_argument("--scene", required=True)
    parser.add_argument("--grok", default=os.environ.get("AGORA_GROK", "grok"))
    parser.add_argument("--move-timeout", type=float, default=MOVE_TIMEOUT)


def _start(inbox, key, fn, args):
    def target():
        try:
            inbox.put((key, fn(*args)))
        except Exception as exc:
            inbox.put((key, exc))

    threading.Thread(target=target, name=f"agora-{key}", daemon=True).start()


def _take_ready(inbox, timeout):
    ready = []
    try:
        ready.append(_unpack(inbox.get(timeout=timeout)))
    except queue.Empty:
        return []
    while True:
        try:
            ready.append(_unpack(inbox.get_nowait()))
        except queue.Empty:
            return ready


def _unpack(item):
    return item


def _fail_move(scene_dir, state, key, exc):
    """A worker crash is silence. The read cursor stays where it was."""
    print(f"error: {key}: {exc}", file=sys.stderr)
    if key != "narrator" and key not in state.get("left", []):
        _write_public(scene_dir, state, key, "silence", "(silence)")
        state["open_public_id"] = None
        state["open_speaker"] = None
        state["dirty"] = True
        state["turns"] = state.get("turns", 0) + 1
        _render(scene_dir)
        _csv(scene_dir, state, key, "silence", "public", 0, 1, None, "error")
    else:
        _csv(scene_dir, state, key, "skip", "", 0, 0, None, "error")
    save_state(scene_dir, state)


def _launch_idle(scene_dir, state, client, inbox, inflight, rng, move_timeout):
    choice = choose_mode(state, rng) or {}
    for bot in [name for name in state["present"] if name not in state["left"]]:
        if bot in inflight:
            continue
        if state["show"].get(bot) == "yes":
            _write_public(scene_dir, state, bot, "note", f"{bot} is thinking.")
            state["show"][bot] = "no"
            _render(scene_dir)
        profile = _profile(scene_dir, bot)
        unread = collect_lines(scene_dir, bot, state)
        sessions = load_sessions(scene_dir)
        sid = sessions.get(bot)
        seed = ""
        if not sid:
            seed = participant_prompt(sheet_text(scene_dir), profile, unread, seed=True)
        delta = participant_prompt(sheet_text(scene_dir), profile, unread, seed=False)
        read_to = _cursor_snapshot(scene_dir, bot, state)
        effort = _next_effort(state, bot)
        mode = choice.get("mode", "speak") if choice.get("bot") == bot else "speak"
        target = choice.get("target") if choice.get("bot") == bot else None
        _start(
            inbox,
            bot,
            _produce,
            (client, seed, delta, sid, effort, move_timeout, scene_dir, bot, ",".join(state["present"])),
        )
        inflight[bot] = {"mode": mode, "target": target, "effort": effort, "read_to": read_to}


def _launch_narrator(scene_dir, state, client, inbox, inflight, move_timeout):
    if "narrator" in inflight:
        return
    gap = state.get("narrator_gap") or 6
    due = state["turns"] and state["turns"] - state.get("narrator_at", 0) >= gap
    changed = state.get("dirty")
    if not due and not changed:
        return
    public = _public_since(scene_dir, state.get("narrator_cursor", 0))
    sessions = load_sessions(scene_dir)
    sid = sessions.get("narrator")
    seed = ""
    if not sid:
        seed = narrator_prompt(sheet_text(scene_dir), public, seed=True)
    delta = narrator_prompt(sheet_text(scene_dir), public, seed=False)
    state["narrator_cursor"] = _last_public_id(scene_dir)
    state["dirty"] = False
    state["narrator_at"] = state["turns"]
    _start(inbox, "narrator", _produce, (client, seed, delta, sid, "low", move_timeout, scene_dir, "narrator", ""))
    inflight["narrator"] = {"mode": "narrate", "target": None, "effort": "low"}


def _produce(client, seed, delta, session_id, effort, timeout, scene_dir, bot, roster):
    result = _call(client, seed, delta, session_id, effort, timeout)
    if bot == "narrator" or not result.text:
        return result
    if _needs_retry(scene_dir, bot, result.text, roster):
        correction = delta + "\n\nSay it again. At most 45 words. Do not repeat another person's private words.\n"
        sid = result.session_id or session_id
        second = _call(client, seed, correction, sid, effort, timeout)
        second.seconds += result.seconds
        if not second.session_id:
            second.session_id = sid
        if second.text:
            return second
    clipped = _clip_turn(result.text)
    if clipped != result.text:
        result.text = clipped
    return result


def _call(client, seed, delta, session_id, effort, timeout):
    first = client.complete(_prompt_for(seed, delta, session_id), session_id, effort, timeout)
    if not first.timed_out:
        return first
    sid = first.session_id or session_id
    second = client.complete(_prompt_for(seed, delta, sid), sid, effort, timeout)
    second.seconds += first.seconds
    if not second.session_id:
        second.session_id = sid
    return second


def _prompt_for(seed, delta, session_id):
    if session_id:
        return delta
    if seed:
        return seed + "\n\n" + delta
    return delta


def _apply_bot(scene_dir, state, bot, item, result):
    if bot in state["left"]:
        return
    if not result.text:
        _csv(scene_dir, state, bot, "skip", "", result.seconds, 0, result.session_id, "empty")
        return
    note = commit_move(
        scene_dir,
        state,
        bot,
        result.text,
        mode=item["mode"],
        target=item["target"],
        session_id=result.session_id,
        seconds=result.seconds,
    )
    if note in ("ok", "leave"):
        _advance_cursor(state, bot, item.get("read_to"))
        if item["effort"] == "high":
            state["high_used"][bot] = state["high_used"].get(bot, 0) + 1


def _drain_narrator(scene_dir, state, inflight, inbox, move_timeout):
    if "narrator" not in inflight:
        return
    deadline = time.monotonic() + move_timeout
    while time.monotonic() < deadline:
        try:
            key, result = _unpack(inbox.get(timeout=0.05))
        except queue.Empty:
            continue
        inflight.pop(key, None)
        if isinstance(result, Exception):
            _fail_move(scene_dir, state, key, result)
            if key == "narrator":
                return
            continue
        if key != "narrator":
            continue
        if result.session_id:
            save_session(scene_dir, "narrator", result.session_id)
        _apply_narrator(scene_dir, state, result)
        return


def _apply_narrator(scene_dir, state, result):
    text = _narration(result.text)
    if not text:
        return
    _write_public(scene_dir, state, "narrator", "note", text)
    _render(scene_dir)
    save_state(scene_dir, state)


def _next_effort(state, bot):
    effort = state["effort"].get(bot, "low")
    if effort == "high" and state["high_used"].get(bot, 0) >= HIGH_CAP:
        return "medium"
    return effort


def _parse_reply(text, roster, bot):
    clipped = _clip_turn(text)
    for candidate in (text, clipped):
        try:
            parsed = dt.parse_turn(candidate, roster)
        except ValueError:
            continue
        parsed["bot"] = bot
        parsed.setdefault("action", "speak")
        parsed.setdefault("channel", "public")
        parsed.setdefault("effort", "low")
        parsed.setdefault("show", "no")
        return parsed
    return None


def _needs_retry(scene_dir, bot, text, roster):
    try:
        parsed = dt.parse_turn(text, roster)
    except ValueError:
        return True
    try:
        dt.check_leak(scene_dir, bot, parsed["thoughts"])
        dt.check_leak(scene_dir, bot, parsed["message"])
    except ValueError:
        return True
    return False


def _clip_turn(text):
    match = re.search(r"(?m)^MESSAGE:(.*?)(?=^ACTION:|\Z)", text, re.S)
    if not match:
        return text
    words = match.group(1).split()
    if len(words) <= dt.WORD_CAP:
        return text
    clipped = " " + " ".join(words[: dt.WORD_CAP])
    if not match.group(0).endswith("\n") and text[match.end() : match.end() + 1] != "\n":
        clipped += "\n"
    else:
        clipped += "\n"
    return text[: match.start(1)] + clipped + text[match.end() :]


def _narration(text):
    kept = []
    for line in (text or "").splitlines():
        if line.strip().upper().startswith("NEXT:"):
            continue
        kept.append(line)
    body = "\n".join(kept)
    try:
        dt._reject_speaker_line(body)
    except ValueError:
        return ""
    words = " ".join(body.split()).split()
    if not words:
        return ""
    return " ".join(words[: dt.WORD_CAP])


def _route(parsed, bot, target, state):
    action = parsed["action"]
    channel = parsed["channel"]
    if action == "whisper" or channel != "public":
        members = _resolve_members(channel, bot, target, state)
        if len(members) < 2 or bot not in members:
            return "skip", None
        return "private", members
    return "public", None


def _resolve_members(channel, bot, target, state):
    present = set(state["present"])
    if channel.startswith("new:"):
        names = [part.strip() for part in channel[4:].split(",") if part.strip()]
    elif target:
        names = [bot, target]
    else:
        names = [bot]
    picked = []
    for name in names:
        if name in present and name not in picked:
            picked.append(name)
    if bot not in picked:
        picked.insert(0, bot)
    return picked


def _write_public(scene_dir, state, bot, action, text):
    path = os.path.join(scene_dir, "channels", "public.jsonl")
    event = _next_event(path, bot, action, text)
    _append_jsonl(path, event)
    state["last_public_id"] = event["id"]


def _write_private(scene_dir, state, bot, action, text, members):
    key = _channel_key(members)
    known = set(state.get("private_channels") or [])
    if key not in known and len(known) >= PRIVATE_CAP:
        return "cap"
    filename = key + ".jsonl"
    path = os.path.join(scene_dir, "channels", filename)
    event = _next_event(path, bot, action, text)
    _append_jsonl(path, event)
    if key not in known:
        state.setdefault("private_channels", []).append(key)
    return "ok"


def _cut_open(scene_dir, state, cutter):
    open_id = state.get("open_public_id")
    if not open_id or state.get("open_speaker") in (None, cutter):
        return
    path = os.path.join(scene_dir, "channels", "public.jsonl")
    events = _read_events(path)
    target = None
    for event in events:
        if event.get("id") == open_id:
            target = event
            break
    if target is None:
        return
    full = target.get("full") or target.get("text") or ""
    words = full.split()
    kept = words[: max(1, len(words) // 2)] if words else []
    target["full"] = full
    target["text"] = " ".join(kept)
    target["cut"] = True
    _write_events(path, events)
    note = _next_event(path, "narrator", "note", "The line was cut.")
    _append_jsonl(path, note)
    state["last_public_id"] = note["id"]
    state["open_public_id"] = None
    state["open_speaker"] = None


def _next_event(path, bot, action, text):
    events = _read_events(path)
    number = events[-1]["id"] + 1 if events else 1
    kind = "note" if action == "note" else "line"
    return {
        "id": number,
        "bot": bot,
        "action": action,
        "kind": kind,
        "text": text,
        "full": text,
        "cut": False,
    }


def _mind(scene_dir, bot, parsed):
    _append_jsonl(
        os.path.join(scene_dir, "minds", bot + ".jsonl"),
        {
            "kind": "thought",
            "thoughts": parsed["thoughts"],
            "message": parsed["message"],
            "action": parsed["action"],
            "channel": parsed["channel"],
            "effort": parsed["effort"],
            "show": parsed["show"],
        },
    )


def _after(scene_dir, state, bot, parsed, seconds, session_id, note):
    state["effort"][bot] = parsed["effort"]
    state["show"][bot] = parsed["show"]
    state["turns"] = state.get("turns", 0) + 1
    state["dirty"] = True
    _render(scene_dir)
    _csv(scene_dir, state, bot, "leave", "public", seconds, len(parsed["message"].split()), session_id, note)
    save_state(scene_dir, state)


def _render(scene_dir):
    lines = []
    for event in _read_events(os.path.join(scene_dir, "channels", "public.jsonl")):
        if event.get("kind") == "note" or event.get("action") == "note":
            lines.append(f"*{event['text']}*")
        else:
            lines.append(f"**{event['bot']}:** {event['text']}")
    _write(os.path.join(scene_dir, "transcript.md"), ("\n".join(lines) + "\n") if lines else "")


def _csv(scene_dir, state, bot, action, channel, seconds, words, session_id, note):
    state["rows"] = state.get("rows", 0) + 1
    row = [
        str(state["rows"]),
        time.strftime("%H:%M:%S"),
        bot,
        action,
        channel,
        f"{seconds:.3f}",
        str(words),
        session_id or "",
        note,
    ]
    with open(os.path.join(scene_dir, "turns.csv"), "a", encoding="utf-8") as handle:
        handle.write(",".join(row) + "\n")


def _public_since(scene_dir, after_id):
    lines = []
    for event in _read_events(os.path.join(scene_dir, "channels", "public.jsonl")):
        if event.get("id", 0) <= after_id:
            continue
        lines.append(_format_event("public", event))
    return lines


def _last_public_id(scene_dir):
    events = _read_events(os.path.join(scene_dir, "channels", "public.jsonl"))
    if not events:
        return 0
    return events[-1]["id"]


def _profile(scene_dir, bot):
    for record in _read_events(os.path.join(scene_dir, "minds", bot + ".jsonl")):
        if record.get("kind") == "profile":
            return record
    raise ValueError(f"missing profile: {bot}")


def _profile_block(sheet, profile):
    knows = profile.get("knows") or {}
    known = "; ".join(f"{name}={fact}" for name, fact in knows.items()) or "(none)"
    return "\n".join(
        [
            f"SPEAKER: {profile['bot']}",
            "SHEET:",
            sheet.strip(),
            f"GOAL: {profile['goal']}",
            f"SECRET: {profile['secret']}",
            f"KNOWS: {known}",
        ]
    )


def _format_event(key, event):
    if event.get("kind") == "note" or event.get("action") == "note":
        return f"[{key}] {event['text']}"
    return f"[{key}] {event['bot']}: {event['text']}"


def _members(filename):
    if filename == "public.jsonl":
        return None
    if filename.startswith("dm-") and filename.endswith(".jsonl"):
        return filename[len("dm-") : -len(".jsonl")].split("-")
    if filename.startswith("huddle-") and filename.endswith(".jsonl"):
        return filename[len("huddle-") : -len(".jsonl")].split("-")
    return []


def _channel_key(members):
    ordered = sorted(members)
    prefix = "dm" if len(ordered) == 2 else "huddle"
    return prefix + "-" + "-".join(ordered)


def _quorum(state):
    alive = [bot for bot in state["present"] if bot not in state["left"]]
    return len(alive) >= state["quorum"]


def _weighted(present, state, rng, avoid):
    counts = state.get("counts") or {}
    mean = sum(counts.get(bot, 0) for bot in present) / len(present)
    avoid = set(avoid)

    def silence(bot):
        spoke = (state.get("spoke_at") or {}).get(bot)
        if spoke is None:
            return state.get("turns", 0) + 1
        return state.get("turns", 0) - spoke

    forced = [bot for bot in present if silence(bot) > 8 and bot not in avoid]
    if forced:
        return max(forced, key=silence)
    bots = []
    weights = []
    for bot in present:
        if bot in avoid:
            continue
        if counts.get(bot, 0) > mean + 2:
            continue
        bots.append(bot)
        weights.append((1 + silence(bot)) ** 1.5)
    if not bots:
        bots = [bot for bot in present if bot not in avoid] or list(present)
        weights = [1 for _bot in bots]
    return rng.choices(bots, weights=weights)[0]


def _reject_incomplete(sheet, cards):
    for key in SHEET_KEYS:
        if key not in sheet or not str(sheet[key]).strip():
            raise ValueError(f"sheet missing {key}")
    if sheet["goals_conflict"] not in ("yes", "no"):
        raise ValueError("goals_conflict must be yes or no")
    if str(sheet["word_cap"]).strip() != "45":
        raise ValueError("word_cap must be 45")
    if sheet["end"] != "time":
        raise ValueError("end must be time")
    parse_duration(sheet["duration"])
    quorum = int(sheet["quorum"])
    present = _names(sheet["present"])
    if quorum < 1 or quorum > len(present):
        raise ValueError("quorum is out of range")
    if any("-" in name or " " in name for name in present):
        raise ValueError("names cannot contain spaces or hyphens")
    for card in cards:
        if card["bot"] not in present:
            raise ValueError(f"unknown card: {card['bot']}")
        if card["witness"] not in card["secret"]:
            raise ValueError(f"witness is not in the secret: {card['bot']}")
        if len(card["witness"]) < 8:
            raise ValueError(f"witness is too short: {card['bot']}")


def _reject_cross(cards):
    for card in cards:
        for other in cards:
            if other["bot"] == card["bot"]:
                continue
            blob = " ".join((card["knows"], card["goal"]))
            for field in ("secret", "witness", "goal"):
                snippet = other[field]
                if len(snippet) >= 8 and snippet in blob:
                    raise ValueError(f"{card['bot']} knows private text of {other['bot']}")


def _pairs(lines, keys, label):
    found = []
    for line in lines:
        if not line.strip():
            continue
        if ":" not in line:
            raise ValueError(f"invalid {label} line: {line}")
        key, value = line.split(":", 1)
        found.append((key.strip(), value.strip()))
    names = [key for key, _value in found]
    if names != list(keys):
        raise ValueError(f"{label} fields must be " + ", ".join(keys))
    return {key: value for key, value in found}


def _split_bots(lines):
    blocks = []
    current = []
    for line in lines:
        if line.startswith("bot:") and current:
            blocks.append(current)
            current = [line]
        elif line.strip():
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _find_label(lines, label):
    for index, line in enumerate(lines):
        if line.strip() == label:
            return index
    return None


def _names(text):
    return [part.strip() for part in str(text).split(",") if part.strip()]


def _knows(text):
    parsed = {}
    for part in str(text).split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"invalid knows entry: {part}")
        name, fact = part.split("=", 1)
        parsed[name.strip()] = fact.strip()
    return parsed


def _parse_cast(text):
    people = []
    for part in text.split(","):
        part = part.strip()
        if ":" not in part:
            raise ValueError(f"cast entry needs a public fact: {part}")
        name, fact = part.split(":", 1)
        name = name.strip()
        fact = fact.strip()
        if not name or not fact or "-" in name:
            raise ValueError(f"invalid cast entry: {part}")
        people.append((name, fact))
    if len(people) < 1:
        raise ValueError("cast is empty")
    return people


def _sheet_map(text):
    mapped = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        mapped[key.strip()] = value.strip()
    return mapped


def _prompt(name):
    return _read(os.path.join(PROMPTS, name))


def _stream_text(stdout):
    parts = []
    for line in stdout.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("type") == "text":
            parts.append(item.get("data") or "")
    return "".join(parts).strip()


def _stream_session(stdout):
    found = None
    for line in stdout.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("sessionId"):
            found = item["sessionId"]
    return found


def _read_events(path):
    if not os.path.exists(path):
        return []
    raw_lines = _read(path).splitlines()
    last = _last_nonempty(raw_lines)
    events = []
    for index, line in enumerate(raw_lines):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            events.append(json.loads(stripped))
        except json.JSONDecodeError:
            if index != last:
                raise
            head = "\n".join(raw_lines[:index])
            _write(path, head + ("\n" if head else ""))
            return events
    return events


def _last_nonempty(lines):
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].strip():
            return index
    return -1


def _write_events(path, events):
    body = "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events)
    _write(path, body)


def _append_jsonl(path, record):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read(path):
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def _save_json(path, payload):
    _write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    sys.exit(main())
