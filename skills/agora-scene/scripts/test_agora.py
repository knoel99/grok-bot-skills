#!/usr/bin/env python3
"""Offline tests for the scene runner. The grok binary is a local fake."""

import json
import os
import random
import stat
import sys
import tempfile
import textwrap
import threading
import unittest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import agora


def director_text(people, quorum="2", duration="30 m"):
    present = ", ".join(name for name, _goal, _secret, _witness, _knows in people)
    lines = [
        "SHEET:",
        "title: A short argument",
        "place: a small room",
        "register: spoken",
        f"duration: {duration}",
        "word_cap: 45",
        f"quorum: {quorum}",
        "end: time",
        "goals_conflict: yes",
        "languages: " + "; ".join(f"{name}=english" for name, *_rest in people),
        "may_interrupt: " + (
            f"{people[0][0]}>{people[0][0]}" if len(people) == 1 else ">".join(name for name, *_rest in people[:2])
        ),
        "stakes: whether to send the note",
        f"present: {present}",
        "PRIVATE:",
    ]
    for name, goal, secret, witness, knows in people:
        lines.extend(
            [
                f"bot: {name}",
                f"goal: {goal}",
                f"secret: {secret}",
                f"witness: {witness}",
                f"knows: {knows}",
            ]
        )
    return "\n".join(lines) + "\n"


ADA = (
    "ada",
    "keep the note unsent today",
    "She hid the march contract on the blue folder",
    "the march contract on the blue folder",
    "bo=speaks first when nervous",
)
BO = (
    "bo",
    "send the note before noon",
    "the spare key is under the mat tonight",
    "the spare key is under the mat",
    "ada=wrote the note",
)
CY = (
    "cy",
    "wait until tomorrow morning",
    "the spare bulb is in the drawer",
    "the spare bulb is in the drawer",
    "ada=wrote the note",
)
DEE = (
    "dee",
    "read the note once more",
    "the red stamp is on the copy",
    "the red stamp is on the copy",
    "ada=wrote the note",
)


def install(scene, people, quorum="2", duration="30 m"):
    sheet, cards = agora.parse_director(director_text(people, quorum, duration))
    return agora.write_sheet(scene, sheet, cards)


def turn(bot, message="Hello.", action="speak", channel="public", effort="low", show="no"):
    return (
        f"BOT: {bot}\n"
        f"THOUGHTS: Only this speaker knows this line.\n"
        f"MESSAGE: {message}\n"
        f"ACTION: {action}\n"
        f"CHANNEL: {channel}\n"
        f"EFFORT: {effort}\n"
        f"SHOW: {show}\n"
    )


def speaker_of(prompt):
    for line in prompt.splitlines():
        if line.startswith("SPEAKER: "):
            return line.split(": ", 1)[1].strip()
    return ""


class ManualClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class RecordingGrok:
    def __init__(self, reply, seconds=30):
        self.reply = reply
        self.seconds = seconds
        self.calls = []
        self.lock = threading.Lock()

    def complete(self, prompt, session_id, effort, timeout):
        with self.lock:
            self.calls.append(
                {"prompt": prompt, "session_id": session_id, "effort": effort, "timeout": timeout}
            )
            number = len(self.calls)
        text = self.reply(prompt, number)
        return agora.Completed(text, session_id or "sid-local", False, self.seconds)


def public_events(scene):
    return agora._read_events(os.path.join(scene, "channels", "public.jsonl"))


def transcript(scene):
    with open(os.path.join(scene, "transcript.md"), encoding="utf-8") as handle:
        return handle.read()


