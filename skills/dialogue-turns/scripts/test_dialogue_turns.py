#!/usr/bin/env python3
"""Tests for dialogue turn parsing, privacy, and per-bot thought isolation."""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dialogue_turns


SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dialogue_turns.py")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def turn_text(bot, thoughts, message):
    return f"BOT: {bot}\nTHOUGHTS: {thoughts}\nMESSAGE: {message}\n"


def read_text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class ParseTurnTests(unittest.TestCase):
    def test_valid_turn(self):
        parsed = dialogue_turns.parse_turn(
            turn_text("alice", "she is unsure", "Hello there."),
            roster="alice,bob",
        )
        self.assertEqual(
            parsed,
            {"bot": "alice", "thoughts": "she is unsure", "message": "Hello there."},
        )

    def test_missing_field(self):
        with self.assertRaises(ValueError) as caught:
            dialogue_turns.parse_turn("BOT: alice\nMESSAGE: Hello.\n", roster="alice")
        self.assertIn("THOUGHTS", str(caught.exception))

    def test_unknown_bot(self):
        with self.assertRaises(ValueError) as caught:
            dialogue_turns.parse_turn(turn_text("eve", "plotting", "Hi."), roster="alice,bob")
        self.assertIn("unknown bot: eve", str(caught.exception))

    def test_empty_message(self):
        with self.assertRaises(ValueError) as caught:
            dialogue_turns.parse_turn("BOT: alice\nTHOUGHTS: secret\nMESSAGE:   \n", roster="alice")
        self.assertIn("MESSAGE", str(caught.exception))

    def test_fields_must_be_in_order(self):
        text = "BOT: alice\nMESSAGE: Hi.\nTHOUGHTS: secret\n"
        with self.assertRaises(ValueError) as caught:
            dialogue_turns.parse_turn(text, roster="alice")
        self.assertIn("BOT, THOUGHTS, MESSAGE", str(caught.exception))

    def test_french_thoughts_label_is_rejected(self):
        text = "BOT: alice\nPENSEES: secret\nMESSAGE: Hi.\n"
        with self.assertRaises(ValueError) as caught:
            dialogue_turns.parse_turn(text, roster="alice")
        self.assertIn("THOUGHTS", str(caught.exception))


