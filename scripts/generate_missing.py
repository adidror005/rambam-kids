"""Generate and check kid-friendly Mishneh Torah adaptations.

Default: generate only missing kids_hebrew/kids_english fields. --regenerate
refreshes older, attributable AI drafts; approved or untracked text is protected.

Every new generation receives a second, source-based AI nuance check by default.
If the check flags a material problem, the candidate is NOT published to chapter
JSON. --audit-existing checks older adaptations without changing their text.

The AI check is only a screening aid: human/rabbinic review is still required.
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
PROMPT_VERSION = "kids-adaptation-v4-cross-case-2026-11-17"
CHECK_VERSION = "source-nuance-check-v2-cross-case-2026-11-17"

INSTRUCTIONS = """You are faithfully adapting ONE passage from Rambam's Mishneh Torah
for children ages 8–12. Halachic meaning takes priority over simple language.
The source text, not your own general halachic knowledge, defines the scope.

Before writing, mentally inventory EVERY distinct rule in the source:
- Who the law applies to; the action; its setting; all prerequisites.
- Exact quantities, minimum/maximum thresholds, times, sequences, and exceptions.
- Whether an act is forbidden, permitted, valid/invalid, obligatory, exempt,
  punishable, or subject to a particular punishment. These are NOT interchangeable.
- Logical combinations such as AND/OR, 'only if', 'even when', and 'unless'.
- Differences between an original rule and a later qualification that overrides it.
- Which conditions apply ONLY to a particular quantity, situation or case.

Before writing, build an INTERNAL case-by-case rule table from the source. For
EVERY proposed broad sentence ("only when", "always", "all conditions must",
"in these cases", "never", and their Hebrew equivalents), test it against
EVERY exception and later clause of the source. If a later case contradicts a
summary, NARROW the summary to its true scope or remove it. Do not present an
initial case as a universal rule if the source later changes the outcome.

Keep lists and exceptions properly scoped: if a concession applies to exactly
one quantity or type of case, do not phrase it as if it applied regardless of
quantity. Preserve the distinction between EXACTLY a threshold and MORE THAN
that threshold; do not turn one into the other. Preserve the source's wording
when it is unclear whether a later qualification overrides earlier ones;
do not resolve ambiguity by assuming facts the source did not state.

Do not remove, conflate or reverse any of these distinctions. In particular,
פטור does not mean מותר; כשר does not imply permitted; absence of a particular
punishment does not imply there is no prohibition. Do not add conclusions from
commentaries, other chapters, or modern law. Do not invent examples or specifics
(e.g., an exact milliliter conversion) not present in the source. If the text
leaves something ambiguous, preserve that ambiguity rather than guessing.
Keep technical terms where they matter, explaining them briefly and accurately.
Do not conceal unsettling historical laws or suggest they apply to children today.

Style: Natural, respectful language; short sentences; short paragraphs of
2–4 sentences. First introduce the central rule; then carefully explain
conditions, consequences, and exceptions. For complex case distinctions,
separate cases visibly rather than merging them into a single sentence.
If the text has a standard case and a broader/special case, explain each
separately and explicitly identify which earlier conditions still matter.
Avoid repeated 'כלומר' and lengthy parenthetical definitions. Do NOT make the
text artificially short: preserving the details matters more than word count.

kids_hebrew: Modern, grammatical Hebrew WITH niqqud. Use short paragraphs
separated by two newlines within the JSON string. Preserve key Hebrew terms.
kids_english: Fluent, child-accessible English, same precise legal content.
Briefly explain specialized Hebrew terms when needed, in short paragraphs.

Respond ONLY with one valid JSON object containing exactly the requested keys
and string values, without markdown fences or commentary. These remain drafts
requiring human and rabbinic review before publication.
"""

CHECK_INSTRUCTIONS = """You are a STRICT source-faithfulness checker for educational
adaptations of one Mishneh Torah passage. Independently compare EACH adaptation
to the supplied ORIGINAL HEBREW; do not trust the adaptation's assertions.
This is NOT a request to critique style or to add details from outside sources.

