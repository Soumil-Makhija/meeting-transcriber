"""Read timestamped transcript files without a third-party dependency."""

from __future__ import annotations

from dataclasses import dataclass
import html
import json
import math
from pathlib import Path
import re


MAX_SEGMENTS = 2_000
MAX_TEXT_CHARACTERS = 100_000
MAX_FILE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class Segment:
    id: str
    start: float
    end: float
    text: str
    speaker: str | None = None
    uncertain: bool = False


@dataclass(frozen=True)
class Transcript:
    segments: list[Segment]
    source: str = ""


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite, nonnegative number of seconds")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{label} must be a finite, nonnegative number of seconds") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label} must be a finite, nonnegative number of seconds")
    return number


def format_timestamp(seconds: float) -> str:
    """Format a nonnegative number of seconds as ``HH:MM:SS.mmm``."""
    fraction, whole_seconds = math.modf(_number(seconds, "timestamp"))
    # Scale the integer part with integer arithmetic to avoid overflowing a
    # perfectly finite (if unusual) imported timestamp.
    milliseconds = int(whole_seconds) * 1000 + round(fraction * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, fraction = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{fraction:03d}"


def _normal_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a nonempty string")
    text = " ".join(value.split())
    if not text:
        raise ValueError(f"{label} must be a nonempty string")
    return text


def _validate_rows(rows: object, source: str) -> Transcript:
    if not isinstance(rows, list) or not rows:
        raise ValueError("transcript must contain a nonempty segments list")
    if len(rows) > MAX_SEGMENTS:
        raise ValueError(f"transcript exceeds {MAX_SEGMENTS} segments")
    segments: list[Segment] = []
    text_length = 0
    previous_start = -1.0
    for index, row in enumerate(rows, 1):
        label = f"segment {index}"
        if not isinstance(row, dict):
            raise ValueError(f"{label} must be an object")
        start = _number(row.get("start"), f"{label} start")
        end = _number(row.get("end"), f"{label} end")
        if end < start:
            raise ValueError(f"{label} ends before it starts")
        if start < previous_start:
            raise ValueError("segment starts must be in chronological order")
        previous_start = start
        text = _normal_text(row.get("text"), f"{label} text")
        text_length += len(text)
        if text_length > MAX_TEXT_CHARACTERS:
            raise ValueError(f"transcript exceeds {MAX_TEXT_CHARACTERS} text characters")
        speaker = row.get("speaker")
        if speaker is not None:
            speaker = _normal_text(speaker, f"{label} speaker")
        uncertain = row.get("uncertain", False)
        if not isinstance(uncertain, bool):
            raise ValueError(f"{label} uncertain must be a boolean")
        segments.append(Segment(f"s{index}", start, end, text, speaker, uncertain))
    return Transcript(segments, source)


_TIMESTAMP = re.compile(r"(?:(\d{2,}):)?(\d{2}):(\d{2})[.,](\d{3})\Z")


def _parse_timestamp(value: str, *, srt: bool) -> float:
    match = _TIMESTAMP.fullmatch(value)
    if match is None or (srt and (match[1] is None or "," not in value)):
        raise ValueError(f"invalid {'SRT' if srt else 'WebVTT'} timestamp: {value!r}")
    if not srt and "," in value:
        raise ValueError(f"invalid WebVTT timestamp: {value!r}")
    hours = int(match[1] or 0)
    minutes, seconds, milliseconds = (int(match[i]) for i in (2, 3, 4))
    if minutes > 59 or seconds > 59:
        raise ValueError(f"timestamp minutes and seconds must be below 60: {value!r}")
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def _subtitle_rows(content: str, *, srt: bool) -> list[dict]:
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    if not srt:
        lines = content.split("\n")
        if not lines or re.fullmatch(r"WEBVTT(?:[ \t].*)?", lines[0]) is None:
            raise ValueError("WebVTT file must begin with WEBVTT")
        # Header metadata is separated from cue blocks by an empty line.
        header_end = next((i for i, line in enumerate(lines[1:], 1) if not line.strip()), None)
        if header_end is None:
            raise ValueError("WebVTT header must be followed by an empty line and cues")
        content = "\n".join(lines[header_end + 1 :])

    rows: list[dict] = []
    for block in re.split(r"\n[ \t]*\n", content.strip()):
        lines = block.split("\n")
        if not lines or not lines[0].strip():
            continue
        if not srt and (
            re.match(r"^NOTE(?:[ \t]|$)", lines[0])
            or lines[0].strip() in {"STYLE", "REGION"}
        ):
            continue
        timing_index = 0 if "-->" in lines[0] else 1
        if timing_index >= len(lines) or "-->" not in lines[timing_index]:
            raise ValueError(f"subtitle cue {len(rows) + 1} has no timing line")
        before, after = lines[timing_index].split("-->", 1)
        end_and_settings = after.strip().split()
        if not end_and_settings:
            raise ValueError(f"subtitle cue {len(rows) + 1} has no end timestamp")
        start = _parse_timestamp(before.strip(), srt=srt)
        end = _parse_timestamp(end_and_settings[0], srt=srt)
        cue_text = " ".join(lines[timing_index + 1 :])
        voices = re.findall(r"<v(?:\.[^ >]+)?[ \t]+([^>]+)>", cue_text)
        unique_voices = list(dict.fromkeys(voices))
        speaker = unique_voices[0] if len(unique_voices) == 1 else None
        # Remove subtitle markup/timestamp tags, then decode text entities.
        text = html.unescape(re.sub(r"<[^>]*>", "", cue_text))
        rows.append({"start": start, "end": end, "text": text, "speaker": speaker})
        if len(rows) > MAX_SEGMENTS:
            raise ValueError(f"transcript exceeds {MAX_SEGMENTS} segments")
    return rows


def load_transcript(path: str | Path) -> Transcript:
    """Load JSON, WebVTT, or SRT with stable segment IDs and validated timing.

    Overlapping speakers are allowed; segment start times must remain ordered.
    Imported text is untrusted data and is never executed.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in {".json", ".vtt", ".srt"}:
        raise ValueError("supported transcript formats are .json, .vtt, and .srt")
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"transcript file exceeds {MAX_FILE_BYTES} bytes")
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("transcript must use UTF-8 text encoding") from exc
    if suffix == ".json":
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, RecursionError, ValueError) as exc:
            raise ValueError(f"invalid transcript JSON: {exc}") from exc
        rows = parsed.get("segments") if isinstance(parsed, dict) else parsed
    else:
        rows = _subtitle_rows(content, srt=suffix == ".srt")
    return _validate_rows(rows, str(path))
