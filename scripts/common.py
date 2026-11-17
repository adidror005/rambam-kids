"""Shared, dependency-free utilities for incremental Torah chapter publishing."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"
BOOKS = DATA / "books"
FIELDS = ("original_hebrew", "kids_hebrew", "kids_english")


def load_json(path: Path, default=None):
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def slugify(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")


def parse_chapter_ref(ref):
    match = re.fullmatch(r"(.+?)\s+(\d+)", ref.strip())
    if not match:
        raise ValueError(f"Expected a chapter ref ending in a chapter number, got: {ref!r}")
    return match.group(1), int(match.group(2))


def chapter_path(slug, chapter):
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError(f"Invalid slug: {slug!r}")
    return BOOKS / slug / f"{int(chapter)}.json"


def merge_chapter(incoming, slug=None, he_title=None, overwrite_original=False):
    if not isinstance(incoming.get("segments"), dict):
        raise ValueError("Input must have a 'segments' object")
    book_title, chapter = parse_chapter_ref(incoming["ref"])
    slug = slug or slugify(book_title)
    path = chapter_path(slug, chapter)
    existing = load_json(path)
    if existing and existing.get("ref") != incoming["ref"]:
        raise ValueError(f"Chapter reference mismatch: {existing.get('ref')} vs {incoming['ref']}")
    doc = {
        "book_id": slug,
        "book_title": book_title,
        "hebrew_title": he_title or incoming.get("hebrew_title") or existing.get("hebrew_title") or "",
        "chapter": chapter,
        "ref": incoming["ref"],
        "version_title": incoming.get("version_title") or existing.get("version_title") or "",
        "segments": existing.get("segments", {}),
    }
    # Keep a successful download checkpoint when translations are imported later.
    # An explicit replacement of the original text invalidates that checkpoint.
    source_download = incoming.get("source_download")
    if source_download is None and not overwrite_original:
        source_download = existing.get("source_download")
    if source_download:
        doc["source_download"] = source_download
    for key, seg in incoming["segments"].items():
        number = str(int(key))
        if not isinstance(seg, dict):
            raise ValueError(f"Segment {key} must be an object")
        old = doc["segments"].get(number, {})
        merged = dict(old)
        merged.setdefault("ref", f"{incoming['ref']}:{number}")
        for field, value in seg.items():
            if field == "original_hebrew" and old.get(field) and not overwrite_original:
                continue
            # Empty values never erase previously downloaded or translated text.
            if value is not None and value != "":
                merged[field] = value
        doc["segments"][number] = merged
    doc["segments"] = dict(sorted(doc["segments"].items(), key=lambda it: int(it[0])))
    write_json(path, doc)
    rebuild_manifest()
    return path, doc


def rebuild_manifest():
    books = {}
    for path in sorted(BOOKS.glob("*/*.json")):
        doc = load_json(path)
        slug = doc.get("book_id", path.parent.name)
        if slug not in books:
            books[slug] = {
                "id": slug,
                "title": doc.get("book_title", slug),
                "he_title": doc.get("hebrew_title", ""),
                "chapters": [],
            }
        if doc.get("hebrew_title"):
            books[slug]["he_title"] = doc["hebrew_title"]
        segs = list(doc.get("segments", {}).values())
        counts = {field: sum(bool(seg.get(field)) for seg in segs) for field in FIELDS}
        books[slug]["chapters"].append({
            "number": doc["chapter"],
            "ref": doc["ref"],
            "path": f"data/books/{slug}/{doc['chapter']}.json",
            "segments": len(segs),
            "counts": counts,
        })
    result = {"schema_version": 1, "books": sorted(books.values(), key=lambda b: b["title"])}
    for book in result["books"]:
        book["chapters"].sort(key=lambda c: c["number"])
    write_json(DATA / "manifest.json", result)
    return result