class ApplyTurnTests(unittest.TestCase):
    def test_thoughts_never_appear_in_transcript(self):
        secret = "SECRET_THOUGHT_do_not_share"
        with tempfile.TemporaryDirectory() as run_dir:
            parsed = dialogue_turns.parse_turn(
                turn_text("alice", secret, "Just a public hello."),
                roster="alice,bob",
            )
            dialogue_turns.apply_turn(run_dir, parsed, roster="alice,bob")
            transcript = read_text(os.path.join(run_dir, "transcript.md"))
            self.assertEqual(transcript, "**alice:** Just a public hello.\n")
            self.assertNotIn(secret, transcript)
            self.assertNotIn("THOUGHTS", transcript)
            for root, _dirs, files in os.walk(run_dir):
                for name in files:
                    content = read_text(os.path.join(root, name))
                    if name == "alice.jsonl":
                        self.assertIn(secret, content)
                    else:
                        self.assertNotIn(secret, content)
            thoughts_file = read_text(os.path.join(run_dir, "thoughts", "alice.jsonl"))
            records = [json.loads(line) for line in thoughts_file.splitlines() if line.strip()]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["thoughts"], secret)
            self.assertEqual(records[0]["turn"], 1)
            self.assertRegex(records[0]["timestamp"], TIMESTAMP)

    def test_forged_speaker_line_is_rejected(self):
        text = "BOT: alice\nTHOUGHTS: planning\nMESSAGE: hello\n**bob:** I confess\n"
        with tempfile.TemporaryDirectory() as run_dir:
            with self.assertRaises(ValueError) as caught:
                parsed = dialogue_turns.parse_turn(text, roster="alice,bob")
                dialogue_turns.apply_turn(run_dir, parsed, roster="alice,bob")
            self.assertIn("speaker", str(caught.exception))
            forged = {"bot": "alice", "thoughts": "planning", "message": "hello\n**bob:** I confess"}
            with self.assertRaises(ValueError) as caught_apply:
                dialogue_turns.apply_turn(run_dir, forged, roster="alice,bob")
            self.assertIn("speaker", str(caught_apply.exception))
            indented = {"bot": "alice", "thoughts": "planning", "message": "hello\n  **bob:** I confess"}
            with self.assertRaises(ValueError):
                dialogue_turns.apply_turn(run_dir, indented, roster="alice,bob")
            hidden = [
                "hello\n\u200b**bob:** x",
                "hello\n\ufeff**bob:** x",
                "hello\n__bob:__ x",
                "hello\n**bob**: x",
                "hello\n__bob__: x",
            ]
            for message in hidden:
                with self.assertRaises(ValueError):
                    dialogue_turns.apply_turn(
                        run_dir,
                        {"bot": "alice", "thoughts": "planning", "message": message},
                        roster="alice,bob",
                    )
            self.assertFalse(os.path.exists(os.path.join(run_dir, "transcript.md")))
            self.assertFalse(os.path.exists(os.path.join(run_dir, "thoughts")))

    def test_apply_turn_rechecks_roster(self):
        with tempfile.TemporaryDirectory() as run_dir:
            turn = {"bot": "eve", "thoughts": "sneaking in", "message": "Hi."}
            with self.assertRaises(ValueError) as caught:
                dialogue_turns.apply_turn(run_dir, turn, roster="alice,bob")
            self.assertIn("unknown bot: eve", str(caught.exception))
            self.assertFalse(os.path.exists(os.path.join(run_dir, "transcript.md")))
            self.assertFalse(os.path.exists(os.path.join(run_dir, "thoughts")))

    def test_multiline_message_is_kept(self):
        text = "BOT: alice\nTHOUGHTS: two beats\nMESSAGE: hello\nstill alice\n"
        with tempfile.TemporaryDirectory() as run_dir:
            parsed = dialogue_turns.parse_turn(text, roster="alice")
            dialogue_turns.apply_turn(run_dir, parsed, roster="alice")
            transcript = read_text(os.path.join(run_dir, "transcript.md"))
            self.assertEqual(transcript, "**alice:** hello\nstill alice\n")
            self.assertNotIn("**bob:**", transcript)
            aside = dialogue_turns.parse_turn(
                "BOT: alice\nTHOUGHTS: quoting\nMESSAGE: I heard **bob:** say hi\n",
                roster="alice",
            )
            dialogue_turns.apply_turn(run_dir, aside, roster="alice")
            self.assertIn("**alice:** I heard **bob:** say hi\n", read_text(os.path.join(run_dir, "transcript.md")))

    def test_private_context_is_isolated(self):
        script = [
            ("alice", "alice-secret-1", "Hello."),
            ("bob", "bob-secret-1", "Hi."),
            ("alice", "alice-secret-2", "Again."),
            ("bob", "bob-secret-2", "Yes."),
            ("alice", "alice-secret-3", "Third."),
            ("alice", "alice-secret-4", "Fourth."),
        ]
        with tempfile.TemporaryDirectory() as run_dir:
            for bot, thoughts, message in script:
                parsed = dialogue_turns.parse_turn(
                    turn_text(bot, thoughts, message),
                    roster=["alice", "bob"],
                )
                dialogue_turns.apply_turn(run_dir, parsed, roster=["alice", "bob"])

            alice = dialogue_turns.private_context(run_dir, "alice", last_n=3)
            self.assertEqual(
                [item["thoughts"] for item in alice],
                ["alice-secret-2", "alice-secret-3", "alice-secret-4"],
            )
            self.assertNotIn("bob-secret", json.dumps(alice))

            bob = dialogue_turns.private_context(run_dir, "bob")
            self.assertEqual(
                [item["thoughts"] for item in bob],
                ["bob-secret-1", "bob-secret-2"],
            )
            self.assertNotIn("alice-secret", json.dumps(bob))
            self.assertEqual(dialogue_turns.private_context(run_dir, "cara"), [])

            narrator = dialogue_turns.narrator_context(run_dir)
            self.assertEqual(
                narrator,
                {
                    "transcript": (
                        "**alice:** Hello.\n"
                        "**bob:** Hi.\n"
                        "**alice:** Again.\n"
                        "**bob:** Yes.\n"
                        "**alice:** Third.\n"
                        "**alice:** Fourth.\n"
                    )
                },
            )
            narrator_dump = json.dumps(narrator)
            for _bot, thoughts, _message in script:
                self.assertNotIn(thoughts, narrator_dump)