Check BOTH directions:
(1) Is each source condition, exception, threshold, qualifier, consequence,
    actor, time constraint, and special case represented accurately?
(2) Does the adaptation add any unsupported prohibition, permission, exemption,
    rationale, historical claim or implication? Did it reverse AND/OR logic,
    'at least' vs 'more than', prohibited vs punished, or valid vs permitted?
Check negation and explicit numerical thresholds very carefully. Consider
whether short explanations change a technical term's meaning.

CRITICAL CROSS-CASE TEST (do this before deciding pass/flag):
- Identify every broad or categorical statement in the adaptation, including
  "only if all conditions are met", "in these cases", "even if", "always",
  "never", "only", and the corresponding Hebrew phrases (רק, תמיד, כל,
  בכל מקרה, רק כאשר, כל התנאים, אפילו, פטור, מותר).
- For EACH statement, test it against EVERY later exception, override and
  threshold change in the ORIGINAL. Try to find a source-supported case that
  would make the adaptation's summary false, even if a later paragraph is
  individually accurate. FLAG misleading global claims and failure to
  qualify a case-specific exception.
- Check that an exception involving a particular quantity is not stated as
  unconditional (e.g., dilution or pauses exempting one quantity but not a
  larger one). Distinguish EXACTLY the threshold from ABOVE the threshold.
- Check whether the adaptation invents a new qualifier for a later rule that
  the source did not specify, even when that qualifier appeared in an earlier
  rule. Do not silently assume a prior condition continues to apply.
- Verify that examples, glosses, and the overall summary agree with the
  source's full set of cases, not just one nearby sentence.

Return 'pass' ONLY if you find no material omission, distortion, unsupported
addition, or unresolved source ambiguity that makes the adaptation misleading.
Return 'flag' otherwise, including when you cannot confidently resolve a
material ambiguity. Do not flag mere changes in style or harmless paraphrases.
For each flagged adaptation, give concise, specific issues tied to the source.
Mention the conflicting conditions/cases and, when possible, suggest a concise
safe rewording in the issue text. Do not invent a resolution to ambiguity.

Return ONLY a JSON object whose keys are exactly the adaptation keys given,
and whose values are objects with this schema:
{"<field_name>": {"status": "pass" or "flag", "issues": ["specific issue"]}}
Replace <field_name> with each actual requested field key. If status is pass, issues MUST be [].
No markdown fences, extra keys, or prose. This is an automated screening step,
NOT a substitute for human/rabbinic review.
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_json_object(text: str) -> dict:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I).strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model returned invalid JSON: {raw[:180]!r}") from exc
    if not isinstance(obj, dict):
        raise ValueError("Model response must be a JSON object")
    return obj


def parse_response(text: str, expected_fields: list[str]) -> dict[str, str]:
    obj = parse_json_object(text)
    if set(obj) != set(expected_fields):
        raise ValueError(f"Expected exactly these keys: {expected_fields}; got {list(obj)}")
    result = {}
    for field in expected_fields:
        value = obj[field]
        if not isinstance(value, str) or not value.strip() or value.strip() in ("...", "N/A"):
            raise ValueError(f"Missing/empty {field} in model JSON")
        if field == "kids_hebrew":
            if not re.search(r"[\u0590-\u05ff]", value):
                raise ValueError("kids_hebrew contains no Hebrew")
            if not re.search(r"[\u05B0-\u05BC\u05C1-\u05C2\u05C7]", value):
                raise ValueError("kids_hebrew contains no niqqud")
        if field == "kids_english" and not re.search(r"[A-Za-z]", value):
            raise ValueError("kids_english contains no English")
        result[field] = value.strip()
    return result


