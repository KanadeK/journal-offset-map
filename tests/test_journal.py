import json
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from journal_offset_map.core import Entry, InputError, analyze, load_entries, timestamp

BOOT = "a" * 32
MACHINE = "b" * 32


def record(mono=1_000_000, wall=101_000_000, **extra):
    return {
        "_BOOT_ID": BOOT,
        "_MACHINE_ID": MACHINE,
        "__REALTIME_TIMESTAMP": str(wall),
        "__MONOTONIC_TIMESTAMP": str(mono),
        **extra,
    }


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "journal.jsonl"

    def write(self, text):
        self.path.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
        return self.path

    def load(self, records, **limits):
        self.write("\n".join(json.dumps(r) for r in records))
        return load_entries([self.path], **limits)

    def test_native_strings_and_binary_message(self):
        entry = self.load([record(MESSAGE=[0, 255, 10])])[0]
        self.assertEqual(entry.offset_us, 100_000_000)

    def test_blank_lines_preserve_line_reference(self):
        self.write("\n" + json.dumps(record()) + "\n\n")
        self.assertEqual(load_entries([self.path])[0].line, 2)

    def test_missing_machine_is_explicit(self):
        value = record()
        del value["_MACHINE_ID"]
        self.assertIsNone(self.load([value])[0].machine)

    def test_bad_ids_are_rejected(self):
        for key in ["_BOOT_ID", "_MACHINE_ID"]:
            for value in ["", "z" * 32, [BOOT], 1, True]:
                with self.subTest(key=key, value=value), self.assertRaises(InputError):
                    self.load([record(**{key: value})])

    def test_invalid_timestamps_are_rejected(self):
        for value in [None, "-1", -1, True, 1.1, "1e6", "", [], 2**64, "9" * 21]:
            with self.subTest(value=value), self.assertRaises(InputError):
                timestamp(value, "test")

    def test_uint64_exactness(self):
        self.assertEqual(timestamp(str(2**64 - 1), "test"), 2**64 - 1)

    def test_duplicate_keys(self):
        self.write('{"_BOOT_ID":"a","_BOOT_ID":"b"}')
        with self.assertRaisesRegex(InputError, "duplicate JSON key"):
            load_entries([self.path])

    def test_invalid_utf8(self):
        self.write(b"\xff\n")
        with self.assertRaises(InputError):
            load_entries([self.path])

    def test_array_is_not_a_record(self):
        with self.assertRaises(InputError):
            self.load([[record()]])

    def test_empty_input(self):
        self.write("\n")
        with self.assertRaisesRegex(InputError, "no journal entries"):
            load_entries([self.path])

    def test_record_limit(self):
        with self.assertRaisesRegex(InputError, "record limit"):
            self.load([record(), record()], max_records=1)

    def test_byte_limit(self):
        with self.assertRaisesRegex(InputError, "total byte"):
            self.load([record()], max_bytes=10)

    def test_line_limit(self):
        with self.assertRaisesRegex(InputError, "line exceeds"):
            self.load([record()], max_line_bytes=10)

    def test_multiple_files_keep_locations(self):
        self.write(json.dumps(record()))
        other = self.path.with_name("other.jsonl")
        other.write_text(json.dumps(record(mono=2_000_000, wall=102_000_000)), encoding="utf-8")
        entries = load_entries([self.path, other])
        self.assertEqual([e.ordinal for e in entries], [0, 1])
        self.assertEqual(entries[1].file, str(other))

    def test_invalid_json_does_not_echo_secret(self):
        self.write('{"MESSAGE": "private-example"')
        with self.assertRaises(InputError) as caught:
            load_entries([self.path])
        self.assertNotIn("private-example", str(caught.exception))


