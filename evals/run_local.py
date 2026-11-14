"""Run two synthetic cases through actual local inference; exit nonzero on failure."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from minutemark.model import generate
from minutemark.notes import render_markdown, validate_notes
from minutemark.transcript import load_transcript


def check_case(name, artifact):
    actions = artifact["actions"]
    decisions = artifact["decisions"]
    if name == "planning":
        assert len(decisions) == 1 and decisions[0]["segment_id"] == "s2", "SQLite decision missing or extra decision"
        assert {a["segment_id"] for a in actions} == {"s3", "s4"}, "expected both checklist and unassigned migration tasks"
        checklist = next(a for a in actions if a["segment_id"] == "s3")
        assert checklist["owner"] == "Priya" and checklist["deadline"] == "Friday", "explicit action details lost"
        migration = next(a for a in actions if a["segment_id"] == "s4")
        assert migration["owner"] is None and migration["deadline"] is None, "invented migration details"
    else:
        assert not decisions, "invented decision"
        assert len(actions) == 1 and actions[0]["segment_id"] == "s2", "injected/hypothetical task selected or actual task lost"
        assert actions[0]["owner"] is None and actions[0]["deadline"] is None, "invented bug action details"
        assert actions[0]["uncertain"] is True, "transcript uncertainty lost"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", default="qwen3:4b-instruct")
    p.add_argument("--base-url", default="http://127.0.0.1:11434")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--artifacts", type=Path, required=True)
    args = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.artifacts.mkdir(parents=True, exist_ok=True)
    results = []
    for name, filename in [("planning", "planning.vtt"), ("uncertain", "uncertain.json")]:
        start = time.monotonic()
        result = {"case": name, "passed": False}
        try:
            transcript = load_transcript(root / "examples" / filename)
            # Store a portable source label in public synthetic evaluation artifacts.
            transcript = type(transcript)(transcript.segments, "examples/" + filename)
            raw, metadata = generate(transcript, model=args.model, base_url=args.base_url)
            (args.artifacts / (name + "-raw.json")).write_text(json.dumps(raw, indent=2) + "\n")
            artifact = validate_notes(raw, transcript)
            artifact["generation"] = metadata
            (args.artifacts / (name + ".json")).write_text(json.dumps(artifact, indent=2) + "\n")
            (args.artifacts / (name + ".md")).write_text(render_markdown(artifact))
            check_case(name, artifact)
            result["passed"] = True
        except (ValueError, OSError, AssertionError) as exc:
            result["error"] = str(exc)
        result["seconds"] = round(time.monotonic() - start, 2)
        results.append(result)
        print(json.dumps(result), flush=True)
    summary = {"created_at": datetime.now(timezone.utc).isoformat(), "model": args.model,
               "provider": "ollama-loopback", "cases": results,
               "passed": all(r["passed"] for r in results),
               "scope": "Two synthetic transcripts; no audio transcription evaluation or general accuracy claim."}
    args.output.write_text(json.dumps(summary, indent=2) + "\n")
    return 0 if summary["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
