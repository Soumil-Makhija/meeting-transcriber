"""Evidence validation and safe export behavior."""

from copy import deepcopy
import unittest

from minutemark.notes import render_markdown, validate_notes
from minutemark.transcript import Segment, Transcript


class NotesTests(unittest.TestCase):
    def setUp(self):
        self.transcript = Transcript([
            Segment("s1", 0, 5, "We agreed to launch the pilot.", "Morgan"),
            Segment("s2", 5, 12, "Alice will send the draft by Friday.", "Alice"),
            Segment("s3", 12, 20, "We need to check the budget.", uncertain=True),
        ], source="meeting.vtt")
        self.raw = {
            "recap": [{"segment_id": "s1", "quote": "We agreed to launch the pilot."}],
            "decisions": [{"segment_id": "s1", "quote": "We agreed to launch the pilot."}],
            "actions": [{"segment_id": "s2", "quote": "Alice will send the draft by Friday.",
                         "owner": "Alice", "deadline": "Friday"},
                        {"segment_id": "s3", "quote": "We need to check the budget.",
                         "owner": None, "deadline": None}],
        }

    def test_valid_artifact_keeps_timestamps_and_review_status(self):
        artifact = validate_notes(self.raw, self.transcript)
        self.assertEqual(artifact["schema_version"], "minutemark/v1")
        self.assertEqual(artifact["status"], "needs_review")
        self.assertEqual(artifact["actions"][0]["start"], 5)
        self.assertIsNone(artifact["actions"][1]["owner"])
        self.assertTrue(artifact["actions"][1]["uncertain"])
        self.assertEqual(artifact["transcript"][0]["id"], "s1")

    def test_unknown_segment_rejected(self):
        self.raw["recap"][0]["segment_id"] = "s999"
        with self.assertRaisesRegex(ValueError, "unknown segment_id"):
            validate_notes(self.raw, self.transcript)

    def test_invented_quote_and_fields_rejected(self):
        self.raw["actions"][0]["quote"] = "Alice will publish the final report."
        with self.assertRaisesRegex(ValueError, "does not match"):
            validate_notes(self.raw, self.transcript)
        self.raw["actions"][0]["quote"] = "Alice will send the draft by Friday."
        self.raw["actions"][0]["owner"] = "Bob"
        with self.assertRaisesRegex(ValueError, "owner must occur literally"):
            validate_notes(self.raw, self.transcript)
        self.raw["actions"][0]["owner"] = "Alice"
        self.raw["actions"][0]["deadline"] = "Monday"
        with self.assertRaisesRegex(ValueError, "deadline must occur literally"):
            validate_notes(self.raw, self.transcript)

    def test_unknown_fields_are_not_silently_discarded(self):
        variants = [dict(self.raw, title="unexpected"), deepcopy(self.raw)]
        variants[1]["recap"][0]["summary"] = "unsupported claim"
        for raw in variants:
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "exactly"):
                validate_notes(raw, self.transcript)

    def test_wrong_types_and_empty_recap_rejected(self):
        variants = [None, [], dict(self.raw, recap=[]), dict(self.raw, recap="summary")]
        for owner in (5, False, "", "   "):
            variant = deepcopy(self.raw)
            variant["actions"][0]["owner"] = owner
            variants.append(variant)
        variant = deepcopy(self.raw)
        variant["recap"][0]["quote"] = None
        variants.append(variant)
        for raw in variants:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_notes(raw, self.transcript)

    def test_whitespace_normalization_and_no_input_mutation(self):
        raw = deepcopy(self.raw)
        raw["recap"][0]["quote"] = "We agreed\n to launch the pilot."
        original = deepcopy(raw)
        artifact = validate_notes(raw, self.transcript)
        self.assertEqual(artifact["recap"][0]["quote"], "We agreed to launch the pilot.")
        self.assertEqual(raw, original)

    def test_duplicate_and_excessive_selections_rejected(self):
        self.raw["recap"] *= 2
        with self.assertRaisesRegex(ValueError, "repeats"):
            validate_notes(self.raw, self.transcript)
        self.raw["recap"] *= 20
        with self.assertRaisesRegex(ValueError, "limit"):
            validate_notes(self.raw, self.transcript)

    def test_markdown_renders_citations_unknown_fields_and_uncertainty(self):
        markdown = render_markdown(validate_notes(self.raw, self.transcript))
        self.assertIn("00:00:05.000–00:00:12.000", markdown)
        self.assertIn("Owner: Alice; deadline: Friday.", markdown)
        self.assertIn("Owner: Unspecified; deadline: Unspecified.", markdown)
        self.assertIn("UNCERTAIN TRANSCRIPT", markdown)
        self.assertIn("## Transcript", markdown)
        self.assertIn("Draft notes require human review", markdown)

    def test_markdown_escapes_untrusted_html_and_links(self):
        text = '<script>alert(1)</script> [click](https://example.test) **bold** | table'
        transcript = Transcript([Segment("s1", 0, 1, text, "<img>")], source="[source](bad)")
        raw = {"recap": [{"segment_id": "s1", "quote": text}], "decisions": [], "actions": []}
        markdown = render_markdown(validate_notes(raw, transcript))
        self.assertNotIn("<script>", markdown)
        self.assertNotIn("<img>", markdown)
        self.assertNotIn("[click](", markdown)
        self.assertIn(r"\[click\]\(", markdown)
        self.assertIn("No supported items selected.", markdown)


if __name__ == "__main__":
    unittest.main()
