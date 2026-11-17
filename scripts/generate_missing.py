"""Generate only missing kid-friendly Mishneh Torah adaptations with an LLM.

Default provider: Meta Muse Spark 1.3 Contributor (via the OpenAI-compatible Responses API).
Each successful segment is written to its chapter JSON immediately. Re-running
this script skips fields that are already present, including partially completed
segments. Newly generated adaptations are marked as AI drafts for human review.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from common import BOOKS, ROOT, load_json, rebuild_manifest, write_json

META_URL = "https://api.meta.ai/v1"
TARGETS = ("kids_hebrew", "kids_english")

INSTRUCTIONS = """You are preparing an educational, kid-accessible adaptation of a passage
from Maimonides' Mishneh Torah. Your job is to explain faithfully, not to add
new legal rulings or rewrite the law to fit modern expectations.

Hard requirements:
- Retain the passage's material legal distinctions, conditions, and exceptions.
- Use simple, precise language suitable for a young reader, without childish
  filler or adding speculative historical details. Do not silently omit difficult
  or sensitive concepts. Do not normalize historical practices as modern advice.
- If a concept needs a short explanation, explain it in the adaptation itself.
- For kids_hebrew: write clear modern Hebrew WITH full niqqud. Adapt the text;
  do not merely copy the original. Preserve technical Hebrew terms if useful.
- For kids_english: write natural, clear English, explain specialized Hebrew
  terms briefly, and do not oversimplify away any material condition.
- Respond with ONLY one JSON object, with exactly the requested keys and
  string values. No markdown fences, notes, or other prose.