FAKE_GROK = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, sys, time
    args = sys.argv[1:]

    def grab(flag):
        if flag not in args:
            return ""
        return args[args.index(flag) + 1]

    prompt = grab("-p")
    record = {"prompt": prompt, "effort": grab("--effort"), "resume": grab("--resume")}
    with open(os.environ["MOCK_LOG"], "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\\n")
    mode = os.environ.get("MOCK_MODE", "turn")
    if mode == "sheet":
        data = open(os.environ["MOCK_SHEET"], encoding="utf-8").read()
        sys.stdout.write(json.dumps({"type": "text", "sessionId": "sheet-1", "data": data}) + "\\n")
        raise SystemExit(0)
    if mode == "timeout" and not record["resume"]:
        sys.stdout.write(json.dumps({"type": "text", "sessionId": "kept-1", "data": ""}) + "\\n")
        sys.stdout.flush()
        time.sleep(30)
        raise SystemExit(0)
    bot = "ada"
    for line in prompt.splitlines():
        if line.startswith("SPEAKER: "):
            bot = line.split(": ", 1)[1].strip()
    if "ROLE: narrator" in prompt:
        body = "The room stays quiet."
    else:
        body = (
            "BOT: " + bot + "\\n"
            "THOUGHTS: Only this speaker knows this line.\\n"
            "MESSAGE: Hello.\\n"
            "ACTION: speak\\n"
            "CHANNEL: public\\n"
            "EFFORT: high\\n"
            "SHOW: no\\n"
        )
    sid = record["resume"] or "sid-1"
    sys.stdout.write(json.dumps({"type": "text", "sessionId": sid, "data": body}) + "\\n")
    """
)


def write_fake(directory):
    path = os.path.join(directory, "fake-grok.py")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(FAKE_GROK)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


def log_rows(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


class DurationTests(unittest.TestCase):
    def test_parse_duration_minutes_and_hours(self):
        self.assertEqual(agora.parse_duration("30 m"), 1800)
        self.assertEqual(agora.parse_duration("45 minutes"), 2700)
        self.assertEqual(agora.parse_duration("2 hours"), 7200)
        self.assertEqual(agora.parse_duration("1 hour"), 3600)

    def test_scene_stops_at_the_duration(self):
        with tempfile.TemporaryDirectory() as scene:
            install(scene, [ADA], quorum="1", duration="30 m")
            client = RecordingGrok(lambda prompt, number: turn("ada", "Hello."))
            state = agora.run_scene(scene, client, ManualClock(), 60)
            self.assertEqual(state["turns"], 2)
            self.assertEqual(len(client.calls), 2)
            self.assertEqual(clock_rows(scene), 2)


def clock_rows(scene):
    with open(os.path.join(scene, "turns.csv"), encoding="utf-8") as handle:
        return len(handle.read().splitlines()) - 1


class ChannelTests(unittest.TestCase):
    def test_a_bot_reads_only_its_own_channels(self):
        with tempfile.TemporaryDirectory() as scene:
            state = install(scene, [ADA, BO, CY], quorum="2")
            agora._append_jsonl(
                os.path.join(scene, "channels", "public.jsonl"),
                {"id": 1, "bot": "ada", "action": "speak", "kind": "line", "text": "Hello there.", "full": "Hello there.", "cut": False},
            )
            agora._append_jsonl(
                os.path.join(scene, "channels", "dm-ada-bo.jsonl"),
                {"id": 1, "bot": "ada", "action": "whisper", "kind": "line", "text": "Over here.", "full": "Over here.", "cut": False},
            )
            agora._append_jsonl(
                os.path.join(scene, "channels", "huddle-bo-cy.jsonl"),
                {"id": 1, "bot": "bo", "action": "whisper", "kind": "line", "text": "Not for ada.", "full": "Not for ada.", "cut": False},
            )
            heard = agora.collect_lines(scene, "ada", state)
            self.assertEqual(heard, ["[dm-ada-bo] ada: Over here.", "[public] ada: Hello there."])
            self.assertNotIn("Not for ada.", "\n".join(heard))
            profile = agora._profile(scene, "ada")
            prompt = agora.participant_prompt(agora.sheet_text(scene), profile, heard, seed=True)
            self.assertIn("the march contract on the blue folder", prompt)
            self.assertNotIn("the spare key is under the mat", prompt)
            self.assertNotIn("Not for ada.", prompt)

    def test_narrator_never_sees_private_content(self):
        with tempfile.TemporaryDirectory() as scene:
            install(scene, [ADA], quorum="1")
            agora._append_jsonl(
                os.path.join(scene, "channels", "dm-ada-bo.jsonl"),
                {
                    "id": 1,
                    "bot": "bo",
                    "action": "whisper",
                    "kind": "line",
                    "text": "the spare key is under the mat",
                    "full": "the spare key is under the mat",
                    "cut": False,
                },
            )

            def reply(prompt, _number):
                if "ROLE: narrator" in prompt:
                    return "The room stays quiet."
                bot = speaker_of(prompt) or "ada"
                if bot == "ada":
                    return turn("ada", "I am going.", action="leave")
                return turn(bot, "I will stay.")

            client = RecordingGrok(reply, seconds=1)
            agora.run_scene(scene, client, ManualClock(), 30, stop_when=lambda state: state["turns"] >= 1)
            narrator_calls = [call for call in client.calls if "ROLE: narrator" in call["prompt"]]
            self.assertEqual(len(narrator_calls), 1)
            prompt = narrator_calls[0]["prompt"]
            self.assertIn("I am going.", prompt)
            self.assertIn("a small room", prompt)
            self.assertNotIn("the spare key is under the mat", prompt)
            self.assertNotIn("the march contract on the blue folder", prompt)
            self.assertNotIn("keep the note unsent today", prompt)
            self.assertNotIn("Only this speaker knows this line.", prompt)


class LeakAndFloorTests(unittest.TestCase):
    def test_a_leak_is_blocked_and_own_secret_is_kept(self):
        with tempfile.TemporaryDirectory() as scene:
            state = install(scene, [ADA, BO])
            leaked = turn(
                "ada",
                "I found the spare key is under the mat in the stack.",
            )
            note = agora.commit_move(scene, state, "ada", leaked)
            self.assertEqual(note, "leak")
            self.assertEqual(public_events(scene), [])
            self.assertNotIn("spare key", transcript(scene))
            self.assertEqual(state["leaks_blocked"], 1)
            state = agora.load_state(scene)
            own = turn("ada", "The file is the march contract on the blue folder.")
            note = agora.commit_move(scene, state, "ada", own)
            self.assertEqual(note, "ok")
            self.assertIn("the march contract on the blue folder", transcript(scene))

    def test_an_interruption_cuts_the_turn(self):
        with tempfile.TemporaryDirectory() as scene:
            state = install(scene, [ADA, BO, CY])
            agora._save_json(os.path.join(scene, "sessions.json"), {"ada": "sid-ada", "bo": "sid-bo"})
            first = agora.commit_move(scene, state, "ada", turn("ada", "one two three four"))
            self.assertEqual(first, "ok")
            second = agora.commit_move(
                scene, state, "bo", turn("bo", "Enough."), mode="interrupt"
            )
            self.assertEqual(second, "ok")
            visible = [event["text"] for event in public_events(scene)]
            self.assertEqual(visible, ["one two", "The line was cut.", "Enough."])
            self.assertNotIn("three", transcript(scene))
            self.assertNotIn("four", transcript(scene))
            minds = agora._read(os.path.join(scene, "minds", "ada.jsonl"))
            self.assertIn("one two three four", minds)
            self.assertEqual(
                json.loads(agora._read(os.path.join(scene, "sessions.json"))),
                {"ada": "sid-ada", "bo": "sid-bo"},
            )
            heard = "\n".join(agora.collect_lines(scene, "cy", agora.load_state(scene)))
            self.assertIn("[public] ada: one two", heard)
            self.assertIn("The line was cut.", heard)
            self.assertNotIn("three", heard)
            self.assertNotIn("four", heard)

    def test_leave_is_final(self):
        with tempfile.TemporaryDirectory() as scene:
            install(scene, [ADA, BO], quorum="2")

            def reply(prompt, _number):
                if "ROLE: narrator" in prompt:
                    return "Ada leaves the room."
                return turn(speaker_of(prompt) or "ada", "I am going.", action="leave")

            client = RecordingGrok(reply, seconds=1)
            state = agora.run_scene(scene, client, ManualClock(), 60)
            self.assertIn("ada", state["left"])
            self.assertEqual(agora.collect_lines(scene, "ada", state), [])
            self.assertIn("I am going.", transcript(scene))
            later = [call for call in client.calls if speaker_of(call["prompt"]) == "ada"]
            self.assertEqual(len(later), 1)

    def test_a_left_bot_is_not_chosen(self):
        with tempfile.TemporaryDirectory() as scene:
            state = install(scene, [ADA, BO])
            state["left"] = ["ada"]
            for seed in range(5):
                choice = agora.choose_mode(state, random.Random(seed))
                self.assertEqual(choice["bot"], "bo")
                self.assertNotEqual(choice["bot"], "ada")


class EffortAndSessionTests(unittest.TestCase):
    def test_chosen_effort_is_passed_to_the_cli(self):
        with tempfile.TemporaryDirectory() as scene:
            install(scene, [ADA], quorum="1", duration="30 m")
            fake = write_fake(scene)
            log = os.path.join(scene, "mock.log")
            client = agora.SubprocessGrok(fake, timeout=5)
            previous = os.environ.get("MOCK_LOG")
            os.environ["MOCK_LOG"] = log
            os.environ["MOCK_MODE"] = "turn"
            try:
                agora.run_scene(
                    scene,
                    client,
                    ManualClock(),
                    60,
                    move_timeout=5,
                    stop_when=lambda state: state["turns"] >= 2,
                )
            finally:
                if previous is None:
                    os.environ.pop("MOCK_LOG", None)
                    os.environ.pop("MOCK_MODE", None)
                else:
                    os.environ["MOCK_LOG"] = previous
            rows = log_rows(log)
            self.assertEqual([row["effort"] for row in rows], ["low", "high"])

    def test_high_effort_is_capped(self):
        with tempfile.TemporaryDirectory() as scene:
            install(scene, [ADA], quorum="1")
            client = RecordingGrok(lambda prompt, number: turn("ada", "Hello.", effort="high"), seconds=10)
            agora.run_scene(scene, client, ManualClock(), 40)
            self.assertEqual([call["effort"] for call in client.calls], ["low", "high", "high", "medium"])

    def test_session_is_kept_after_a_timeout(self):
        with tempfile.TemporaryDirectory() as scene:
            install(scene, [ADA], quorum="1")
            fake = write_fake(scene)
            log = os.path.join(scene, "mock.log")
            os.environ["MOCK_LOG"] = log
            os.environ["MOCK_MODE"] = "timeout"
            try:
                client = agora.SubprocessGrok(fake, timeout=0.4)
                agora.run_scene(
                    scene,
                    client,
                    ManualClock(),
                    30,
                    move_timeout=0.4,
                    stop_when=lambda state: state["turns"] >= 1,
                )
            finally:
                os.environ.pop("MOCK_LOG", None)
                os.environ.pop("MOCK_MODE", None)
            rows = log_rows(log)
            self.assertGreaterEqual(len(rows), 2)
            self.assertEqual(rows[0]["resume"], "")
            self.assertEqual(rows[1]["resume"], "kept-1")
            self.assertIn("You are one participant in a live conversation.", rows[0]["prompt"])
            self.assertNotIn("You are one participant in a live conversation.", rows[1]["prompt"])
            self.assertIn("the march contract on the blue folder", rows[0]["prompt"])
            self.assertNotIn("the march contract on the blue folder", rows[1]["prompt"])
            sessions = json.loads(agora._read(os.path.join(scene, "sessions.json")))
            self.assertEqual(sessions["ada"], "kept-1")

    def test_show_announces_thinking_only_when_asked(self):
        with tempfile.TemporaryDirectory() as scene:
            install(scene, [ADA], quorum="1")
            client = RecordingGrok(lambda prompt, number: turn("ada", "Hello.", show="yes"), seconds=10)
            agora.run_scene(scene, client, ManualClock(), 20)
            self.assertIn("*ada is thinking.*", transcript(scene))
        with tempfile.TemporaryDirectory() as scene:
            install(scene, [ADA], quorum="1")
            client = RecordingGrok(lambda prompt, number: turn("ada", "Hello.", show="no"), seconds=10)
            agora.run_scene(scene, client, ManualClock(), 20)
            self.assertIn("**ada:** Hello.", transcript(scene))
            self.assertNotIn("is thinking", transcript(scene))


class AsyncAndGateTests(unittest.TestCase):
    def test_the_slow_bot_does_not_block_the_fast_one(self):
        with tempfile.TemporaryDirectory() as scene:
            install(scene, [ADA, BO], quorum="1")
            gate = {
                "ada": threading.Event(),
                "bo": threading.Event(),
                "started": {"ada": threading.Event(), "bo": threading.Event()},
            }

            class GateGrok:
                def complete(self, prompt, session_id, effort, timeout):
                    bot = speaker_of(prompt) or "ada"
                    if "ROLE: narrator" in prompt:
                        return agora.Completed("The room stays quiet.", "narr", False, 0)
                    gate["started"][bot].set()
                    gate[bot].wait(timeout=5)
                    message = "beta line from bo" if bot == "bo" else "alpha line from ada"
                    return agora.Completed(turn(bot, message), "sid-" + bot, False, 0)

            holder = {}

            def run():
                holder["state"] = agora.run_scene(
                    scene,
                    GateGrok(),
                    ManualClock(),
                    3600,
                    stop_when=lambda state: state["turns"] >= 1,
                )

            worker = threading.Thread(target=run)
            worker.start()
            try:
                self.assertTrue(gate["started"]["ada"].wait(2))
                self.assertTrue(gate["started"]["bo"].wait(2))
                gate["bo"].set()
                worker.join(3)
                self.assertFalse(worker.is_alive())
                page = transcript(scene)
                self.assertIn("beta line from bo", page)
                self.assertNotIn("alpha line from ada", page)
            finally:
                gate["ada"].set()
                gate["bo"].set()
                worker.join(2)

    def test_private_channel_cap(self):
        with tempfile.TemporaryDirectory() as scene:
            state = install(scene, [ADA, BO, CY, DEE], quorum="2")
            pairs = [("bo",), ("cy",), ("dee",), ("bo", "cy")]
            for extra in pairs:
                names = ",".join(("ada",) + extra)
                note = agora.commit_move(
                    scene,
                    state,
                    "ada",
                    turn("ada", "Come here.", action="whisper", channel=f"new:{names}"),
                )
                self.assertEqual(note, "ok")
            blocked = agora.commit_move(
                scene,
                state,
                "ada",
                turn("ada", "One more.", action="whisper", channel="new:ada,bo,dee"),
            )
            self.assertEqual(blocked, "cap")
            names = os.listdir(os.path.join(scene, "channels"))
            private = [name for name in names if name.startswith("dm-") or name.startswith("huddle-")]
            self.assertEqual(sorted(private), sorted([
                "dm-ada-bo.jsonl",
                "dm-ada-cy.jsonl",
                "dm-ada-dee.jsonl",
                "huddle-ada-bo-cy.jsonl",
            ]))

    def test_sheet_approval_gates_and_private_cards(self):
        with tempfile.TemporaryDirectory() as parent:
            scene = os.path.join(parent, "scene")
            fake = write_fake(parent)
            sheet_path = os.path.join(parent, "director.txt")
            with open(sheet_path, "w", encoding="utf-8") as handle:
                handle.write(director_text([ADA, BO]))
            log = os.path.join(parent, "mock.log")
            os.environ["MOCK_LOG"] = log
            os.environ["MOCK_MODE"] = "sheet"
            os.environ["MOCK_SHEET"] = sheet_path
            try:
                code = agora.main(
                    [
                        "sheet",
                        "--scene",
                        scene,
                        "--situation",
                        "whether to send the note",
                        "--cast",
                        "ada:writes notes,bo:reads notes",
                        "--duration",
                        "30 m",
                        "--grok",
                        fake,
                    ]
                )
                self.assertEqual(code, 0)
                page = agora.sheet_text(scene)
                self.assertIn("approved: no", page)
                self.assertNotIn("the march contract on the blue folder", page)
                mind = agora._read(os.path.join(scene, "minds", "ada.jsonl"))
                self.assertIn("the march contract on the blue folder", mind)
                prompt = agora.narrator_prompt(page, ["[public] ada: Hello."], seed=True)
                self.assertNotIn("the march contract on the blue folder", prompt)
                self.assertIn("a small room", prompt)
                self.assertEqual(agora.main(["pilot", "--scene", scene, "--grok", fake]), 2)
                agora._write(os.path.join(scene, "sheet.md"), page.replace("approved: no", "approved: yes"))
                self.assertEqual(agora.main(["run", "--scene", scene, "--grok", fake]), 2)
            finally:
                os.environ.pop("MOCK_LOG", None)
                os.environ.pop("MOCK_MODE", None)
                os.environ.pop("MOCK_SHEET", None)

    def test_incomplete_sheet_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            agora.parse_director("SHEET:\ntitle: only\nPRIVATE:\nbot: ada\n")
        self.assertIn("sheet fields", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
