# MinuteMark

Turn a timestamped transcript into reviewable meeting notes using a local Ollama model. Import WebVTT, SRT, or JSON; extract recap excerpts, decisions, and action items; export Markdown and optional JSON with the supporting transcript and timestamps.

**Current milestone:** transcript-to-notes CLI. Audio upload and speech-to-text transcription are not implemented. The repository name reflects the larger project; this release does not transcribe recordings. There is no web UI or hosted API integration.

## Run it

Requires Python 3.11+ and, for note generation, a separately installed [Ollama](https://ollama.com/) server with the chosen model already available. The Python package has no runtime dependencies.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .

# Works offline without a model server.
minutemark inspect examples/planning.vtt

# Start your separately installed Ollama server if needed: ollama serve
minutemark notes examples/planning.vtt \
  --model qwen3:4b-instruct \
  --output minutes.md \
  --json-output minutes.json
```

Choose new output filenames each time; existing files and the input transcript are never overwritten. Parent directories must already exist. `--json-output` is optional. `--base-url` defaults to `http://127.0.0.1:11434`; `--timeout` defaults to 120 seconds and must be greater than zero and at most 180 seconds. Errors exit with status 2.

The default model is `qwen3:4b-instruct`. The recorded real-model evaluation passed **1 of 2 cases**: decisions and task quotes were supported, but the planning case lost the explicit owner “Priya” and deadline “Friday” in its structured fields. Those details remain visible in the quoted action. This release is a working review tool with incomplete extraction quality, not a completed transcription application. See [observed results](evals/RESULTS.md).

## Evidence and review

Each note is an extractive quote linked to one transcript segment. Outputs include its start/end timestamps, speaker when available, and an uncertainty marker. Action owners and deadlines are `null`/“Unspecified” unless the model supplies words that occur literally in the same quote. A null field can also mean the model missed a stated detail; it does not prove the source omitted it. Relative deadlines such as “Friday” stay unchanged.

Validation rejects unknown segment IDs, altered quotes, unsupported owner/deadline text, duplicate entries within a section, and malformed output. These lexical checks establish text provenance; they do not prove that a quote was correctly classified as a decision or task, that an owner has the intended relationship, or that all tasks were found. Every generated artifact is marked `needs_review`. Check the source recording before relying on uncertain names or dates.

Timestamps retain millisecond precision and refer to whole subtitle cues, not word-level alignment. VTT/SRT uncertainty is unknown and defaults to false; use JSON's explicit `uncertain` field when that information is available. A false flag is not a confidence score.

## Input formats and limits

WebVTT supports cue IDs/settings, comments, header metadata, and voice tags. SRT supports numbered multiline cues. Subtitle formatting is removed and whitespace is normalized. Overlapping cues are allowed; start times must be nondecreasing. Every import receives stable IDs `s1`, `s2`, and so on.

JSON accepts either a segment list or an object containing `segments`:

```json
{
  "segments": [
    {
      "start": 12.0,
      "end": 19.5,
      "text": "Priya will send the checklist by Friday.",
      "speaker": "Sam",
      "uncertain": true
    }
  ]
}
```

`start` and `end` are finite nonnegative seconds, with `end >= start`. `text` is required; `speaker` is optional text or null; `uncertain` is an optional boolean. Supplied IDs are ignored.

Import/inspection limits are 2 MiB per UTF-8 file, 2,000 segments, and 100,000 normalized text characters. Note generation has narrower upper bounds of 100 segments and 16,000 text characters, plus a **6,000-byte prompt budget including the system prompt, schema, and serialized transcript**. That budget usually limits input before the segment/character ceilings do. Split longer meetings into short transcript files. The tool refuses oversized input rather than silently truncating it.

## Local inference

The provider uses Ollama's [`/api/chat` structured outputs](https://docs.ollama.com/capabilities/structured-outputs) with a JSON schema, temperature 0, and bounded input/output. Only numeric loopback HTTP URLs (`127.0.0.1` or `::1`) are accepted; proxies and redirects are disabled. Transcripts are treated as untrusted data, with no tool execution or external actions. Model prompt adherence still requires evaluation.

The examples contain synthetic meeting text. Generated Markdown/JSON retains transcript contents; review artifacts before publishing them. No private recording, account access, or paid model service is required for this milestone.

## Checks and real-model evaluation

```sh
python -m unittest discover -s tests -v

# Uses actual inference from your running local Ollama server.
mkdir -p /tmp/minutemark-eval
python evals/run_local.py \
  --model qwen3:4b-instruct \
  --base-url http://127.0.0.1:11434 \
  --output /tmp/minutemark-eval/summary.json \
  --artifacts /tmp/minutemark-eval/artifacts
```

The evaluator checks two synthetic transcripts: an explicit decision with assigned/unassigned tasks, and an uncertain task beside an instruction-injection example. It saves raw responses, validated notes, timing, and a pass/fail summary, and exits nonzero if a case fails. This is a small reproducible check, not a general accuracy benchmark or an audio-transcription evaluation. Use a fresh evaluation directory to preserve earlier evidence.

[CI](.github/workflows/ci.yml) installs the package, runs unit/workflow tests on Python 3.11, 3.13, and 3.14, and inspects a sample transcript. It does not run a model; deterministic test doubles are separate from the real-model evaluation above.

## Architecture and next steps

- [transcript.py](src/minutemark/transcript.py): input parsing, validation, and stable segment IDs.
- [model.py](src/minutemark/model.py): bounded loopback Ollama requests and structured-output schema.
- [notes.py](src/minutemark/notes.py): evidence validation and Markdown rendering.
- [cli.py](src/minutemark/cli.py): inspection, generation, and output-file handling.
- [run_local.py](evals/run_local.py): actual local-model evaluation against synthetic references.

Next: validate a speech-to-text provider on an authorized recording with a known reference, preserve transcription uncertainty, evaluate note coverage on more varied meetings, and add a review UI. Those larger requirements remain unfinished.

MIT licensed; see [LICENSE](LICENSE).