- These are AI draft adaptations, to be checked by a human before publication.
"""


def missing_fields(segment: dict, only: str | None = None) -> list[str]:
    if not segment.get("original_hebrew"):
        return []
    candidates = (only,) if only else TARGETS
    return [key for key in candidates if not segment.get(key)]


def parse_response(text: str, expected_fields: list[str]) -> dict[str, str]:
    """Parse JSON, accepting optional markdown fences but no unrelated fields."""
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I).strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model did not return valid JSON: {raw[:160]!r}") from exc
    if not isinstance(obj, dict):
        raise ValueError("Model output must be a JSON object")
    result = {}
    for field in expected_fields:
        value = obj.get(field)
        if not isinstance(value, str) or not value.strip() or value.strip() in ("...", "N/A"):
            raise ValueError(f"Missing or empty {field} in model JSON")
        if field == "kids_hebrew" and not re.search(r"[\u0590-\u05ff]", value):
            raise ValueError("kids_hebrew does not appear to contain Hebrew")
        if field == "kids_english" and not re.search(r"[A-Za-z]", value):
            raise ValueError("kids_english does not appear to contain English")
        result[field] = value.strip()
    return result


def load_local_env(path: Path | None = None) -> None:
    """Load simple KEY=value entries from project-root .env without extra packages.

    Existing environment variables take priority. Keep .env out of Git and docs/.
    """
    path = path or ROOT / ".env"
    if not path.is_file():
        return
    for lineno, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            raise ValueError(f"Invalid .env entry on line {lineno}: expected KEY=value")
        key, value = (part.strip() for part in line.split("=", 1))
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"Invalid .env variable name on line {lineno}")
        # Double- or single-quoted keys are convenient; no special escaping needed.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("\"", "'"):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def create_client(provider: str, base_url: str | None = None):
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install the API client first: python -m pip install openai") from exc
    if provider == "meta":
        key = os.environ.get("MODEL_API_KEY")
        if not key:
            raise RuntimeError("Set MODEL_API_KEY to your Meta Model API key")
        return OpenAI(api_key=key, base_url=base_url or META_URL, max_retries=2)
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("Set OPENAI_API_KEY to your OpenAI API key")
    kwargs = {"api_key": key, "max_retries": 2}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def generate(client, model: str, ref: str, original: str, fields: list[str]) -> dict[str, str]:
    user_input = (
        f"Reference: {ref}\n"
        f"Original Hebrew:\n{original}\n\n"
        f"Return exactly these JSON keys: {json.dumps(fields, ensure_ascii=False)}"
    )
    response = client.responses.create(
        model=model,
        instructions=INSTRUCTIONS,
        input=user_input,
    )
    output = getattr(response, "output_text", None)
    if not isinstance(output, str) or not output.strip():
        raise ValueError("Model returned no output_text")
    return parse_response(output, fields)


def select_paths(args) -> list[Path]:
    if args.all:
        return sorted(BOOKS.glob("*/*.json"), key=lambda p: (p.parent.name, int(p.stem)))
    slug = args.slug or "mishneh-torah-slaves"
    folder = BOOKS / slug
    if args.chapter is not None:
        return [folder / f"{args.chapter}.json"]
    return sorted(folder.glob("*.json"), key=lambda p: int(p.stem))


def run(args, client=None) -> tuple[int, int]:
    paths = [p for p in select_paths(args) if p.is_file()]
    if not paths:
        print("No downloaded chapter JSON found. Run fetch_sefaria.py first.")
        return 0, 0
    attempts = 0
    successful = 0
    failures = 0
    changed = False
    try:
        for path in paths:
            doc = load_json(path)
            for index, segment in sorted(doc.get("segments", {}).items(), key=lambda item: int(item[0])):
                fields = missing_fields(segment, args.only)
                if not fields:
                    continue
                if args.limit is not None and attempts >= args.limit:
                    break
                ref = segment.get("ref") or f"{doc['ref']}:{index}"
                attempts += 1
                print(f"[{attempts}] {ref}: {', '.join(fields)}", flush=True)
                if args.dry_run:
                    continue
                try:
                    # Both missing languages are generated in one API call by default.
                    values = generate(client, args.model, ref, segment["original_hebrew"], fields)
                    for field, value in values.items():
                        if not segment.get(field):
                            segment[field] = value
                    segment["ai_draft"] = True
                    meta = segment.setdefault("ai_generation", {})
                    for field in fields:
                        meta[field] = {
                            "model": args.model,
                            "provider": args.provider,
                            "generated_at": datetime.now(timezone.utc).isoformat(),
                            "review_status": "needs_review",
                        }
                    # Atomic save after EVERY successful API call: safe to interrupt/resume.
                    write_json(path, doc)
                    changed = True
                    successful += 1
                except Exception as exc:
                    failures += 1
                    print(f"  ERROR: {exc}", file=sys.stderr, flush=True)
                    if args.fail_fast:
                        raise
                if args.delay and not args.dry_run:
                    time.sleep(args.delay)
            if args.limit is not None and attempts >= args.limit:
                break
    except KeyboardInterrupt:
        print("\nInterrupted. Previously generated fields are already saved.", file=sys.stderr)
    finally:
        if changed:
            rebuild_manifest()
    if args.dry_run:
        print(f"Dry run: {attempts} segments need adaptations. No API calls made.")
    else:
        print(f"Generated {successful} segments; {failures} errors; saved immediately.")
        print("Re-run the same command to continue from whatever is still missing.")
    return successful, failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="Process every downloaded book and chapter")
    parser.add_argument("--slug", help="Book slug (default: mishneh-torah-slaves)")
    parser.add_argument("--chapter", type=int, help="One chapter from the selected book")
    parser.add_argument("--only", choices=TARGETS, help="Generate only one target language")
    parser.add_argument("--limit", type=int, help="Maximum number of segments to attempt")
    parser.add_argument("--provider", choices=("meta", "openai"), default="meta")
    parser.add_argument("--model", help="Model ID; defaults to muse-spark-1.3-contributor for Meta")
    parser.add_argument("--base-url", help="Override API base URL (advanced)")
    parser.add_argument("--delay", type=float, default=0.25, help="Seconds between requests (default 0.25)")
    parser.add_argument("--dry-run", action="store_true", help="Preview missing segments without API calls")
    parser.add_argument("--fail-fast", action="store_true", help="Stop on first failed response")
    args = parser.parse_args(argv)
    if args.all and (args.slug or args.chapter):
        parser.error("--all cannot be combined with --slug or --chapter")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be >= 1")
    if args.delay < 0:
        parser.error("--delay must be >= 0")
    if not args.model:
        args.model = "muse-spark-1.3-contributor" if args.provider == "meta" else "gpt-5-mini"
    try:
        if not args.dry_run:
            load_local_env()
        client = None if args.dry_run else create_client(args.provider, args.base_url)
        _, failures = run(args, client=client)
    except (RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
