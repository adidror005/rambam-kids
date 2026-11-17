"""Export JSONL work queues of missing kid-friendly translations (one row per field)."""
import argparse
import json
from pathlib import Path

from common import BOOKS, load_json


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--all", action="store_true", help="Include every downloaded book/chapter")
    p.add_argument("--slug", default=None, help="Book ID (default: mishneh-torah-slaves unless --all)")
    p.add_argument("--chapter", type=int, help="Filter to one chapter (not with --all)")
    p.add_argument("--limit", type=int, help="Maximum missing translations to include (for prototyping)")
    p.add_argument("--output", type=Path, default=Path("work/missing.jsonl"))
    args = p.parse_args(argv)
    if args.all and (args.slug or args.chapter):
        p.error("--all cannot be combined with --slug or --chapter")
    if args.limit is not None and args.limit < 1:
        p.error("--limit must be at least 1")
    if args.all:
        paths = sorted(BOOKS.glob("*/*.json"))
    else:
        slug = args.slug or "mishneh-torah-slaves"
        paths = ([BOOKS / slug / f"{args.chapter}.json"] if args.chapter else
                 sorted((BOOKS / slug).glob("*.json")))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with args.output.open("w", encoding="utf-8") as output:
        for path in paths:
            if not path.exists():
                continue
            doc = load_json(path)
            for index, seg in doc.get("segments", {}).items():
                if not seg.get("original_hebrew"):
                    continue  # No LLM prompt without Hebrew source.
                for field in ("kids_hebrew", "kids_english"):
                    if seg.get(field):
                        continue
                    row = {
                        "chapter_ref": doc["ref"],
                        "ref": seg.get("ref", f"{doc['ref']}:{index}"),
                        "segment": index,
                        "field": field,
                        "original_hebrew": seg["original_hebrew"],
                    }
                    output.write(json.dumps(row, ensure_ascii=False) + "\n")
                    count += 1
                    if args.limit is not None and count >= args.limit:
                        break
                if args.limit is not None and count >= args.limit:
                    break
            if args.limit is not None and count >= args.limit:
                break
    print(f"Exported {count} missing translations to {args.output}")
    print("Generate any subset, then merge back with import_chapter.py using the same chapter ref.")


if __name__ == "__main__":
    main()
