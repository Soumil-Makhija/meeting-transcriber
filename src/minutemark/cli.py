"""CLI for transcript inspection and local-model meeting notes."""

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import tempfile

from .model import generate
from .notes import render_markdown, validate_notes
from .transcript import format_timestamp, load_transcript


def save_outputs(outputs: dict[Path, str]) -> None:
    """Publish prepared files without replacing any existing file."""
    normalized = [p.resolve() for p in outputs]
    if len(set(normalized)) != len(normalized):
        raise ValueError("Output paths must be different")
    for path in outputs:
        if path.exists() or path.is_symlink():
            raise ValueError(f"Output exists: {path}; choose another filename")
        if not path.parent.is_dir():
            raise ValueError(f"Output directory does not exist: {path.parent}")
    staged, created = [], []
    try:
        for path, content in outputs.items():
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=".minutemark-", delete=False) as f:
                staged.append((Path(f.name), path))
                f.write(content)
        for temporary, path in staged:
            os.link(temporary, path)  # fails atomically if a destination exists
            created.append(path)
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="minutemark", description=__doc__)
    p.add_argument("--version", action="version", version="MinuteMark 0.1.0")
    sub = p.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="Validate a WebVTT, SRT, or JSON transcript")
    inspect.add_argument("transcript", type=Path)
    notes = sub.add_parser("notes", help="Extract cited notes with a local Ollama model")
    notes.add_argument("transcript", type=Path)
    notes.add_argument("--output", type=Path, required=True, help="New Markdown output file")
    notes.add_argument("--json-output", type=Path, help="Optional new structured JSON artifact")
    notes.add_argument("--model", default="qwen3:4b-instruct")
    notes.add_argument("--base-url", default="http://127.0.0.1:11434")
    notes.add_argument("--timeout", type=float, default=120)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        transcript = load_transcript(args.transcript)
        if args.command == "inspect":
            print(json.dumps({"source": transcript.source, "segments": len(transcript.segments),
                              "duration": format_timestamp(max(s.end for s in transcript.segments)),
                              "uncertain_segments": sum(s.uncertain for s in transcript.segments),
                              "transcript": [asdict(s) for s in transcript.segments]}, indent=2))
            return 0
        paths = [args.output] + ([args.json_output] if args.json_output else [])
        if len({p.resolve() for p in paths}) != len(paths):
            raise ValueError("Output paths must be different")
        for p in paths:
            if p.resolve() == args.transcript.resolve() or p.exists() or p.is_symlink():
                raise ValueError(f"Output exists or matches input: {p}; choose another filename")
            if not p.parent.is_dir():
                raise ValueError(f"Output directory does not exist: {p.parent}")
        raw, metadata = generate(transcript, model=args.model, base_url=args.base_url, timeout=args.timeout)
        artifact = validate_notes(raw, transcript)
        artifact["generation"] = metadata
        outputs = {args.output: render_markdown(artifact)}
        if args.json_output:
            outputs[args.json_output] = json.dumps(artifact, ensure_ascii=False, indent=2) + "\n"
        save_outputs(outputs)
        print(f"Saved {args.output} — review quotes and timestamps before sharing.")
        return 0
    except (ValueError, OSError, UnicodeError) as exc:
        print(f"minutemark: {exc}", file=sys.stderr)
        return 2