def parse_checks(text: str, fields: list[str]) -> dict[str, dict]:
    obj = parse_json_object(text)
    if set(obj) != set(fields):
        raise ValueError(f"Checker expected keys {fields}; got {list(obj)}")
    result = {}
    for field in fields:
        entry = obj[field]
        if not isinstance(entry, dict) or set(entry) != {"status", "issues"}:
            raise ValueError(f"Checker response has invalid fields for {field}")
        status, issues = entry["status"], entry["issues"]
        if status not in ("pass", "flag") or not isinstance(issues, list) or any(
            not isinstance(issue, str) or not issue.strip() for issue in issues
        ):
            raise ValueError(f"Checker response invalid for {field}")
        if (status == "pass" and issues) or (status == "flag" and not issues):
            raise ValueError(f"Checker status/issues conflict for {field}")
        result[field] = {"status": status, "issues": issues}
    return result


def fields_to_generate(segment: dict, only: str | None, regenerate: bool,
                       retry_flagged: bool = False) -> list[str]:
    if not segment.get("original_hebrew"):
        return []
    meta = segment.get("ai_generation") or {}
    result = []
    for key in ((only,) if only else TARGETS):
        field_meta = meta.get(key) or {}
        if field_meta.get("review_status") == "approved":
            continue
        rejected = field_meta.get("last_rejected_attempt") or {}
        if not retry_flagged and rejected.get("prompt_version") == PROMPT_VERSION:
            continue  # Avoid repeated paid retries on the same flagged candidate.
        if not segment.get(key):
            result.append(key)
        elif regenerate and field_meta and field_meta.get("prompt_version") != PROMPT_VERSION:
            result.append(key)
        # Never overwrite text with no AI-generation provenance.
    return result


def fields_to_audit(segment: dict, only: str | None, force: bool = False) -> list[str]:
    if not segment.get("original_hebrew"):
        return []
    meta = segment.get("ai_generation") or {}
    result = []
    for key in ((only,) if only else TARGETS):
        if not segment.get(key):
            continue
        field_meta = meta.get(key) or {}
        if field_meta.get("review_status") == "approved":
            continue
        check = field_meta.get("automatic_check") or {}
        if force or check.get("check_version") != CHECK_VERSION:
            result.append(key)
    return result


def load_local_env(path: Path | None = None) -> None:
    """Load basic KEY=value pairs without overriding existing environment vars."""
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
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def create_client(provider: str, base_url: str | None = None):
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install the API client: python -m pip install openai") from exc
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


def model_output(client, model: str, instructions: str, input_text: str) -> str:
    response = client.responses.create(model=model, instructions=instructions, input=input_text)
    output = getattr(response, "output_text", None)
    if not isinstance(output, str) or not output.strip():
        raise ValueError("Model returned no output_text")
    return output


def generate(client, model: str, ref: str, original: str, fields: list[str]) -> dict[str, str]:
    prompt = (f"Reference: {ref}\nOriginal Hebrew:\n{original}\n\n"
              f"Return exactly these JSON keys: {json.dumps(fields, ensure_ascii=False)}")
    return parse_response(model_output(client, model, INSTRUCTIONS, prompt), fields)


def check_adaptations(client, model: str, ref: str, original: str,
                      adaptations: dict[str, str]) -> dict[str, dict]:
    fields = list(adaptations)
    prompt = (f"Reference: {ref}\nOriginal Hebrew (authoritative):\n{original}\n\n"
              f"Adaptations to check:\n{json.dumps(adaptations, ensure_ascii=False)}\n\n"
              f"Return results for exactly these keys: {json.dumps(fields, ensure_ascii=False)}")
    return parse_checks(model_output(client, model, CHECK_INSTRUCTIONS, prompt), fields)


def select_paths(args) -> list[Path]:
    if args.all:
        return sorted(BOOKS.glob("*/*.json"), key=lambda p: (p.parent.name, int(p.stem)))
    folder = BOOKS / (args.slug or "mishneh-torah-slaves")
    if args.chapter is not None:
        return [folder / f"{args.chapter}.json"]
    return sorted(folder.glob("*.json"), key=lambda p: int(p.stem))


