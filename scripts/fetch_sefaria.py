"""Download Hebrew chapters from Sefaria, incrementally and without duplicate requests.

Use --all for every Mishneh Torah book/chapter; --book for a single book.
Each chapter is saved as its own JSON file and translated fields are preserved.
"""
import argparse
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from common import ROOT, chapter_path, load_json, merge_chapter, slugify

BASE = "https://www.sefaria.org/api/v3/texts/"
TOC_URL = "https://www.sefaria.org/api/index"
SHAPE_URL = "https://www.sefaria.org/api/shape/"
CATALOG_PATH = ROOT / "work" / "mishneh_torah_catalog.json"
USER_AGENT = "TorahReadingPages/0.2 (educational, incremental downloader)"


def request_json(url, timeout=30, max_retries=3):
    """Retry transient server/rate-limit errors, with conservative backoff."""
    for attempt in range(max_retries + 1):
        try:
            with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout) as response:
                data = json.load(response)
            if isinstance(data, dict) and data.get("error"):
                raise ValueError(f"Sefaria returned an error: {data['error']}")
            return data
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == max_retries:
                raise
            wait = min(20, 2 ** attempt * 2)
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if retry_after and retry_after.isdigit():
                wait = max(wait, min(60, int(retry_after)))
        except (URLError, TimeoutError) as exc:
            if attempt == max_retries:
                raise
            wait = min(20, 2 ** attempt * 2)
        print(f"  Temporary network issue; retrying in {wait}s...", flush=True)
        time.sleep(wait)


def already_downloaded(book, chapter, slug, requested_version):
    """Skip only fully fetched, checkpointed source chapters from the same edition.

    Imported partial chapter files are not automatically trusted as complete.
    """
    path = chapter_path(slug or slugify(book), chapter)
    existing = load_json(path)
    checkpoint = existing.get("source_download") or {}
    segment_ids = checkpoint.get("segment_ids")
    if (
        existing.get("ref") != f"{book} {chapter}"
        or checkpoint.get("requested_version") != requested_version
        or not isinstance(segment_ids, list)
        or not segment_ids
    ):
        return False
    return all(
        isinstance(existing.get("segments", {}).get(str(i), {}).get("original_hebrew"), str)
        and existing["segments"][str(i)]["original_hebrew"].strip()
        for i in segment_ids
    )


def get_chapter(ref, version_title, timeout=30):
    version = f"hebrew|{version_title}" if version_title else "source"
    url = BASE + quote(ref, safe="") + "?" + urlencode({"version": version, "return_format": "text_only"})
    payload = request_json(url, timeout=timeout)
    versions = payload.get("versions", [])
    if not versions:
        raise ValueError(f"No Hebrew version for {ref!r}, version={version!r}. Warnings: {payload.get('warnings', [])}")
    result = versions[0]
    texts = result.get("text")
    if not isinstance(texts, list) or any(isinstance(x, (list, dict)) for x in texts):
        raise ValueError(f"Expected a flat chapter array of segments for {ref}. Got {type(texts).__name__}.")
    segments = {
        str(i): {"ref": f"{ref}:{i}", "original_hebrew": content.strip()}
        for i, content in enumerate(texts, 1)
        if isinstance(content, str) and content.strip()
    }
    if not segments:
        raise ValueError(f"Sefaria returned no Hebrew segments for {ref}; nothing was saved.")
    return {
        "ref": ref,
        "version_title": result.get("versionTitle", version_title or "Sefaria source"),
        "segments": segments,
    }


def list_mishneh_torah_books(toc):
    """Extract ONLY Mishneh Torah books from Sefaria's nested table of contents.

    Prefix matching excludes commentaries such as 'Lechem Mishneh on Mishneh Torah'.
    Preserve Sefaria's reading order as the TOC walks the category tree.
    """
    found = {}

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            title = node.get("title")
            if isinstance(title, str) and title.startswith("Mishneh Torah, "):
                found.setdefault(title, {"title": title, "he_title": node.get("heTitle") or ""})
            for key in ("contents", "children"):
                if key in node:
                    walk(node[key])

    walk(toc)
    if not found:
        raise ValueError("No 'Mishneh Torah, ...' works found in Sefaria's table of contents.")
    return list(found.values())


