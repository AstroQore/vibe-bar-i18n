#!/usr/bin/env python3
"""Convert the native app's catalogue into this repository's schema.

`AstroQore/vibe-bar` grew its own `Resources/i18n/` while this repository
was still a skeleton, so the strings exist twice under two schemas and, in
places, two names. This is the one-way door between them: it reads the
app's catalogue and writes `catalog/*.json` in the shape both clients read.

It is a *script*, not a migration that runs once. The app keeps adding keys
in parallel batches, so this is expected to be re-run at extraction time
against whatever the app's catalogue holds then. Everything it decides is
in the tables below, where a reviewer can weigh it — nothing is inferred
from the strings themselves.

    python3 scripts/import_from_app.py --app-root ../vibe-bar
    python3 scripts/import_from_app.py --app-root ../vibe-bar --report

Two schema differences had to resolve somewhere:

*   **`args` (app) versus `placeholders` (here).** This repository wins. It
    is the schema two clients read, it is the one the JSON Schema and both
    generators already encode, and the app's copy is about to stop existing
    — converting the other way would mean editing the shared schema, both
    generators and the TypeScript lane to adopt the name of the client that
    happened to be written first, which is exactly what § 4 says not to do.
*   **`double`.** This repository allows `string`, `int` and `double`; the
    app's generator rejects `double` on the grounds that number formatting
    is locale-dependent and belongs in one formatter. That reasoning is
    right about *formatting* and wrong about *declaring an argument's type*:
    `double` here means "the client receives a number and formats it", which
    is the same rule stated from the other side. This repository wins again,
    and the app's generator does not need to grow to match, because at
    extraction it is deleted rather than extended — `scripts/generate.py`
    produces the Swift lane from then on. No key in the app declares a
    decimal today, so the converter never emits one; the type stays
    available for whichever client needs it first.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CATALOG_DIR = os.path.join(ROOT, "catalog")
SOURCE_LOCALE = "en"
LOCALES = ["en", "zh-Hans"]

sys.path.insert(0, HERE)

# The repository's own ICU parser and reuse signature. Re-deriving either
# here would mean two implementations of the rule the validator enforces,
# and the first version of this script proved the point: a regex looking for
# `{name}` read the plural branch `one {once}` as a placeholder named
# "once", and declared an argument no caller passes.
from _catalog import parse_icu, used_placeholders  # noqa: E402
from validate import _reuse_signature  # noqa: E402

# ---------------------------------------------------------------------------
# 1. Keys that are not copy, and do not belong in a catalogue at all
# ---------------------------------------------------------------------------

# A catalogue holds sentences. These hold formatting, which § 3 makes the
# client's job — and each one also has a working replacement on the native
# side already, so dropping them costs nothing but a call-site edit at
# extraction time.
DROP: Dict[str, str] = {
    **{
        "common.date.month.%d" % month:
        "a CLDR month name; every platform's date formatter has these, and "
        "the key also breaks this repository's key pattern with its numeric "
        "segment. The app now renders dates through AppLocale skeletons."
        for month in range(1, 13)
    },
    "common.date.monthDayTime":
        "date assembly. `AppLocale.dateFormatter(template:)` builds this from "
        "a CLDR skeleton, in the right order for the locale, without a key.",
    "common.date.monthDayYearTime":
        "date assembly, as above.",
    "quota.resetCredits.count":
        "a bare number with no words around it. `AppLocale.number(_:)` renders "
        "it; a key whose entire value is one placeholder carries no sentence "
        "for a translator to translate.",
    "settings.miniWindow.position":
        "a bare number, as above.",
    "settings.pricing.number":
        "a bare number, as above.",
    "cost.modelRanking.rank":
        "a bare number behind a sigil. `#` outside a plural is literal ICU "
        "that the TypeScript runtime reports as unrendered, and the value "
        "carries no words either way — the client renders the rank.",
}

# ---------------------------------------------------------------------------
# 2. Renames onto the names this repository already uses
# ---------------------------------------------------------------------------

# § 4: "If the other client already keyed it, use theirs." These are the
# concepts both catalogues had, under different names. The four that already
# agreed (common.refresh, common.retry, error.rateLimited,
# settings.language.title) need no entry — they confirm the mapping rather
# than exercise it.
RENAME: Dict[str, str] = {
    "error.needsLogin": "error.needsReLogin",
    "error.noCredential": "error.noAccountFound",
    "error.networkDetail": "error.networkWithReason",
    "quota.forecast.value.left": "quota.remainingPercent",
    "quota.forecast.value.used": "quota.usedPercent",
    "quota.resetHistory.lane.noCycles": "resetHistory.noCompletedCycles",
    "quota.resetHistory.totals.headline": "resetHistory.wastedSummary",
    "quota.resetHistory.lane.wasteSummary": "resetHistory.laneAverage",
}

# `resetHistory.*` is a top-level namespace here (AGENTS.md § 3) and was
# nested under `quota.` in the app. Applied after RENAME, so the entries
# above win where both would match.
NAMESPACE_MOVES: List[Tuple[str, str]] = [
    ("quota.resetHistory.", "resetHistory."),
]

# ---------------------------------------------------------------------------
# 3. Duplicates: which key survives
# ---------------------------------------------------------------------------

# One concept, two names. The survivor is on the left; every alias on the
# right is dropped and its call sites move at extraction time. Chosen for
# the more general home: a string used on three surfaces belongs in
# `common.*`, not in whichever surface happened to key it first.
COLLAPSE: Dict[str, List[str]] = {
    "common.duration.days": ["quota.freshness.age.days"],
    "common.duration.hours": ["quota.freshness.age.hours"],
    "common.duration.minutes": ["quota.freshness.age.minutes"],
    "common.off": ["platform.macos.launchAtLogin.off"],
    "common.updated.justNow": ["status.card.updatedJustNow"],
    "cost.timeframe.today": ["cost.metric.today"],
    "cost.timeframe.yesterday": ["cost.metric.yesterday"],
    "cost.timeframe.weekShort": ["cost.topModel.window"],
    "cost.metric.totalCost": ["usage.hero.totalCost"],
    "error.needsReLogin": ["quota.empty.needsLogin.headline"],
    "error.network": ["quota.empty.network.headline"],
    "error.noAccountFound": ["quota.empty.noAccount.headline"],
    "error.parseFailure": ["quota.empty.parseChanged.headline"],
    "onboarding.step.browserCookies.title": ["onboarding.done.browserCookies"],
    "onboarding.step.pricing.title": ["onboarding.done.modelPricing"],
    "onboarding.step.subscriptions.title": ["onboarding.done.subscriptions"],
    "platform.macos.launchAtLogin.title": ["onboarding.done.launchAtLogin"],
    "popover.tab.overview": ["settings.section.overview"],
    "quota.reset.in": ["quota.bucket.resetsIn"],
    "quota.forecast.reset.enough": ["quota.upcoming.forecastAtReset"],
    "settings.pricing.localOverrides": ["settings.pricing.localOverridesName"],
    "status.component.degraded": ["status.overview.degraded"],
    "status.component.maintenance": [
        "status.indicator.maintenance", "status.overview.maintenance",
    ],
    "status.component.operational": ["status.overview.operational"],
    "status.component.partialOutage": ["status.overview.partialOutage"],
}

# ---------------------------------------------------------------------------
# 4. Genuine homonyms: same English, different concept
# ---------------------------------------------------------------------------

# § 4's escape hatch. Each of these is a word English happens to spell once
# and another language may not — the note goes into the surviving key's
# comment so a reviewer sees the claim rather than a silent pass.
# Keyed by the *surviving* key; the partner it is distinct from is worked
# out from the signature groups, because the validator names the first key
# in the group and that is not something a hand-written table can promise to
# keep saying. A group with more than one key and no reason here fails the
# conversion rather than reaching the validator.
DISTINCT_REASONS: Dict[str, str] = {
    "resetHistory.axisNow":
        "a cycle grid's live column and a countdown that reached zero are "
        "different things",
    "resetHistory.window.all":
        "every recorded cycle and an all-time date range are different "
        "spans",
    "quota.forecast.verdict.learning":
        "a verdict about the quota and a confidence level in the forecast "
        "are different judgements",
    "status.component.other":
        "a status component outside the named groups and a quota group "
        "without an L3 name sit on different naming axes (vibe-bar "
        "AGENTS.md 7.1)",
    "settings.section.system":
        "the Settings section for login items and the language option "
        "meaning 'follow the OS language' are different things",
    "settings.section.refreshing":
        "a Settings section for refresh intervals, a button label while a "
        "fetch runs, and a provider's in-flight state are three things",
    "settings.pricing.refreshing":
        "a Settings section for refresh intervals, a button label while a "
        "fetch runs, and a provider's in-flight state are three things",
    "status.overview.refreshing":
        "a Settings section for refresh intervals, a button label while a "
        "fetch runs, and a provider's in-flight state are three things",
    "settings.section.components":
        "the Settings section listing bundled components and a provider "
        "status page's components are different lists",
    "status.card.components":
        "the Settings section listing bundled components and a provider "
        "status page's components are different lists",
    "settings.mcp.status":
        "the local MCP socket's listening state and a provider's service "
        "status are different things",
    "status.overview.title":
        "the local MCP socket's listening state and a provider's service "
        "status are different things",
}

# ---------------------------------------------------------------------------
# 5. Keys this repository already had
# ---------------------------------------------------------------------------

# The default is that the app does not get to change them. A key here is
# already compiled into the other client, and § 2 rule 1 is explicit:
# renaming is breaking, and changing what a key *means* is not allowed at
# all. So a pre-existing key keeps its value unless it is named below.
#
# These two are named because the app's version is the same sentence with
# correct pluralization, which English needs and the existing value fakes.
# Both keep the placeholder *names and types* the other client already
# passes — that is the API, and improving a sentence is not licence to
# change it.
ADOPT_FROM_APP: Dict[str, Dict[str, object]] = {
    "resetHistory.wastedSummary": {
        "why": "adds the plural English needs; keeps {cycles} and its int type",
        "en": "{used}% used · {wasted}% wasted · "
              "{cycles, plural, one {1 cycle} other {# cycles}}",
        "zh-Hans": "已用 {used}% · 浪费 {wasted}% · "
                   "{cycles, plural, other {# 个周期}}",
        "placeholders": {"used": "int", "wasted": "int", "cycles": "int"},
    },
    "resetHistory.laneAverage": {
        "why": "adds the plural English needs; keeps {percent}/{count} as ints",
        "en": "avg wasted {percent}% · last "
              "{count, plural, one {1 cycle} other {# cycles}}",
        "zh-Hans": "平均浪费 {percent}% · 最近 {count, plural, other {# 个周期}}",
        "placeholders": {"percent": "int", "count": "int"},
    },
}

KEY_PATTERN = re.compile(r"^[a-z][a-zA-Z0-9]*(\.[a-z][a-zA-Z0-9]*)+$")


def load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def target_key(key: str) -> Optional[str]:
    if key in DROP:
        return None
    renamed = RENAME.get(key, key)
    if renamed == key:
        for prefix, replacement in NAMESPACE_MOVES:
            if key.startswith(prefix):
                renamed = replacement + key[len(prefix):]
                break
    return renamed


# Filled in by `reconcile_existing`: keys where the app's string lost to the
# one this repository already had. Reported rather than left silent — each
# one is a sentence that changes on the native app's screen at extraction.
REFUSED: Dict[str, Tuple[str, str]] = {}


def convert(app_root: str, problems: List[str]) -> Tuple[dict, dict, dict]:
    app_dir = os.path.join(app_root, "Resources", "i18n")
    source = load(os.path.join(app_dir, "%s.json" % SOURCE_LOCALE))

    # Aliases resolve to their survivor; a survivor that does not exist is a
    # stale table entry, which is worth failing on rather than ignoring.
    alias_of: Dict[str, str] = {}
    for survivor, aliases in COLLAPSE.items():
        for alias in aliases:
            alias_of[alias] = survivor

    converted: Dict[str, dict] = {}
    mapping: Dict[str, str] = {}
    for key in sorted(source):
        renamed = target_key(key)
        if renamed is None:
            continue
        if key in alias_of:
            continue  # the survivor carries this string
        if not KEY_PATTERN.match(renamed):
            problems.append(
                "%s -> %s does not match this repository's key pattern "
                "(dotted lowerCamelCase segments)" % (key, renamed)
            )
            continue
        entry = source[key]
        out = {"value": entry["value"]}
        if entry.get("comment"):
            out["comment"] = entry["comment"]
        # `args` here, `placeholders` there. Same meaning, one name.
        declared = dict(entry.get("args") or {})
        # A placeholder the value uses but never declares defaults to a
        # string on both sides; declaring it explicitly is what the shared
        # schema wants, and what the TypeScript lane types from.
        try:
            used = used_placeholders(parse_icu(entry["value"]))
        except Exception as error:  # reported by the validator in detail
            problems.append("%s: %s" % (key, error))
            continue
        for name in sorted(used):
            declared.setdefault(name, "string")
        stale = sorted(set(declared) - used)
        if stale:
            problems.append(
                "%s declares %s, which its value never uses" % (key, stale)
            )
            continue
        if declared:
            out["placeholders"] = declared
        converted[renamed] = out
        mapping[key] = renamed

    for alias, survivor in alias_of.items():
        if survivor not in converted and target_key(survivor) not in converted:
            problems.append(
                "COLLAPSE: %r is listed as an alias of %r, which the "
                "conversion did not produce" % (alias, survivor)
            )

    # `reconcile_existing` may put this repository's own entry back for a
    # key the app also had; the translation has to follow whichever source
    # value survived, so it runs before the translations are built.
    kept = reconcile_existing(converted, problems)
    annotate_homonyms(converted, problems)

    translations: Dict[str, Dict[str, dict]] = {}
    for locale in LOCALES:
        if locale == SOURCE_LOCALE:
            continue
        raw = load(os.path.join(app_dir, "%s.json" % locale))
        previous_path = os.path.join(CATALOG_DIR, "%s.json" % locale)
        previous = (
            load(previous_path).get("keys", {})
            if os.path.exists(previous_path) else {}
        )
        out: Dict[str, dict] = {}
        for key, renamed in mapping.items():
            if key in raw:
                out[renamed] = {"value": raw[key]["value"]}
        for key in kept:
            if key in previous:
                out[key] = dict(previous[key])
        for key, adopt in ADOPT_FROM_APP.items():
            if key in converted and locale in adopt:
                out[key] = {"value": adopt[locale]}
        translations[locale] = out
    return converted, translations, mapping


def reconcile_existing(converted: dict, problems: List[str]) -> set:
    """Refuse to change a key the other client already calls.

    The conversion maps several app keys onto names this repository already
    used, and the sentences are not always identical — which is the whole
    reason § 4 says the meaning check is on the reviewer. Anything that
    differs and is not declared in `ADOPT_FROM_APP` keeps the existing
    value, so the desktop client's call sites and generated types do not
    move underneath it.
    """
    kept = set()
    path = os.path.join(CATALOG_DIR, "%s.json" % SOURCE_LOCALE)
    if not os.path.exists(path):
        return kept
    existing = load(path).get("keys", {})
    for key, previous in existing.items():
        if key not in converted:
            continue
        adopt = ADOPT_FROM_APP.get(key)
        if adopt:
            converted[key]["value"] = adopt["en"]
            converted[key]["placeholders"] = dict(adopt["placeholders"])
            continue
        incoming = converted[key]
        if incoming.get("value") != previous.get("value") or \
                incoming.get("placeholders", {}) != previous.get("placeholders", {}):
            # Keep this repository's entry, comment and all.
            REFUSED[key] = (incoming.get("value", ""), previous.get("value", ""))
            converted[key] = dict(previous)
            kept.add(key)
    return kept


def annotate_homonyms(converted: dict, problems: List[str]) -> None:
    """Write `distinct-from:` notes, and refuse a duplicate nobody explained.

    The validator groups by reuse signature, names the first key in the
    group, and looks for the note in either key's comment. So the note goes
    on every *other* key in the group and always names the first — worked
    out here rather than hand-written, because a new key joining a group
    changes which key is first and would silently invalidate a fixed note.

    A reason declared on either member covers the pair, which is why the
    reasons read as comparisons rather than as a claim about one side.
    """
    groups: Dict[str, List[str]] = {}
    for key in sorted(converted):
        signature = _reuse_signature(converted[key]["value"])
        if signature:
            groups.setdefault(signature, []).append(key)
    for keys in groups.values():
        if len(keys) < 2:
            continue
        first = keys[0]
        for key in keys[1:]:
            reason = DISTINCT_REASONS.get(key) or DISTINCT_REASONS.get(first)
            if reason is None:
                problems.append(
                    "%s and %s are the same sentence and neither is in "
                    "COLLAPSE or DISTINCT_REASONS; decide which key survives"
                    % (first, key)
                )
                continue
            entry = converted[key]
            note = "distinct-from: %s — %s." % (first, reason)
            comment = entry.get("comment")
            entry["comment"] = "%s %s" % (comment, note) if comment else note


def merge_existing(converted: dict, translations: dict, problems: List[str]) -> dict:
    """Keys this repository already had and the app did not produce.

    Kept rather than dropped: the cross-platform client may already call
    them, and this converter has no standing to retire another client's key.
    """
    kept: Dict[str, List[str]] = {}
    for locale in LOCALES:
        path = os.path.join(CATALOG_DIR, "%s.json" % locale)
        if not os.path.exists(path):
            continue
        existing = load(path).get("keys", {})
        target = converted if locale == SOURCE_LOCALE else translations[locale]
        for key, entry in existing.items():
            if key not in target:
                target[key] = entry
                kept.setdefault(locale, []).append(key)
    return kept


def write(converted: dict, translations: dict) -> None:
    for locale in LOCALES:
        keys = converted if locale == SOURCE_LOCALE else translations[locale]
        document = {
            "$schema": "../schema/catalog.schema.json",
            "locale": locale,
            "keys": {key: keys[key] for key in sorted(keys)},
        }
        path = os.path.join(CATALOG_DIR, "%s.json" % locale)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--app-root", required=True,
        help="checkout of AstroQore/vibe-bar to read Resources/i18n from",
    )
    parser.add_argument(
        "--report", action="store_true",
        help="print what the tables did and write nothing",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    problems: List[str] = []
    converted, translations, mapping = convert(args.app_root, problems)
    refused = REFUSED
    if problems:
        for problem in problems:
            sys.stderr.write("import: %s\n" % problem)
        return 1
    kept = merge_existing(converted, translations, problems)

    if args.report:
        print("dropped (not copy):        %d" % len(DROP))
        print("renamed onto shared names: %d" % len(RENAME))
        print("namespace moves:           %d"
              % sum(1 for k, r in mapping.items() if k != r and k not in RENAME))
        print("duplicates collapsed:      %d aliases into %d survivors"
              % (sum(len(a) for a in COLLAPSE.values()), len(COLLAPSE)))
        print("homonyms kept apart:       %d" % len(DISTINCT_REASONS))
        print("kept from this repository: %s"
              % ", ".join("%s %d" % (loc, len(ks)) for loc, ks in sorted(kept.items())))
        print("keys written:              %d" % len(converted))
        if refused:
            print("\nkept this repository's wording over the app's — each one "
                  "changes what the native app renders, at extraction time:")
            for key, (app_value, ours) in sorted(refused.items()):
                print("  %s\n      app:    %r\n      shared: %r" % (key, app_value, ours))
        return 0

    write(converted, translations)
    print("import: %d keys x %d locales written to catalog/"
          % (len(converted), len(LOCALES)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