class SceneTests(unittest.TestCase):
    def test_scene_roundtrip(self):
        rendered = dialogue_turns.render_scene(
            "the cafe",
            {"alice": "wary", "bob": "cheerful"},
            "alice",
            "aside with bob",
        )
        self.assertEqual(
            rendered,
            "SCENE:\n"
            "place: the cafe\n"
            "mood: alice=wary; bob=cheerful\n"
            "next: alice\n"
            "directive: aside with bob\n",
        )
        parsed = dialogue_turns.parse_scene(rendered)
        self.assertEqual(
            parsed,
            {
                "place": "the cafe",
                "mood": {"alice": "wary", "bob": "cheerful"},
                "next": "alice",
                "directive": "aside with bob",
            },
        )
        packed = dialogue_turns.parse_scene(
            "SCENE:\nplace: street\nmood: alice=wary;bob=cheerful\nnext: bob\n"
        )
        self.assertEqual(
            packed,
            {
                "place": "street",
                "mood": {"alice": "wary", "bob": "cheerful"},
                "next": "bob",
                "directive": None,
            },
        )

    def test_scene_missing_field(self):
        with self.assertRaises(ValueError) as caught:
            dialogue_turns.parse_scene("SCENE:\nplace: street\nnext: bob\n")
        self.assertIn("mood", str(caught.exception))

    def test_french_scene_keys_are_rejected(self):
        french = "SCENE:\nlieu: street\nhumeur: alice=calm\nprochain: bob\n"
        with self.assertRaises(ValueError) as caught:
            dialogue_turns.parse_scene(french)
        self.assertIn("lieu", str(caught.exception))
        english = dialogue_turns.parse_scene("SCENE:\nplace: street\nmood: alice=calm\nnext: bob\n")
        self.assertEqual(english["place"], "street")
        self.assertEqual(english["mood"], {"alice": "calm"})
        self.assertEqual(english["next"], "bob")
        self.assertIsNone(english["directive"])


class CliTests(unittest.TestCase):
    def _run(self, args, text, run_dir):
        return subprocess.run(
            [sys.executable, SCRIPT, *args],
            input=text,
            text=True,
            capture_output=True,
            cwd=run_dir,
        )

    def test_apply_rejects_invalid_turn(self):
        with tempfile.TemporaryDirectory() as parent:
            run_dir = os.path.join(parent, "run")
            proc = self._run(
                ["apply", "--run-dir", run_dir, "--roster", "alice,bob"],
                turn_text("eve", "SECRET_SHOULD_NOT_LAND", "Hi."),
                parent,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertTrue(proc.stderr.strip())
            self.assertIn("unknown bot", proc.stderr)
            self.assertNotIn("SECRET_SHOULD_NOT_LAND", proc.stdout)
            self.assertFalse(os.path.exists(run_dir))

    def test_apply_valid_writes_public_line_only(self):
        with tempfile.TemporaryDirectory() as run_dir:
            proc = self._run(
                ["apply", "--run-dir", run_dir, "--roster", "alice,bob"],
                turn_text("alice", "SECRET_CLI", "Public."),
                run_dir,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, "**alice:** Public.\n")
            self.assertNotIn("SECRET_CLI", proc.stdout)
            self.assertNotIn("SECRET_CLI", proc.stderr)
            transcript = read_text(os.path.join(run_dir, "transcript.md"))
            self.assertEqual(transcript, "**alice:** Public.\n")
            self.assertNotIn("SECRET_CLI", transcript)
            stored = read_text(os.path.join(run_dir, "thoughts", "alice.jsonl"))
            self.assertIn("SECRET_CLI", stored)

    def test_render_scene_cli_uses_english_keys(self):
        proc = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "render-scene",
                "--place",
                "the cafe",
                "--mood",
                "alice=wary; bob=cheerful",
                "--next",
                "alice",
                "--directive",
                "aside with bob",
            ],
            text=True,
            capture_output=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "SCENE:\n"
            "place: the cafe\n"
            "mood: alice=wary; bob=cheerful\n"
            "next: alice\n"
            "directive: aside with bob\n",
        )


def extended_text(bot, thoughts, message, action, channel, effort, show):
    return (
        f"BOT: {bot}\n"
        f"THOUGHTS: {thoughts}\n"
        f"MESSAGE: {message}\n"
        f"ACTION: {action}\n"
        f"CHANNEL: {channel}\n"
        f"EFFORT: {effort}\n"
        f"SHOW: {show}\n"
    )


