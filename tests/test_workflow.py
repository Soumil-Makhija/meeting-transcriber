from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from minutemark.cli import main, save_outputs
from minutemark.model import generate, local_endpoint, NoRedirect
from minutemark.transcript import Segment, Transcript


class Response(io.BytesIO):
    pass


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.transcript = Transcript([Segment("s1", 0, 5, "We agreed to use SQLite.")], "synthetic.json")
        self.raw = {"recap": [{"segment_id": "s1", "quote": "We agreed to use SQLite."}],
                    "decisions": [{"segment_id": "s1", "quote": "We agreed to use SQLite."}], "actions": []}

    def response(self, **changes):
        result = {"done": True, "model": "test-local", "message": {"content": json.dumps(self.raw)}}
        result.update(changes)
        return Response(json.dumps(result).encode())

    def test_provider_payload_and_parsing(self):
        with patch("minutemark.model.build_opener") as opener:
            opener.return_value.open.return_value = self.response()
            raw, metadata = generate(self.transcript)
            request = opener.return_value.open.call_args.args[0]
            payload = json.loads(request.data)
            self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
            self.assertIs(payload["stream"], False)
            self.assertIn("transcript_untrusted", payload["messages"][1]["content"])
            self.assertEqual(raw, self.raw)
            self.assertEqual(metadata["model"], "test-local")

    def test_incomplete_malformed_and_oversized_provider_replies(self):
        replies = [self.response(done=False), self.response(done_reason="length"),
                   self.response(message={}), self.response(message={"content": "[]"}),
                   self.response(message={"content": "broken"}), Response(b"not json"),
                   Response(b"x" * 262145)]
        for reply in replies:
            with self.subTest(reply=reply), patch("minutemark.model.build_opener") as opener:
                opener.return_value.open.return_value = reply
                with self.assertRaises(ValueError):
                    generate(self.transcript)

    def test_provider_errors_are_actionable(self):
        for error in [URLError("unavailable"), TimeoutError(), HTTPError("local", 404, "missing", {}, None)]:
            with self.subTest(error=error), patch("minutemark.model.build_opener") as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaisesRegex(ValueError, "Local model"):
                    generate(self.transcript)

    def test_loopback_only_and_redirects_refused(self):
        self.assertEqual(local_endpoint("http://[::1]:11434/"), "http://[::1]:11434/api/chat")
        for url in ["https://127.0.0.1", "http://example.test", "http://localhost:11434", "http://127.0.0.1/x",
                    "http://u:p@127.0.0.1", "http://127.0.0.1?x=1", "http://127.0.0.1:bad", "http://127.0.0.1:0"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                local_endpoint(url)
        with self.assertRaisesRegex(ValueError, "redirects"):
            NoRedirect().redirect_request(None, None, 302, "", {}, "http://example.test")

    def test_provider_input_bounds_before_network(self):
        for kwargs in [{"timeout": 0}, {"timeout": float("nan")}, {"timeout": 181}, {"model": ""}]:
            with self.subTest(kwargs=kwargs), patch("minutemark.model.build_opener") as opener:
                with self.assertRaises(ValueError):
                    generate(self.transcript, **kwargs)
                opener.assert_not_called()
        large = Transcript([Segment("s1", 0, 1, "x" * 16001)])
        with self.assertRaisesRegex(ValueError, "split this meeting"):
            generate(large)
        unicode_heavy = Transcript([Segment("s1", 0, 1, "会議" * 2000)])
        with patch("minutemark.model.build_opener") as opener:
            with self.assertRaisesRegex(ValueError, "prompt budget"):
                generate(unicode_heavy)
            opener.assert_not_called()

    def test_cli_real_import_and_output_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "meeting.json"
            source.write_text(json.dumps({"segments": [{"start": 0, "end": 5, "text": "We agreed to use SQLite."}]}))
            md, js = root / "notes.md", root / "notes.json"
            with patch("minutemark.cli.generate", return_value=(self.raw, {"model": "fixture"})), redirect_stdout(io.StringIO()):
                self.assertEqual(main(["notes", str(source), "--output", str(md), "--json-output", str(js)]), 0)
            self.assertIn("00:00:00.000", md.read_text())
            self.assertEqual(json.loads(js.read_text())["status"], "needs_review")
            with patch("minutemark.cli.generate") as generate_mock, redirect_stderr(io.StringIO()):
                self.assertEqual(main(["notes", str(source), "--output", str(md)]), 2)
                generate_mock.assert_not_called()
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(["inspect", str(source)]), 0)
            self.assertEqual(json.loads(out.getvalue())["segments"], 1)

    def test_invalid_generation_creates_no_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "meeting.json"
            source.write_text('[{"start":0,"end":1,"text":"Actual text"}]')
            output = root / "notes.md"
            with patch("minutemark.cli.generate", return_value=(self.raw, {})), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["notes", str(source), "--output", str(output)]), 2)
            self.assertFalse(output.exists())

    def test_save_does_not_clobber_and_rolls_back_partial_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = root / "one", root / "two"
            second.write_text("original")
            with self.assertRaises(ValueError):
                save_outputs({first: "new", second: "replace"})
            self.assertFalse(first.exists())
            self.assertEqual(second.read_text(), "original")
            second.unlink()
            real_link = __import__("os").link
            def fail_second(src, dest):
                if dest == second:
                    raise OSError("simulated second publication failure")
                real_link(src, dest)
            with patch("minutemark.cli.os.link", side_effect=fail_second), self.assertRaises(OSError):
                save_outputs({first: "a", second: "b"})
            self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
