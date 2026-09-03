#!/usr/bin/env python3
"""Check `catalog/` and the generated lanes. Pure: reads, never writes.

Exits 0 when everything holds, 1 with one line per problem otherwise. Each
line names the file, the key and what is wrong, so the fix does not require
reading this script.

What it checks, in order:

  1. schema coverage  — the mini JSON Schema validator understands every
     keyword `schema/*.schema.json` uses (so a rule can never be skipped
     silently).
  2. schema           — every `catalog/*.json` against `schema/catalog.schema.json`,
     `_glossary.json` against `schema/glossary.schema.json`.
  3. locale identity  — `locale` matches the file name.
  4. key parity       — every locale has exactly the source locale's keys.
  5. metadata         — `comment` required in `en`, `comment`/`placeholders`
     forbidden everywhere else.
  6. ICU syntax       — every value parses; every plural has an `other` branch.
  7. placeholder parity — the names used in a value are exactly the declared set.
  8. plural shape     — the plural argument is declared, is an integer, and a
     translation never invents a plural the source does not have.
  9. glossary         — a never-translate term in the source value appears
     verbatim in every translation of that key.
 10. freshness        — regenerating into a temp directory reproduces exactly
     what is checked in.

Usage:
    python3 scripts/validate.py
    python3 scripts/validate.py --skip-generated   # catalog only
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _catalog import (  # noqa: E402
    CATALOG_DIR,
    GLOSSARY_BASENAME,
    REPO_ROOT,
    SCHEMA_DIR,
    SOURCE_LOCALE,
    Catalog,
    CatalogError,
    IcuError,
    check_schema_coverage,
    load_all,
    load_json,
    locale_files,
    parse_icu,
    plain_text,
    plural_arguments,
    rel,
    used_placeholders,
    validate_schema,
)
import generate  # noqa: E402


def _catalog_label(catalog: Catalog) -> str:
    return rel(catalog.path)


# --------------------------------------------------------------------------
# 1 + 2. Schema
# --------------------------------------------------------------------------


def check_schemas(problems: List[str]) -> None:
    catalog_schema_path = os.path.join(SCHEMA_DIR, "catalog.schema.json")
    glossary_schema_path = os.path.join(SCHEMA_DIR, "glossary.schema.json")
    catalog_schema = load_json(catalog_schema_path)
    glossary_schema = load_json(glossary_schema_path)

    problems += check_schema_coverage(catalog_schema, rel(catalog_schema_path))
    problems += check_schema_coverage(glossary_schema, rel(glossary_schema_path))

    for locale, path in locale_files():
        raw = load_json(path)
        problems += validate_schema(raw, catalog_schema, rel(path))
        declared = raw.get("locale") if isinstance(raw, dict) else None
        if declared != locale:
            problems.append(
                "%s: `locale` is %r but the file name says %r; they must match, "
                "because the file name is what names the .lproj directory"
                % (rel(path), declared, locale)
            )
        expected_schema = "../schema/catalog.schema.json"
        if isinstance(raw, dict) and raw.get("$schema") not in (None, expected_schema):
            problems.append(
                "%s: `$schema` is %r, expected %r"
                % (rel(path), raw.get("$schema"), expected_schema)
            )

    glossary_path = os.path.join(CATALOG_DIR, GLOSSARY_BASENAME)
    problems += validate_schema(load_json(glossary_path), glossary_schema, rel(glossary_path))


# --------------------------------------------------------------------------
# 3-8. Catalog invariants
# --------------------------------------------------------------------------


def check_catalogs(catalogs: Sequence[Catalog], problems: List[str]) -> None:
    by_locale = {catalog.locale: catalog for catalog in catalogs}
    if SOURCE_LOCALE not in by_locale:
        problems.append(
            "catalog/%s.json is missing; it is the source locale every other "
            "file is checked against" % SOURCE_LOCALE
        )
        return
    source = by_locale[SOURCE_LOCALE]

    # -- key parity -------------------------------------------------------
    source_keys = set(source.entries)
    for catalog in catalogs:
        if catalog.locale == SOURCE_LOCALE:
            continue
        label = _catalog_label(catalog)
        for key in sorted(source_keys - set(catalog.entries)):
            problems.append(
                "%s: missing key %r; every locale carries every key (ship the "
                "English text as the value if the translation is not ready)"
                % (label, key)
            )
        for key in sorted(set(catalog.entries) - source_keys):
            problems.append(
                "%s: key %r is not in catalog/%s.json; a key is authored in the "
                "source locale first" % (label, key, SOURCE_LOCALE)
            )

    # -- metadata placement ----------------------------------------------
    for key in sorted(source.entries):
        if not source.entries[key].comment:
            problems.append(
                "%s: %s: `comment` is required in the source locale; a "
                "translator cannot see your screen" % (_catalog_label(source), key)
            )
    for catalog in catalogs:
        if catalog.locale == SOURCE_LOCALE:
            continue
        label = _catalog_label(catalog)
        raw = load_json(catalog.path)["keys"]
        for key in sorted(catalog.entries):
            body = raw.get(key) or {}
            for field in ("comment", "placeholders"):
                if field in body:
                    problems.append(
                        "%s: %s: `%s` is declared in the source locale only; two "
                        "files declaring the same metadata is two files that will "
                        "disagree" % (label, key, field)
                    )

    # -- ICU, placeholders, plurals ---------------------------------------
    source_plurals: Dict[str, List[str]] = {}
    for key in sorted(source.entries):
        entry = source.entries[key]
        try:
            nodes = parse_icu(entry.value)
        except IcuError as exc:
            problems.append("%s: %s: %s" % (_catalog_label(source), key, exc))
            continue
        source_plurals[key] = plural_arguments(nodes)
        declared = set(entry.placeholders)
        used = used_placeholders(nodes)
        _report_placeholder_parity(problems, _catalog_label(source), key, declared, used)
        for name in source_plurals[key]:
            kind = entry.placeholders.get(name)
            if kind is not None and kind != "int":
                problems.append(
                    "%s: %s: plural argument {%s} is declared %r; a plural selects "
                    "on a whole number, so it must be \"int\"" % (
                        _catalog_label(source), key, name, kind
                    )
                )

    for catalog in catalogs:
        if catalog.locale == SOURCE_LOCALE:
            continue
        label = _catalog_label(catalog)
        for key in sorted(catalog.entries):
            if key not in source.entries:
                continue
            try:
                nodes = parse_icu(catalog.entries[key].value)
            except IcuError as exc:
                problems.append("%s: %s: %s" % (label, key, exc))
                continue
            declared = set(source.entries[key].placeholders)
            _report_placeholder_parity(problems, label, key, declared, used_placeholders(nodes))
            extra_plurals = set(plural_arguments(nodes)) - set(source_plurals.get(key, []))
            for name in sorted(extra_plurals):
                problems.append(
                    "%s: %s: {%s} is a plural here but not in catalog/%s.json; a "
                    "translation cannot introduce a plural the source locale does "
                    "not declare" % (label, key, name, SOURCE_LOCALE)
                )


def _report_placeholder_parity(
    problems: List[str], label: str, key: str, declared: "set", used: "set"
) -> None:
    for name in sorted(used - declared):
        problems.append(
            "%s: %s: value uses {%s}, which is not in the declared placeholders "
            "(%s); a translation cannot invent a placeholder"
            % (label, key, name, ", ".join(sorted(declared)) or "none")
        )
    for name in sorted(declared - used):
        problems.append(
            "%s: %s: declared placeholder {%s} is never used in the value; a "
            "dropped placeholder means the value the caller passes disappears"
            % (label, key, name)
        )


# --------------------------------------------------------------------------
# 9. Glossary
# --------------------------------------------------------------------------


def _contains_term(haystack: str, term: str) -> bool:
    """Substring match that will not fire inside a longer word."""
    start = 0
    while True:
        index = haystack.find(term, start)
        if index < 0:
            return False
        before = haystack[index - 1] if index > 0 else ""
        after_index = index + len(term)
        after = haystack[after_index] if after_index < len(haystack) else ""
        if not (before.isalnum() and before.isascii()) and not (
            after.isalnum() and after.isascii()
        ):
            return True
        start = index + 1


def check_glossary(catalogs: Sequence[Catalog], problems: List[str]) -> None:
    glossary_path = os.path.join(CATALOG_DIR, GLOSSARY_BASENAME)
    raw = load_json(glossary_path)
    terms = [item["term"] for item in raw.get("neverTranslate", []) if "term" in item]

    by_locale = {catalog.locale: catalog for catalog in catalogs}
    source = by_locale.get(SOURCE_LOCALE)
    if source is None:
        return

    for key in sorted(source.entries):
        try:
            source_text = plain_text(parse_icu(source.entries[key].value))
        except IcuError:
            continue  # already reported
        present = [term for term in terms if _contains_term(source_text, term)]
        if not present:
            continue
        for catalog in catalogs:
            if catalog.locale == SOURCE_LOCALE or key not in catalog.entries:
                continue
            try:
                translated = plain_text(parse_icu(catalog.entries[key].value))
            except IcuError:
                continue
            for term in present:
                if term not in translated:
                    problems.append(
                        "%s: %s: never-translate term %r appears in the source "
                        "value but not in this translation; %s must stay in "
                        "English (catalog/%s)"
                        % (_catalog_label(catalog), key, term, term, GLOSSARY_BASENAME)
                    )


# --------------------------------------------------------------------------
# 10. Reuse before you add
# --------------------------------------------------------------------------


def _reuse_signature(text: str) -> str:
    """What two strings must share before they count as the same sentence.

    Case, surrounding whitespace, the trailing full stop and the ellipsis a
    button adds are all presentation. Placeholder *names* are not compared
    either: `{provider} is offline` and `{tool} is offline` are one sentence
    with one translation, and letting the name divide them is how a catalog
    grows two of everything.
    """
    lowered = text.strip().lower().rstrip(".…")
    out: List[str] = []
    depth = 0
    for ch in lowered:
        if ch == "{":
            depth += 1
            out.append("{}")
            continue
        if ch == "}":
            depth = max(0, depth - 1)
            continue
        if depth == 0:
            out.append(ch)
    return " ".join("".join(out).split())


def check_reuse(catalogs: Sequence[Catalog], problems: List[str]) -> None:
    """Two keys whose English says the same thing are one key with two names.

    Both clients render this catalog, and the moment the same sentence exists
    twice they drift: one gets retranslated, the other does not, and a
    reviewer comparing screenshots cannot tell which key a screen used. The
    rule is reuse, and the check is here rather than in a document because a
    document does not fail a pull request.

    A genuine collision — two identical English strings that must stay
    separately translatable, because some language distinguishes them — is
    declared by giving one of them a `comment` containing `distinct-from:
    <other.key>`, which is a sentence a reviewer can weigh.
    """
    by_locale = {catalog.locale: catalog for catalog in catalogs}
    source = by_locale.get(SOURCE_LOCALE)
    if source is None:
        return

    seen: dict = {}
    for key in sorted(source.entries):
        entry = source.entries[key]
        signature = _reuse_signature(entry.value)
        if not signature:
            continue
        first = seen.get(signature)
        if first is None:
            seen[signature] = key
            continue
        comments = " ".join(
            filter(None, [source.entries[first].comment, entry.comment])
        )
        if ("distinct-from: %s" % first) in comments or (
            "distinct-from: %s" % key
        ) in comments:
            continue
        problems.append(
            "%s: %s repeats the sentence already keyed as %s (%r); reuse that "
            "key, or declare why it must stay separate with a comment saying "
            "`distinct-from: %s`"
            % (_catalog_label(source), key, first, entry.value, first)
        )


# --------------------------------------------------------------------------
# 10. Generated-file freshness
# --------------------------------------------------------------------------


def check_generated(problems: List[str]) -> None:
    try:
        outputs = generate.build_outputs(load_all())
    except CatalogError as exc:
        problems.append("generate: %s" % exc)
        return
    except Exception as exc:  # noqa: BLE001 - a crash here is still a report
        problems.append(
            "generate: crashed on this catalog (%s: %s); fix the problems above first"
            % (type(exc).__name__, exc)
        )
        return

    scratch = tempfile.mkdtemp(prefix="vibe-bar-i18n-generate-")
    try:
        generate.write_outputs(scratch, outputs)
        for relative in sorted(outputs):
            expected = outputs[relative]
            actual = _read(os.path.join(REPO_ROOT, relative))
            if actual is None:
                problems.append(
                    "%s: generated file is missing; run python3 scripts/generate.py"
                    % relative
                )
            elif actual != expected:
                problems.append(
                    "%s: generated file is stale (%s); run python3 scripts/generate.py "
                    "and commit the result" % (relative, _first_difference(actual, expected))
                )
        _report_orphans(outputs, problems)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _report_orphans(outputs: "Dict[str, str]", problems: List[str]) -> None:
    """Generated files on disk that the catalog no longer produces."""
    roots = [
        "implementations/swift/Resources",
        "implementations/swift/Sources/VibeBarLocalization/Generated",
        "implementations/typescript/src/generated",
    ]
    expected = set(outputs)
    for root in roots:
        absolute = os.path.join(REPO_ROOT, root)
        if not os.path.isdir(absolute):
            continue
        for directory, _, names in os.walk(absolute):
            for name in names:
                if name == ".DS_Store":
                    continue
                relative = os.path.relpath(os.path.join(directory, name), REPO_ROOT)
                if relative not in expected:
                    problems.append(
                        "%s: generated tree holds a file the catalog does not "
                        "produce; delete it and re-run python3 scripts/generate.py"
                        % relative
                    )


def _first_difference(actual: str, expected: str) -> str:
    actual_lines = actual.split("\n")
    expected_lines = expected.split("\n")
    for index in range(max(len(actual_lines), len(expected_lines))):
        left = actual_lines[index] if index < len(actual_lines) else "<end of file>"
        right = expected_lines[index] if index < len(expected_lines) else "<end of file>"
        if left != right:
            return "line %d: checked in %r, generated %r" % (index + 1, left, right)
    return "content differs"


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


# --------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--skip-generated",
        action="store_true",
        help="check the catalog only; do not compare the generated lanes",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    problems: List[str] = []
    try:
        check_schemas(problems)
        catalogs = load_all()
        check_catalogs(catalogs, problems)
        check_glossary(catalogs, problems)
        check_reuse(catalogs, problems)
        # Freshness last, and only on a catalog that already holds together:
        # regenerating from a broken catalog produces broken output, and
        # "the generated file is stale" is a confusing thing to say to
        # somebody whose real problem is a missing key.
        if not args.skip_generated and not problems:
            check_generated(problems)
    except CatalogError as exc:
        sys.stderr.write("validate: %s\n" % exc)
        return 1

    if problems:
        for problem in problems:
            sys.stderr.write("validate: %s\n" % problem)
        sys.stderr.write("validate: %d problem(s)\n" % len(problems))
        return 1

    locales = ", ".join(sorted(catalog.locale for catalog in catalogs))
    print(
        "validate: OK — %d keys x %d locales (%s)"
        % (len(catalogs[0].entries), len(catalogs), locales)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
