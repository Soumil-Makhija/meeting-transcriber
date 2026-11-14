"""Validate model selections against transcript evidence and render review notes."""

from __future__ import annotations

from dataclasses import asdict
from html import escape
import re
from typing import Any

from .transcript import Transcript, format_timestamp


MAX_ITEMS_PER_SECTION = 12
MAX_QUOTE_LENGTH = 2000
REVIEW_NOTICE = (
    "Draft notes require human review. Quotes establish text provenance, not that the "
    "model correctly classified a decision or action. Check the original recording "
    "before relying on names, dates, or uncertain transcript passages."
)


def _normalize(text: str) -> str:
    return " ".join(text.split())


def validate_notes(raw: dict[str, Any], transcript: Transcript) -> dict[str, Any]:
    """Reject unsupported model output instead of quietly changing its claims.

    Notes are extractive: every item is a quote from exactly one segment. An owner
    or deadline is accepted only when that literal text occurs in its quote. This
    catches invented fields but does not prove their semantic relationship; the
    artifact remains explicitly marked for review.
    """
    sections = {"recap", "decisions", "actions"}
    if not isinstance(raw, dict) or set(raw) != sections:
        raise ValueError("Notes must contain exactly recap, decisions, and actions.")
    segments = {segment.id: segment for segment in transcript.segments}
    validated: dict[str, list[dict[str, Any]]] = {}
    for name in ("recap", "decisions", "actions"):
        entries = raw[name]
        if not isinstance(entries, list):
            raise ValueError(f"{name} must be an array.")
        if len(entries) > MAX_ITEMS_PER_SECTION:
            raise ValueError(f"{name} exceeds the limit of {MAX_ITEMS_PER_SECTION} items.")
        if name == "recap" and not entries:
            raise ValueError("recap must include at least one evidence quote.")
        expected = {"segment_id", "quote"}
        if name == "actions":
            expected |= {"owner", "deadline"}
        validated[name] = []
        seen: set[tuple[str, str]] = set()
        for index, entry in enumerate(entries, 1):
            context = f"{name}[{index}]"
            if not isinstance(entry, dict) or set(entry) != expected:
                raise ValueError(f"{context} must contain exactly {', '.join(sorted(expected))}.")
            segment_id = entry["segment_id"]
            if not isinstance(segment_id, str) or segment_id not in segments:
                raise ValueError(f"{context} references an unknown segment_id.")
            original_quote = entry["quote"]
            if not isinstance(original_quote, str):
                raise ValueError(f"{context}.quote must be text.")
            quote = _normalize(original_quote)
            if not quote or len(quote) > MAX_QUOTE_LENGTH:
                raise ValueError(f"{context}.quote must contain 1 to {MAX_QUOTE_LENGTH} characters.")
            segment = segments[segment_id]
            if quote not in _normalize(segment.text):
                raise ValueError(f"{context}.quote does not match the cited transcript segment.")
            identity = (segment_id, quote)
            if identity in seen:
                raise ValueError(f"{context} repeats an existing evidence quote.")
            seen.add(identity)
            item: dict[str, Any] = {"segment_id": segment_id, "quote": quote}
            if name == "actions":
                for field in ("owner", "deadline"):
                    value = entry[field]
                    if value is not None:
                        if not isinstance(value, str) or not _normalize(value):
                            raise ValueError(f"{context}.{field} must be nonempty text or null.")
                        value = _normalize(value)
                        if value not in quote:
                            raise ValueError(f"{context}.{field} must occur literally in the action quote.")
                    item[field] = value
            item.update(start=segment.start, end=segment.end, speaker=segment.speaker,
                        uncertain=segment.uncertain)
            validated[name].append(item)
    return {
        "schema_version": "minutemark/v1",
        "status": "needs_review",
        "notice": REVIEW_NOTICE,
        "source": transcript.source,
        "transcript": [asdict(segment) for segment in transcript.segments],
        **validated,
    }


def _markdown(text: str) -> str:
    # Escape HTML first, then Markdown controls, so transcript content cannot
    # turn into active HTML, links, headings, or formatting in exported notes.
    plain = escape(_normalize(str(text)), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|>])", r"\\\1", plain)


def _citation(item: dict[str, Any]) -> str:
    start = format_timestamp(item["start"])
    end = format_timestamp(item["end"])
    uncertain = " — UNCERTAIN TRANSCRIPT" if item["uncertain"] else ""
    speaker = f" · {_markdown(item['speaker'])}" if item["speaker"] else ""
    return f"[{start}–{end} · {_markdown(item['segment_id'])}{speaker}{uncertain}]"


def render_markdown(artifact: dict[str, Any]) -> str:
    """Render a validated artifact as an evidence-first Markdown document."""
    lines = ["# MinuteMark meeting notes", "", f"**Review required.** {REVIEW_NOTICE}", "",
             f"Source: {_markdown(artifact['source'])}", ""]
    for field, title in (("recap", "Recap"), ("decisions", "Decisions"),
                         ("actions", "Action items")):
        lines.extend([f"## {title}", ""])
        if not artifact[field]:
            lines.extend(["No supported items selected.", ""])
            continue
        for item in artifact[field]:
            lines.append(f"- {_citation(item)} {_markdown(item['quote'])}")
            if field == "actions":
                owner = _markdown(item["owner"]) if item["owner"] else "Unspecified"
                deadline = _markdown(item["deadline"]) if item["deadline"] else "Unspecified"
                lines.append(f"  Owner: {owner}; deadline: {deadline}.")
        lines.append("")
    lines.extend(["## Transcript", ""])
    for segment in artifact["transcript"]:
        item = dict(segment, segment_id=segment["id"])
        lines.append(f"- {_citation(item)} {_markdown(segment['text'])}")
    lines.append("")
    return "\n".join(lines)