class ArithmeticTests(unittest.TestCase):
    def entry(self, mono, wall, ordinal=0, machine=MACHINE, boot=BOOT):
        return Entry(machine, boot, wall, mono, "input", ordinal + 1, ordinal)

    def test_forward_and_backward_discontinuities(self):
        report = analyze(
            [self.entry(1, 101), self.entry(2, 122, 1), self.entry(3, 83, 2)], threshold_us=10
        )
        changes = report["groups"][0]["discontinuities"]
        self.assertEqual([c["offset_change_us"] for c in changes], [20, -40])
        self.assertEqual(report["discontinuity_count"], 2)
        self.assertEqual(len(report["groups"][0]["segments"]), 3)

    def test_monotonic_sort_overrides_wall_sort(self):
        report = analyze([self.entry(2, 50), self.entry(1, 100, 1)], threshold_us=1, timeline=True)
        group = report["groups"][0]
        self.assertTrue(group["input_order_changed"])
        self.assertEqual([e["monotonic_us"] for e in group["timeline"]], [1, 2])
        self.assertEqual(group["discontinuities"][0]["wall_elapsed_us"], -50)

    def test_never_compare_different_boots(self):
        report = analyze(
            [self.entry(1, 101), self.entry(2, 99999, 1, boot="c" * 32)], threshold_us=1
        )
        self.assertEqual(len(report["groups"]), 2)
        self.assertEqual(report["discontinuity_count"], 0)

    def test_machine_boundary(self):
        report = analyze(
            [self.entry(1, 101), self.entry(2, 99999, 1, machine=None)], threshold_us=1
        )
        self.assertEqual(report["discontinuity_count"], 0)

    def test_threshold_is_inclusive(self):
        entries = [self.entry(1, 101), self.entry(2, 112, 1)]
        self.assertEqual(analyze(entries, threshold_us=10)["discontinuity_count"], 1)
        self.assertEqual(analyze(entries, threshold_us=11)["discontinuity_count"], 0)

    def test_ties_are_retained_and_disclosed(self):
        report = analyze(
            [self.entry(1, 100), self.entry(1, 120, 1)], threshold_us=10, timeline=True
        )
        group = report["groups"][0]
        self.assertEqual(group["equal_monotonic_pairs"], 1)
        self.assertTrue(group["discontinuities"][0]["equal_monotonic_timestamp"])
        self.assertEqual(len(group["timeline"]), 2)

    def test_subthreshold_drift_is_visible_in_segment_range(self):
        group = analyze([self.entry(i, 100 + i * 2, i) for i in range(10)], threshold_us=2)[
            "groups"
        ][0]
        self.assertEqual(group["discontinuities"], [])
        self.assertEqual(group["segments"][0]["max_offset_us"], 109)

    def test_randomized_offset_identity(self):
        rng = random.Random(417)
        for _ in range(200):
            offsets = [rng.randrange(10**15, 10**15 + 1000) for _ in range(20)]
            entries = [self.entry(i * 123, offset + i * 123, i) for i, offset in enumerate(offsets)]
            group = analyze(entries, threshold_us=1)["groups"][0]
            expected = [b - a for a, b in zip(offsets, offsets[1:]) if b != a]
            self.assertEqual([c["offset_change_us"] for c in group["discontinuities"]], expected)

    def test_negative_offset_allowed(self):
        group = analyze([self.entry(10, 1)])["groups"][0]
        self.assertEqual(group["segments"][0]["min_offset_us"], -9)


class CLITests(unittest.TestCase):
    def test_installed_cli_end_to_end(self):
        fixture = Path(__file__).resolve().parents[1] / "examples" / "clock-change.jsonl"
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-m",
                "journal_offset_map",
                str(fixture),
                "--json",
                "--timeline",
                "--fail-on-change",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["discontinuity_count"], 1)
        self.assertEqual(report["groups"][0]["discontinuities"][0]["offset_change_us"], 20_000_000)
        self.assertNotIn("MESSAGE", result.stdout)

    def test_cli_bad_input_exit_two(self):
        result = subprocess.run(
            [sys.executable, "-I", "-m", "journal_offset_map", "missing.jsonl"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)

    def test_cli_bad_threshold(self):
        result = subprocess.run(
            [sys.executable, "-I", "-m", "journal_offset_map", "x", "--threshold-us", "0"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
