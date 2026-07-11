"""Create a minimal de-identified fixture from a related local XHS post/comment pair."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DROP_KEYS = {"nickname", "avatar", "ip_location", "xsec_token", "cookie", "cookies", "device_id", "device_fingerprint"}
PSEUDONYM_KEYS = {"note_id", "comment_id", "id", "user_id", "parent_comment_id"}
TEXT_KEYS = {"title", "desc", "description", "content", "content_text", "comment_text", "text"}


def _read_rows(path: Path) -> list[dict[str, Any]]:
    return [value for line in path.read_text(encoding="utf-8").splitlines() if line.strip() for value in [json.loads(line)] if isinstance(value, dict)]


def select_related_records(contents_path: Path, comments_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    contents_by_id = {str(row.get("note_id") or row.get("content_id") or ""): row for row in _read_rows(contents_path)}
    for comment in _read_rows(comments_path):
        note_id = str(comment.get("note_id") or comment.get("content_id") or "")
        if note_id and note_id in contents_by_id:
            return contents_by_id[note_id], comment
    raise ValueError("No comment has a matching content note_id; refusing to fabricate a relationship")


def _placeholder_media(key: str, value: Any) -> Any:
    if isinstance(value, list):
        return [_placeholder_media(key, item) for item in value]
    if isinstance(value, dict):
        return {child_key: _placeholder_media(child_key, child_value) for child_key, child_value in value.items()}
    return f"https://example.test/media/{key}" if value not in {None, ""} else value


def _sanitize(value: Any, mappings: dict[str, dict[str, str]], key: str = "") -> Any:
    if isinstance(value, list):
        return [_sanitize(item, mappings, key) for item in value]
    if not isinstance(value, dict):
        if key in TEXT_KEYS and value not in {None, ""}:
            return f"synthetic fixture {key}"
        return value
    clean: dict[str, Any] = {}
    for child_key, item in value.items():
        lowered = child_key.lower()
        if child_key in DROP_KEYS or lowered.startswith("xsec_") or "cookie" in lowered or "device" in lowered or "ip_location" in lowered:
            continue
        if "url" in lowered or any(marker in lowered for marker in ("image", "video", "picture", "avatar", "cover")):
            clean[child_key] = _placeholder_media(child_key, item)
        elif "tag" in lowered and item not in {None, ""}:
            clean[child_key] = "synthetic-fixture-tag"
        elif child_key in PSEUDONYM_KEYS and item not in {None, "", "0", 0}:
            table = mappings.setdefault(child_key, {})
            table.setdefault(str(item), f"{child_key}_fixture_{len(table) + 1}")
            clean[child_key] = table[str(item)]
        elif child_key in TEXT_KEYS and item not in {None, ""}:
            clean[child_key] = f"synthetic fixture {child_key}"
        else:
            clean[child_key] = _sanitize(item, mappings, child_key)
    return clean


def sanitize_related_pair(contents_path: Path, comments_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    content, comment = select_related_records(contents_path, comments_path)
    mappings: dict[str, dict[str, str]] = {}
    clean_content = _sanitize(content, mappings)
    clean_comment = _sanitize(comment, mappings)
    if clean_content.get("note_id") != clean_comment.get("note_id"):
        raise ValueError("Pseudonym mapping did not preserve the verified note relationship")
    return clean_content, clean_comment


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contents", type=Path, required=True)
    parser.add_argument("--comments", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    content, comment = sanitize_related_pair(args.contents, args.comments)
    (args.output_dir / "real_capture_sanitized_contents.jsonl").write_text(json.dumps(content, ensure_ascii=False) + "\n", encoding="utf-8")
    (args.output_dir / "real_capture_sanitized_comments.jsonl").write_text(json.dumps(comment, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
