"""Merge any already-produced chapter JSON into the site without deleting other segments."""
import argparse
from pathlib import Path
from common import load_json, merge_chapter


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", type=Path, help="JSON matching {ref, version_title, segments}")
    p.add_argument("--slug", help="Stable book identifier; auto-derived if omitted")
    p.add_argument("--he-title", default=None, help="Hebrew title for the book")
    p.add_argument("--overwrite-original", action="store_true", help="Replace already-stored original Hebrew text")
    args = p.parse_args()
    path, doc = merge_chapter(load_json(args.input), args.slug, args.he_title, args.overwrite_original)
    print(f"Merged {len(doc['segments'])} numbered segments into {path}")
    print("Manifest rebuilt. You can commit and publish immediately.")


if __name__ == "__main__":
    main()