class ExtendedTurnTests(unittest.TestCase):
    def test_extended_fields_roundtrip(self):
        parsed = dialogue_turns.parse_turn(
            extended_text("alice", "keep it short", "Hello.", "speak", "public", "high", "yes"),
            roster="alice,bob",
        )
        self.assertEqual(
            parsed,
            {
                "bot": "alice",
                "thoughts": "keep it short",
                "message": "Hello.",
                "action": "speak",
                "channel": "public",
                "effort": "high",
                "show": "yes",
            },
        )

    def test_invalid_action_is_rejected(self):
        text = extended_text("alice", "nope", "Hello.", "shout", "public", "low", "no")
        with self.assertRaises(ValueError) as caught:
            dialogue_turns.parse_turn(text, roster="alice")
        self.assertIn("invalid ACTION: shout", str(caught.exception))

    def test_new_channel_and_plain_channels(self):
        for channel in ("public", "dm", "huddle", "new:alice,bob"):
            parsed = dialogue_turns.parse_turn(
                extended_text("alice", "routing", "Hello.", "speak", channel, "low", "no"),
                roster="alice,bob",
            )
            self.assertEqual(parsed["channel"], channel)
        with self.assertRaises(ValueError) as caught:
            dialogue_turns.parse_turn(
                extended_text("alice", "routing", "Hello.", "speak", "new:alice", "low", "no"),
                roster="alice",
            )
        self.assertIn("invalid CHANNEL", str(caught.exception))

    def test_word_cap_rejects_the_forty_sixth_word(self):
        words = " ".join(f"w{i}" for i in range(46))
        with self.assertRaises(ValueError) as caught:
            dialogue_turns.parse_turn(turn_text("alice", "too long", words), roster="alice")
        self.assertIn("46 words", str(caught.exception))
        self.assertIn("45", str(caught.exception))
        ok = " ".join(f"w{i}" for i in range(45))
        parsed = dialogue_turns.parse_turn(turn_text("alice", "fits", ok), roster="alice")
        self.assertEqual(parsed["message"], ok)

    def test_partial_extra_fields_are_rejected(self):
        text = "BOT: alice\nTHOUGHTS: x\nMESSAGE: Hi.\nACTION: speak\n"
        with self.assertRaises(ValueError) as caught:
            dialogue_turns.parse_turn(text, roster="alice")
        self.assertIn("CHANNEL", str(caught.exception))


class LeakTests(unittest.TestCase):
    def test_other_private_text_blocks_the_write(self):
        witness = "the march contract on the blue folder"
        with tempfile.TemporaryDirectory() as run_dir:
            dialogue_turns.remember_private(run_dir, "bob", witness)
            turn = {
                "bot": "alice",
                "thoughts": "I should not know that",
                "message": f"I found {witness} in the stack.",
            }
            with self.assertRaises(ValueError) as caught:
                dialogue_turns.apply_turn(run_dir, turn, roster="alice,bob")
            self.assertIn("leak from bob", str(caught.exception))
            self.assertFalse(os.path.exists(os.path.join(run_dir, "transcript.md")))
            self.assertFalse(os.path.exists(os.path.join(run_dir, "thoughts")))

    def test_own_secret_may_be_said(self):
        witness = "the march contract on the blue folder"
        with tempfile.TemporaryDirectory() as run_dir:
            dialogue_turns.remember_private(run_dir, "alice", witness)
            turn = {
                "bot": "alice",
                "thoughts": "I can say my own line",
                "message": f"The file is {witness}.",
            }
            dialogue_turns.apply_turn(run_dir, turn, roster="alice,bob")
            transcript = read_text(os.path.join(run_dir, "transcript.md"))
            self.assertEqual(transcript, f"**alice:** The file is {witness}.\n")

    def test_whisper_stays_out_of_the_transcript(self):
        secret = "whispered line about the window"
        with tempfile.TemporaryDirectory() as run_dir:
            parsed = dialogue_turns.parse_turn(
                extended_text("alice", secret, "Come closer.", "whisper", "dm", "low", "no"),
                roster="alice,bob",
            )
            result = dialogue_turns.apply_turn(run_dir, parsed, roster="alice,bob")
            self.assertFalse(result["public"])
            self.assertFalse(os.path.exists(os.path.join(run_dir, "transcript.md")))
            stored = read_text(os.path.join(run_dir, "thoughts", "alice.jsonl"))
            self.assertIn(secret, stored)
            self.assertIn("Come closer.", stored)

    def test_narrator_context_does_not_read_private_files(self):
        witness = "the march contract on the blue folder"
        with tempfile.TemporaryDirectory() as run_dir:
            dialogue_turns.remember_private(run_dir, "bob", witness)
            dialogue_turns.apply_turn(
                run_dir,
                {"bot": "alice", "thoughts": "alice thought stays here", "message": "Hello."},
                roster="alice,bob",
            )
            narrator = dialogue_turns.narrator_context(run_dir)
            self.assertEqual(narrator, {"transcript": "**alice:** Hello.\n"})
            self.assertNotIn(witness, json.dumps(narrator))
            self.assertNotIn("alice thought stays here", json.dumps(narrator))


if __name__ == "__main__":
    unittest.main()