def chapter_count_from_shape(shape, title):
    """Sefaria shape describes the length (number of chapters) of a simple work."""
    if isinstance(shape, list):
        matches = [s for s in shape if isinstance(s, dict) and (s.get("title") == title or s.get("book") == title)]
        if len(matches) != 1:
            raise ValueError(f"Unexpected Sefaria shape response for {title}")
        shape = matches[0]
    if not isinstance(shape, dict):
        raise ValueError(f"Invalid shape response for {title}")
    length = shape.get("length")
    if not isinstance(length, int) or isinstance(length, bool) or length < 1:
        # For simple texts, the 'chapters' list contains a count of segments per chapter.
        chapters = shape.get("chapters")
        if isinstance(chapters, list) and chapters and all(isinstance(c, int) for c in chapters):
            length = len(chapters)
        else:
            raise ValueError(f"Missing valid chapter count for {title}: {shape!r}")
    return length


def discover_all_books(refresh_catalog=False, delay=0.5):
    """Discover book titles and chapter counts; cache the successful catalog locally."""
    if not refresh_catalog and CATALOG_PATH.exists():
        cached = load_json(CATALOG_PATH)
        books = cached.get("books") if isinstance(cached, dict) else None
        if isinstance(books, list) and books and all(
            isinstance(b, dict) and b.get("title") and isinstance(b.get("chapters"), int)
            and b["chapters"] > 0 for b in books
        ):
            print(f"Using saved book catalog: {CATALOG_PATH} (use --refresh-catalog to rediscover)", flush=True)
            return books
        print("Ignoring invalid cached catalog; requesting a fresh one.", flush=True)

    print("Discovering Mishneh Torah books via Sefaria's table of contents...", flush=True)
    toc = request_json(TOC_URL)
    books = list_mishneh_torah_books(toc)
    print(f"Found {len(books)} works; checking chapter counts...", flush=True)
    errors = []
    for i, book in enumerate(books, 1):
        title = book["title"]
        try:
            shape = request_json(SHAPE_URL + quote(title.replace(" ", "_"), safe=""))
            book["chapters"] = chapter_count_from_shape(shape, title)
            if not book["he_title"] and isinstance(shape, dict):
                book["he_title"] = shape.get("heTitle") or ""
        except (HTTPError, URLError, ValueError, TimeoutError, json.JSONDecodeError) as exc:
            errors.append(f"{title}: {exc}")
        if i < len(books):
            time.sleep(max(0, delay))
    if errors:
        detail = "\n  ".join(errors[:8])
        raise ValueError(
            f"Could not determine chapter counts for {len(errors)} work(s). "
            f"Catalog not saved, so no chapters will be missed silently.\n  {detail}"
        )
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.write_text(
        json.dumps({"schema_version": 1, "books": books}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return books


def chapter_tasks(args, parser):
    """Return (book, chapter_number, slug, he_title) for requested download."""
    if args.all:
        if args.chapters or args.start is not None or args.end is not None:
            parser.error("--all cannot be combined with --chapters, --start or --end")
        if args.book or args.slug or args.he_title:
            parser.error("--all discovers books automatically; omit --book, --slug and --he-title")
        books = discover_all_books(refresh_catalog=args.refresh_catalog, delay=args.delay)
        return [
            (book["title"], ch, None, book.get("he_title", ""))
            for book in books
            for ch in range(1, book["chapters"] + 1)
        ]

    if args.refresh_catalog:
        parser.error("--refresh-catalog requires --all")
    if args.chapters and (args.start is not None or args.end is not None):
        parser.error("Use --chapters OR --start/--end, not both")
    if args.chapters:
        chapters = args.chapters
    elif args.start is not None and args.end is not None and args.start <= args.end:
        chapters = range(args.start, args.end + 1)
    else:
        parser.error("Provide --all, --chapters 4 5, or --start 1 --end 10")
    if any(n < 1 for n in chapters):
        parser.error("Chapters must be positive integers")
    book = args.book or "Mishneh Torah, Slaves"
    return [(book, ch, args.slug, args.he_title) for ch in chapters]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--all", action="store_true", help="Download ALL Mishneh Torah books and chapters")
    p.add_argument("--book", help="Sefaria book title (default: Mishneh Torah, Slaves for manual ranges)")
    p.add_argument("--slug", help="Optional book ID; not used with --all")
    p.add_argument("--he-title", default="", help="Hebrew book title; autodetected with --all")
    p.add_argument("--chapters", type=int, nargs="+", help="Specific chapters, e.g. --chapters 4 5")
    p.add_argument("--start", type=int, help="Start of inclusive chapter range, requires --end")
    p.add_argument("--end", type=int, help="End of inclusive chapter range, requires --start")
    p.add_argument("--version-title", default="Torat Emet 363", help="Exact Sefaria Hebrew version title")
    p.add_argument("--primary", action="store_true", help="Use Sefaria source instead of a named version")
    p.add_argument("--refresh", action="store_true", help="Refetch chapter text even when already downloaded")
    p.add_argument("--refresh-catalog", action="store_true", help="Rediscover book/chapter counts (only for --all)")
    p.add_argument("--limit", type=int, help="Fetch at most N *uncached* chapters this run (useful for batches)")
    p.add_argument("--dry-run", action="store_true", help="Show what would be requested, without fetching texts")
    p.add_argument("--delay", type=float, default=0.5, help="Pause between network requests, in seconds")
    args = p.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        p.error("--limit must be at least 1")
    if args.delay < 0:
        p.error("--delay cannot be negative")
    if args.primary and args.version_title != "Torat Emet 363":
        p.error("Choose either --primary or --version-title, not both")

    try:
        tasks = chapter_tasks(args, p)
    except (HTTPError, URLError, ValueError, TimeoutError, json.JSONDecodeError) as exc:
        p.exit(1, f"Could not discover all chapters: {exc}\n")
    requested_version = "source" if args.primary else args.version_title
    print(f"Selected {len(tasks)} chapters; edition: {requested_version}", flush=True)
    errors = skipped = fetched = planned = 0
    try:
        for book, chapter, slug, he_title in tasks:
            ref = f"{book} {chapter}"
            try:
                if not args.refresh and already_downloaded(book, chapter, slug, requested_version):
                    skipped += 1
                    continue
                if args.limit is not None and (planned if args.dry_run else fetched + errors) >= args.limit:
                    break
                if args.dry_run:
                    print(f"Would download: {ref}")
                    planned += 1
                    continue
                incoming = get_chapter(ref, None if args.primary else args.version_title)
                incoming["source_download"] = {
                    "requested_version": requested_version,
                    "segment_ids": list(incoming["segments"]),
                }
                path, doc = merge_chapter(incoming, slug=slug, he_title=he_title, overwrite_original=True)
                fetched += 1
                count = sum(bool(s.get("original_hebrew")) for s in doc["segments"].values())
                print(f"✓ {ref}: {count} original segments saved to {path}", flush=True)
            except (HTTPError, URLError, ValueError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                errors += 1
                print(f"✗ {ref}: {exc}", flush=True)
            if not args.dry_run:
                time.sleep(max(0, args.delay))
    except KeyboardInterrupt:
        print("\nInterrupted. Previously completed chapters are saved. Rerun to resume.", flush=True)
        raise SystemExit(130)
    print(
        f"Summary: {fetched} downloaded, {skipped} already cached, "
        f"{errors} failed" + (f", {planned} would download" if args.dry_run else "")
        + f"; {len(tasks)} chapters in selection.",
        flush=True,
    )
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
