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
    return f"BOT: {bot}\nPENSEES: {thoughts}\nMESSAGE: {message}\n"


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
        self.assertIn("PENSEES", str(caught.exception))

    def test_unknown_bot(self):
        with self.assertRaises(ValueError) as caught:
            dialogue_turns.parse_turn(turn_text("eve", "plotting", "Hi."), roster="alice,bob")
        self.assertIn("unknown bot: eve", str(caught.exception))

    def test_empty_message(self):
        with self.assertRaises(ValueError) as caught:
            dialogue_turns.parse_turn("BOT: alice\nPENSEES: secret\nMESSAGE:   \n", roster="alice")
        self.assertIn("MESSAGE", str(caught.exception))

    def test_fields_must_be_in_order(self):
        text = "BOT: alice\nMESSAGE: Hi.\nPENSEES: secret\n"
        with self.assertRaises(ValueError) as caught:
            dialogue_turns.parse_turn(text, roster="alice")
        self.assertIn("order", str(caught.exception))


class ApplyTurnTests(unittest.TestCase):
    def test_thoughts_never_appear_in_transcript(self):
        secret = "SECRET_THOUGHT_do_not_share"
        with tempfile.TemporaryDirectory() as run_dir:
            parsed = dialogue_turns.parse_turn(
                turn_text("alice", secret, "Just a public hello."),
                roster="alice,bob",
            )
            dialogue_turns.apply_turn(run_dir, parsed)
            transcript = dialogue_turns._read_text(os.path.join(run_dir, "transcript.md"))
            self.assertEqual(transcript, "**alice:** Just a public hello.\n")
            self.assertNotIn(secret, transcript)
            self.assertNotIn("PENSEES", transcript)
            for root, _dirs, files in os.walk(run_dir):
                for name in files:
                    content = dialogue_turns._read_text(os.path.join(root, name))
                    if name == "alice.jsonl":
                        self.assertIn(secret, content)
                    else:
                        self.assertNotIn(secret, content)
            records = dialogue_turns._read_jsonl(os.path.join(run_dir, "thoughts", "alice.jsonl"))
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["thoughts"], secret)
            self.assertEqual(records[0]["turn"], 1)
            self.assertRegex(records[0]["timestamp"], TIMESTAMP)

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
                dialogue_turns.apply_turn(run_dir, parsed)

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
            all_thoughts = [item["thoughts"] for item in narrator["thoughts"]]
            self.assertEqual(
                all_thoughts,
                [
                    "alice-secret-1",
                    "bob-secret-1",
                    "alice-secret-2",
                    "bob-secret-2",
                    "alice-secret-3",
                    "alice-secret-4",
                ],
            )
            self.assertEqual([item["turn"] for item in narrator["thoughts"]], [1, 2, 3, 4, 5, 6])
            self.assertEqual(
                narrator["transcript"],
                "**alice:** Hello.\n"
                "**bob:** Hi.\n"
                "**alice:** Again.\n"
                "**bob:** Yes.\n"
                "**alice:** Third.\n"
                "**alice:** Fourth.\n",
            )
            for _bot, thoughts, _message in script:
                self.assertNotIn(thoughts, narrator["transcript"])


class SceneTests(unittest.TestCase):
    def test_scene_roundtrip(self):
        rendered = dialogue_turns.render_scene(
            lieu="the cafe",
            humeur={"alice": "wary", "bob": "cheerful"},
            prochain="alice",
            consigne="aparte with bob",
        )
        self.assertEqual(
            rendered,
            "SCENE:\n"
            "lieu: the cafe\n"
            "humeur: alice=wary; bob=cheerful\n"
            "prochain: alice\n"
            "consigne: aparte with bob\n",
        )
        parsed = dialogue_turns.parse_scene(rendered)
        self.assertEqual(parsed["lieu"], "the cafe")
        self.assertEqual(parsed["humeur"], {"alice": "wary", "bob": "cheerful"})
        self.assertEqual(parsed["prochain"], "alice")
        self.assertEqual(parsed["consigne"], "aparte with bob")
        packed = dialogue_turns.parse_scene(
            "SCENE:\nlieu: street\nhumeur: alice=wary;bob=cheerful\nprochain: bob\n"
        )
        self.assertEqual(packed["humeur"], {"alice": "wary", "bob": "cheerful"})
        self.assertIsNone(packed["consigne"])

    def test_scene_missing_field(self):
        with self.assertRaises(ValueError) as caught:
            dialogue_turns.parse_scene("SCENE:\nlieu: street\nprochain: bob\n")
        self.assertIn("humeur", str(caught.exception))


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
            transcript = dialogue_turns._read_text(os.path.join(run_dir, "transcript.md"))
            self.assertEqual(transcript, "**alice:** Public.\n")
            self.assertNotIn("SECRET_CLI", transcript)
            stored = dialogue_turns._read_text(os.path.join(run_dir, "thoughts", "alice.jsonl"))
            self.assertIn("SECRET_CLI", stored)


if __name__ == "__main__":
    unittest.main()
