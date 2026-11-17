"""Generate kid-friendly Mishneh Torah adaptations with an LLM.

Default provider: Meta Muse Model API (via the OpenAI-compatible Responses API).
By default, generate only missing fields; successful results are saved immediately.
With --regenerate, update older AI-generated adaptations to the current prompt
version, while skipping fields already regenerated with that version. Approved or
untracked existing adaptations are not overwritten automatically.
These remain AI drafts pending careful human / rabbinic review.
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

# Increment this when you change the adaptation rules and want to upgrade drafts.
PROMPT_VERSION = "kids-adaptation-v2-2026-11-17"

INSTRUCTIONS = """You are preparing a kid-friendly educational adaptation of a
passage from Maimonides' Mishneh Torah. Explain the source faithfully.
Do not add rulings, speculate about historical details, or modernize the law.

Accuracy is more important than brevity:
- Preserve every material halachic distinction, condition, exception, threshold,
  consequence and limitation in the original source. Do not skip difficult or
  sensitive concepts to make the text more child-friendly.
- Distinguish what is prohibited from what makes a person liable or an action
  invalid. In particular, 'patur' (פטור) does NOT automatically mean 'permitted'
  (מותר); and 'the service remains valid' does NOT automatically mean the
  action was allowed. Explain these distinctions in simple words where relevant.
- Do not invent new prohibitions, exceptions, examples, interpretations, or
  explanations not justified by the source. Use carefully qualified wording
  whenever meaning depends on context. Preserve short source quotations exactly.
- Explain unfamiliar terms briefly, accurately, and in context. For example,
  if relevant: 'יין חי' means undiluted wine (not necessarily extra-strong wine);
  'רביעית' is a halachic volume measure (do not invent a fixed modern conversion).

Writing style:
- Target ages 8-12. Use natural language, short sentences, and short paragraphs
  (normally 2-4 sentences). When several conditions are listed, use separate
  short sentences or a short numbered list inside the text rather than one
  enormous paragraph. Preserve all meaningful details even if the result is long.
- Lead with the main rule. Then explain the key conditions, consequences,
  and exceptions in a logical sequence.
- Avoid repetitive 'כלומר' in Hebrew or 'meaning' in English; explain a term
  once in a natural phrase. Avoid legal jargon when a simple faithful expression
  will do, but retain and briefly explain terms whose precision matters.
- For kids_hebrew: write clear, modern Hebrew WITH niqqud. Avoid long nested
  clauses. Produce several paragraphs separated by \n\n in the JSON string.
- For kids_english: write clear natural English in several short paragraphs,
  explaining technical Hebrew terms when important.
- Do not present historical legal arrangements as instructions for children today.
- Respond with ONLY one valid JSON object with exactly the requested keys, each
  containing a string. No markdown fences or unrelated prose.
- These are AI drafts requiring human/rabbinic review before publication.
"""


def fields_to_generate(segment: dict, only: str | None = None,
                       regenerate: bool = False) -> list[str]:
    """Find missing fields, or older generated drafts eligible for refresh.

    When regenerating, protect approved fields and existing content without
    known AI provenance; skip fields already upgraded to PROMPT_VERSION so an
    interrupted --regenerate run resumes without re-paying for completed work.
    """
    if not segment.get("original_hebrew"):
        return []
    candidates = (only,) if only else TARGETS
    meta = segment.get("ai_generation") or {}
    result = []
    for key in candidates:
        if not segment.get(key):
            result.append(key)
            continue
        if not regenerate:
            continue
        field_meta = meta.get(key) or {}
        if field_meta.get("review_status") == "approved":
            continue
        if not field_meta:
            # The field may be a manually authored adaptation.
            continue
        if field_meta.get("prompt_version") != PROMPT_VERSION:
            result.append(key)
    return result


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
                fields = fields_to_generate(segment, args.only, args.regenerate)
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
                    # All selected fields are generated in one API call by default.
                    values = generate(client, args.model, ref, segment["original_hebrew"], fields)
                    for field, value in values.items():
                        # Replace only fields chosen above; preserve original Hebrew.
                        segment[field] = value
                    segment["ai_draft"] = True
                    meta = segment.setdefault("ai_generation", {})
                    for field in fields:
                        meta[field] = {
                            "model": args.model,
                            "provider": args.provider,
                            "prompt_version": PROMPT_VERSION,
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
        print(f"Dry run: {attempts} segments selected. No API calls made.")
    else:
        print(f"Generated {successful} segments; {failures} errors; saved immediately.")
        print("Re-run the same command to continue. Completed fields are skipped.")
    return successful, failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="Process every downloaded book and chapter")
    parser.add_argument("--slug", help="Book slug (default: mishneh-torah-slaves)")
    parser.add_argument("--chapter", type=int, help="One chapter from the selected book")
    parser.add_argument("--only", choices=TARGETS, help="Generate only one target language")
    parser.add_argument("--regenerate", action="store_true",
                        help="Refresh older AI adaptations to the current prompt version; skip approved fields")
    parser.add_argument("--limit", type=int, help="Maximum number of segments to attempt")
    parser.add_argument("--provider", choices=("meta", "openai"), default="meta")
    parser.add_argument("--model", help="Model ID; defaults to muse-spark-1.3 for Meta")
    parser.add_argument("--base-url", help="Override API base URL (advanced)")
    parser.add_argument("--delay", type=float, default=0.25, help="Seconds between requests (default 0.25)")
    parser.add_argument("--dry-run", action="store_true", help="Preview selected segments without API calls")
    parser.add_argument("--fail-fast", action="store_true", help="Stop on first failed response")
    args = parser.parse_args(argv)
    if args.all and (args.slug or args.chapter):
        parser.error("--all cannot be combined with --slug or --chapter")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be >= 1")
    if args.delay < 0:
        parser.error("--delay must be >= 0")
    if not args.model:
        args.model = "muse-spark-1.3" if args.provider == "meta" else "gpt-5-mini"
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