def list_flags(args) -> int:
    """Show fields whose audits or regenerated candidates raised concerns."""
    count = 0
    for path in (p for p in select_paths(args) if p.is_file()):
        doc = load_json(path)
        for index, segment in sorted(doc.get("segments", {}).items(), key=lambda pair: int(pair[0])):
            ref = segment.get("ref") or f"{doc['ref']}:{index}"
            meta = segment.get("ai_generation") or {}
            for field in ((args.only,) if args.only else TARGETS):
                field_meta = meta.get(field) or {}
                check = field_meta.get("automatic_check") or {}
                rejected = field_meta.get("last_rejected_attempt") or {}
                issues = []
                if check.get("status") == "flag":
                    issues.extend(check.get("issues") or [])
                if rejected:
                    issues.extend(rejected.get("issues") or [])
                if issues:
                    count += 1
                    print(f"{ref} [{field}]:")
                    for issue in dict.fromkeys(issues):
                        print(f"  - {issue}")
    print(f"Flagged fields: {count}")
    return count


def run(args, client=None) -> tuple[int, int, int]:
    paths = [p for p in select_paths(args) if p.is_file()]
    if not paths:
        print("No downloaded chapter JSON found. Run fetch_sefaria.py first.")
        return 0, 0, 0
    attempts = successful = flagged = failures = 0
    changed = False
    try:
        for path in paths:
            doc = load_json(path)
            for index, segment in sorted(doc.get("segments", {}).items(), key=lambda item: int(item[0])):
                fields = (fields_to_audit(segment, args.only, args.force_audit)
                          if args.audit_existing else
                          fields_to_generate(segment, args.only, args.regenerate,
                                             args.retry_flagged))
                if not fields:
                    continue
                if args.limit is not None and attempts >= args.limit:
                    break
                ref = segment.get("ref") or f"{doc['ref']}:{index}"
                attempts += 1
                action = "audit" if args.audit_existing else "generate"
                print(f"[{attempts}] {ref} ({action}): {', '.join(fields)}", flush=True)
                if args.dry_run:
                    continue
                try:
                    original = segment["original_hebrew"]
                    if args.audit_existing:
                        candidates = {key: segment[key] for key in fields}
                    else:
                        candidates = generate(client, args.model, ref, original, fields)
                    checks = (check_adaptations(client, args.check_model or args.model,
                                                ref, original, candidates)
                              if not args.no_verify else
                              {key: {"status": "not_run", "issues": []} for key in fields})

                    meta = segment.setdefault("ai_generation", {})
                    for field in fields:
                        item = meta.setdefault(field, {})
                        verdict = checks[field]
                        if verdict["status"] == "flag":
                            flagged += 1
                            print(f"  FLAGGED {field}: {'; '.join(verdict['issues'])}",
                                  file=sys.stderr, flush=True)
                            if args.audit_existing:
                                # Audit marks risk but leaves displayed text unchanged.
                                item["automatic_check"] = {
                                    **verdict, "check_version": CHECK_VERSION,
                                    "model": args.check_model or args.model,
                                    "checked_at": now_iso(),
                                }
                                item["review_status"] = "flagged"
                            else:
                                # Do not publish rejected draft; keep existing version.
                                item["last_rejected_attempt"] = {
                                    "prompt_version": PROMPT_VERSION,
                                    "issues": verdict["issues"],
                                    "attempted_at": now_iso(),
                                }
                            continue
                        if not args.audit_existing:
                            segment[field] = candidates[field]
                            item.update({
                                "model": args.model, "provider": args.provider,
                                "prompt_version": PROMPT_VERSION,
                                "generated_at": now_iso(),
                                "review_status": "needs_review",
                            })
                            item.pop("last_rejected_attempt", None)
                            segment["ai_draft"] = True
                        item["automatic_check"] = {
                            **verdict,
                            "check_version": CHECK_VERSION if not args.no_verify else None,
                            "model": args.check_model or args.model if not args.no_verify else None,
                            "checked_at": now_iso() if not args.no_verify else None,
                        }
                        if args.audit_existing:
                            item["review_status"] = "needs_review"
                        successful += 1
                    # One atomic chapter write per passage; handles partial acceptance.
                    write_json(path, doc)
                    changed = True
                except Exception as exc:
                    failures += 1
                    print(f"  ERROR: {exc}", file=sys.stderr, flush=True)
                    if args.fail_fast:
                        raise
                if args.delay:
                    time.sleep(args.delay)
            if args.limit is not None and attempts >= args.limit:
                break
    except KeyboardInterrupt:
        print("\nInterrupted. Accepted adaptations and check results were saved.", file=sys.stderr)
    finally:
        if changed:
            rebuild_manifest()
    if args.dry_run:
        print(f"Dry run: {attempts} passages selected. No API calls or writes.")
    else:
        print(f"Accepted/audited {successful} fields; flagged {flagged}; API errors {failures}.")
        if flagged:
            print("Flagged fields require review. Rejected new drafts were not published.")
        print("Rerun to continue; completed or flagged same-version attempts are skipped.")
    return successful, flagged, failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="All downloaded books/chapters")
    parser.add_argument("--list-flags", action="store_true",
                        help="List previously flagged passages; no API calls or writes")
    parser.add_argument("--slug", help="Book slug (default: mishneh-torah-slaves)")
    parser.add_argument("--chapter", type=int, help="One chapter in the selected book")
    parser.add_argument("--only", choices=TARGETS, help="Select one adaptation language")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--regenerate", action="store_true", help="Refresh older AI drafts")
    mode.add_argument("--audit-existing", action="store_true",
                      help="Check existing adaptations without changing their text")
    parser.add_argument("--force-audit", action="store_true", help="Recheck even if already audited")
    parser.add_argument("--retry-flagged", action="store_true", help="Retry previously rejected generated drafts")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip the second AI check (NOT recommended for publication)")
    parser.add_argument("--limit", type=int, help="Maximum number of passages to attempt")
    parser.add_argument("--provider", choices=("meta", "openai"), default="meta")
    parser.add_argument("--model", help="Generation model (Meta default: muse-spark-1.3)")
    parser.add_argument("--check-model", help="Model used for independent second call (same provider)")
    parser.add_argument("--base-url", help="Optional OpenAI-compatible base URL")
    parser.add_argument("--delay", type=float, default=0.25, help="Seconds between passages")
    parser.add_argument("--dry-run", action="store_true", help="Preview without API calls/writes")
    parser.add_argument("--fail-fast", action="store_true", help="Stop on first API/parse error")
    args = parser.parse_args(argv)
    if args.all and (args.slug or args.chapter):
        parser.error("--all cannot be combined with --slug or --chapter")
    if args.force_audit and not args.audit_existing:
        parser.error("--force-audit requires --audit-existing")
    if args.audit_existing and args.no_verify:
        parser.error("--audit-existing cannot be used with --no-verify")
    if args.list_flags and (args.audit_existing or args.regenerate):
        parser.error("--list-flags cannot be combined with --regenerate/--audit-existing")
    if args.audit_existing and args.retry_flagged:
        parser.error("--retry-flagged is for generation only")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be >= 1")
    if args.delay < 0:
        parser.error("--delay must be >= 0")
    if not args.model:
        args.model = "muse-spark-1.3-contributor" if args.provider == "meta" else "gpt-5-mini"
    if args.list_flags:
        list_flags(args)
        return 0
    try:
        if not args.dry_run:
            load_local_env()
        client = None if args.dry_run else create_client(args.provider, args.base_url)
        _, flagged, errors = run(args, client=client)
    except (RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    return 1 if errors or flagged else 0


if __name__ == "__main__":
    sys.exit(main())
