"""Transcript imports preserve evidence while rejecting malformed inputs."""

import json
from pathlib import Path
import tempfile
import unittest

from minutemark.transcript import format_timestamp, load_transcript


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def load(self, content, suffix=".json"):
        path = Path(self.directory.name) / ("meeting" + suffix)
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
        return load_transcript(path)

    def row(self, **changes):
        return {"start": 0, "end": 2, "text": "Alice will send the notes.", **changes}

    def test_json_normalizes_text_and_ignores_supplied_ids(self):
        transcript = self.load({"segments": [self.row(id="unsafe", text="  A\n  decision.  ", speaker=" Alice ", uncertain=True)]})
        segment = transcript.segments[0]
        self.assertEqual((segment.id, segment.text, segment.speaker, segment.uncertain), ("s1", "A decision.", "Alice", True))
        self.assertTrue(transcript.source.endswith("meeting.json"))

    def test_json_list_and_overlapping_speakers(self):
        transcript = self.load([self.row(), self.row(start=1, end=4, speaker="Bob")])
        self.assertEqual([s.id for s in transcript.segments], ["s1", "s2"])
        self.assertEqual(transcript.segments[1].start, 1)

    def test_invalid_segment_fields(self):
        invalid = [
            {"start": True}, {"start": "1"}, {"start": -1}, {"start": float("nan")},
            {"end": float("inf")}, {"end": -1}, {"text": " \n "}, {"text": 1},
            {"speaker": False}, {"speaker": " "}, {"uncertain": "false"}, {"uncertain": 0},
        ]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.load([self.row(**changes)])

    def test_rejects_reverse_end_and_out_of_order_starts(self):
        with self.assertRaisesRegex(ValueError, "ends before"):
            self.load([self.row(start=4, end=3)])
        with self.assertRaisesRegex(ValueError, "chronological"):
            self.load([self.row(start=2, end=3), self.row(start=1)])

    def test_rejects_empty_and_wrong_shape_json(self):
        for value in [[], {}, {"segments": []}, {"segments": "text"}, [None]]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load(value)

    def test_vtt_header_metadata_comment_settings_and_voice(self):
        transcript = self.load(
            "\ufeffWEBVTT - demo\nLanguage: en\n\nNOTE ignored\ncomment\n\n"
            "cue-one\n00:01.000 --> 00:03.500 align:start position:0%\n"
            "<v Alice><b>We agreed</b> &amp; will ship.</v>\n\n"
            "01:02:03.000 --> 01:02:05.000\nNext step.\n", ".vtt")
        self.assertEqual(transcript.segments[0].text, "We agreed & will ship.")
        self.assertEqual(transcript.segments[0].speaker, "Alice")
        self.assertEqual(transcript.segments[1].start, 3723)
        self.assertFalse(transcript.segments[0].uncertain)

    def test_multiple_voices_do_not_invent_one_speaker(self):
        transcript = self.load("WEBVTT\n\n00:00.000 --> 00:02.000\n<v Alice>Yes.</v> <v Bob>No.</v>\n", ".vtt")
        self.assertIsNone(transcript.segments[0].speaker)
        self.assertEqual(transcript.segments[0].text, "Yes. No.")

    def test_srt_crlf_and_multiline(self):
        transcript = self.load("1\r\n00:00:01,250 --> 00:00:04,000\r\nFirst line.\r\nSecond line.\r\n\r\n2\r\n00:01:00,000 --> 00:01:02,000\r\nDone.\r\n", ".srt")
        self.assertEqual(transcript.segments[0].start, 1.25)
        self.assertEqual(transcript.segments[0].text, "First line. Second line.")
        self.assertEqual(transcript.segments[1].start, 60)

    def test_rejects_bad_subtitle_timing_and_empty_cues(self):
        bad = [
            ("WEBVTT\n\n00:61.000 --> 01:02.000\nHello", ".vtt"),
            ("WEBVTT\n\n00:00,000 --> 00:01,000\nHello", ".vtt"),
            ("WEBVTT\n\n00:00.000 -->\nHello", ".vtt"),
            ("WEBVTT\n\n00:00.000 --> 00:01.000\n", ".vtt"),
            ("WEBVTT\n\nbroken cue", ".vtt"),
            ("00:00.000 --> 00:01.000\nHello", ".vtt"),
            ("1\n00:00.000 --> 00:01.000\nHello", ".srt"),
            ("1\n00:00:00.000 --> 00:00:01.000\nHello", ".srt"),
        ]
        for content, suffix in bad:
            with self.subTest(content=content), self.assertRaises(ValueError):
                self.load(content, suffix)

    def test_limits_segment_count_and_text_volume(self):
        with self.assertRaisesRegex(ValueError, "2000 segments"):
            self.load([self.row()] * 2001)
        with self.assertRaisesRegex(ValueError, "100000 text characters"):
            self.load([self.row(text="x" * 100_001)])

    def test_file_byte_limit_precedes_parsing(self):
        with self.assertRaisesRegex(ValueError, "file exceeds"):
            self.load(" " * (2 * 1024 * 1024 + 1))

    def test_rejects_unsupported_extension_and_binary_data(self):
        with self.assertRaisesRegex(ValueError, "supported transcript formats"):
            self.load("Hello", ".txt")
        path = Path(self.directory.name) / "binary.json"
        path.write_bytes(b"\xff\xfe")
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            load_transcript(path)

    def test_format_timestamp_carries_milliseconds_and_rejects_bad_values(self):
        self.assertEqual(format_timestamp(3661.125), "01:01:01.125")
        self.assertEqual(format_timestamp(59.9999), "00:01:00.000")
        self.assertEqual(format_timestamp(0), "00:00:00.000")
        self.assertTrue(format_timestamp(1e308).endswith(".000"))
        for value in [float("inf"), -1, True, "1"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                format_timestamp(value)


if __name__ == "__main__":
    unittest.main()
