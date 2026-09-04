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

A real run writes two things: `catalog/*.json`, and
`migration/rename-from-app.json` — every app key whose name does not
survive, and the name that replaced it. The app cannot move 177 call sites
off a decision spread over five tables, and a rename it has to infer from a
diff is a rename it will get wrong once. Dropped keys are listed separately
because they have no successor: those call sites format a value instead of
looking a sentence up, which is a code change rather than a rename.

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
MIGRATION_DIR = os.path.join(ROOT, "migration")
RENAME_MAP_PATH = os.path.join(MIGRATION_DIR, "rename-from-app.json")
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
    # `resetHistory.title` here has always meant the side-by-side comparison
    # card — its comment on `main` says so — and the app has now grown the key
    # that actually matches it. So the comparison title maps onto the shared
    # name, and the app's own `quota.resetHistory.title`, which titles a single
    # provider's card, gets a name of its own instead of being flattened onto
    # a key that means something else. This is what the previous import's
    # refusal was standing in for; the shared wording still wins, and the app
    # no longer loses a second card's title to it.
    "quota.resetHistory.compareTitle": "resetHistory.title",
    "quota.resetHistory.title": "resetHistory.cardTitle",
    # One option, keyed twice because the app built two pickers. Collapsing it
    # onto either provider's name would put "Claude" in the key the Codex
    # picker calls, so the survivor is renamed to the name the concept has.
    "settings.usageMode.claude.oauthOnly": "settings.usageMode.oauthOnly",
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
#
# Where neither name is more general — two surfaces, one sentence, no
# `common.*` for it — the survivor is the key the signature group already
# names first, so a collapse and a `distinct-from:` note anchor on the same
# key rather than on two different ones. Two exceptions, both spelled out
# where they sit: a name that is already on `main` cannot move, because the
# other client calls it; and a name that would be actively wrong for the
# surviving concept is renamed in § 2 first and collapsed onto the new name
# here.
#
# The test each entry has to pass: a translator holding both keys' comments
# and nothing else writes the same string for both. When the honest answer
# is "which one?", the pair belongs in § 4 instead.
COLLAPSE: Dict[str, List[str]] = {
    "common.add": ["workbench.skills.discover.addRepo"],
    "common.auto": [
        "cost.granularity.auto", "settings.credentialSource.auto",
        "settings.usageMode.auto", "usage.filters.autoMenu",
        "usage.trend.granularityAuto",
        "settings.layout.studioModeAuto",
    ],
    "common.duration.days": ["quota.freshness.age.days"],
    "common.duration.hours": ["quota.freshness.age.hours"],
    "common.duration.minutes": ["quota.freshness.age.minutes"],
    "common.name": [
        "menuBar.composer.metric.label", "menuBar.composer.preset.name",
    ],
    "common.save": ["menuBar.composer.preset.saveConfirm"],
    "common.off": ["platform.macos.launchAtLogin.off"],
    "common.provider": [
        "menuBar.composer.field.provider", "usage.table.column.provider",
    ],
    "common.remove": ["menuBar.composer.action.remove",
        "settings.layout.studioWellRemove",
    ],
    "common.updated.justNow": ["status.card.updatedJustNow"],
    # --- fourth import: the Layout Studio, whose chrome reuses words the app
    # already had. Casing differs on one ("Mini windows" / "Mini Windows"):
    # the studio's menu section takes the heading's Title Case, which is how
    # macOS spells a menu section anyway.
    "menuBar.composer.preview": [
        "settings.layout.preview", "settings.miniWindow.preview",
    ],
    "onboarding.apiKeys.hide": ["settings.layout.studioWellHide"],
    "settings.section.miniWindows": ["settings.layout.studioSubjectMiniWindows"],
    "usage.trend.fit": ["settings.layout.studioZoomFit"],
    "cost.timeframe.month": ["workbench.sessions.range.month"],
    "cost.timeframe.today": [
        "cost.metric.today", "workbench.sessions.range.today",
    ],
    "cost.timeframe.week": ["workbench.sessions.range.week"],
    "cost.timeframe.yesterday": ["cost.metric.yesterday"],
    "cost.timeframe.weekShort": ["cost.topModel.window"],
    "cost.metric.totalCost": ["usage.hero.totalCost"],
    "error.needsReLogin": ["quota.empty.needsLogin.headline"],
    "error.network": ["quota.empty.network.headline"],
    "error.noAccountFound": ["quota.empty.noAccount.headline"],
    "error.parseFailure": ["quota.empty.parseChanged.headline"],
    # The menu bar is macOS-only, but `menuBar.*` is the home the app itself
    # chose for the composer, and § 4 is explicit that `platform.*` is for what
    # one client *cannot* show rather than for what one client keyed first.
    "menuBar.composer.block.logo": ["platform.macos.menuBar.fieldStyle.logo"],
    "menuBar.composer.colour.forecast": [
        "platform.macos.menuBar.color.forecast",
    ],
    "menuBar.composer.template.compact": [
        "platform.macos.menuBar.layout.compact",
        "settings.layout.studioModeCompact",
    ],
    "menuBar.composer.template.twoRows": [
        "platform.macos.menuBar.layout.twoRows",
    ],
    "onboarding.step.browserCookies.title": ["onboarding.done.browserCookies"],
    "onboarding.step.pricing.title": ["onboarding.done.modelPricing"],
    "onboarding.step.subscriptions.title": ["onboarding.done.subscriptions"],
    "platform.macos.launchAtLogin.title": ["onboarding.done.launchAtLogin"],
    "popover.header.settings": [
        "settings.sidebar.settings", "workbench.page.settings.title",
    ],
    "popover.tab.overview": [
        "platform.macos.menuBar.overview", "settings.section.overview",
    ],
    "quota.reset.in": ["quota.bucket.resetsIn"],
    "quota.forecast.reset.enough": ["quota.upcoming.forecastAtReset"],
    "quota.forecast.metric.forecastAtReset": [
        "menuBar.composer.metric.forecastPercent",
    ],
    "quota.history.moreReadings": [
        "workbench.resets.calendar.moreEntries",
    ],
    # `quota.remainingPercent` is one of the 23 keys that were here before the
    # app's catalogue arrived, so it is the name that cannot move.
    "quota.remainingPercent": ["quota.mini.forecastLeft"],
    "settings.pricing.localOverrides": ["settings.pricing.localOverridesName"],
    "settings.usageMode.oauthOnly": ["settings.usageMode.codex.oauthOnly"],
    "status.component.degraded": ["status.overview.degraded"],
    "status.component.maintenance": [
        "status.indicator.maintenance", "status.overview.maintenance",
    ],
    # `status.summary.*` spells the same two states in sentence case. That is
    # the same incidental drift `status.overview.partialOutage` already had,
    # and the same resolution: one vocabulary, title case, dropped in verbatim
    # at the call site. Nothing has to learn to change case.
    "status.component.majorOutage": ["status.summary.majorOutage"],
    "status.component.operational": ["status.overview.operational"],
    "status.component.partialOutage": [
        "status.overview.partialOutage", "status.summary.partialOutage",
    ],
    "usage.breakdown.models": [
        "usage.filters.modelsMenu", "usage.mix.dimension.models",
    ],
    "usage.breakdown.projects": ["usage.mix.dimension.projects"],
    "usage.filters.allHarnessesHelpNone": [
        "workbench.sessions.allChip.helpSelected",
    ],
    "usage.filters.allHarnessesSelectNone": [
        "workbench.sessions.allChip.labelSelected",
    ],
    # A donut slice's comment says what the number *is*; a column header's says
    # where it sits. A translator needs the first, so the slice is the survivor
    # even though the column is the older key.
    "usage.mix.flow.cache": ["usage.table.column.cache"],
    "usage.mix.flow.output": ["usage.table.column.output"],
    "usage.table.column.harness": ["workbench.sessions.filter.harness"],
}

# ---------------------------------------------------------------------------
# 4. Genuine homonyms: same English, different concept
# ---------------------------------------------------------------------------

# § 4's escape hatch. Each of these is a word English happens to spell once
# and another language may not — the note goes into the surviving key's
# comment so a reviewer sees the claim rather than a silent pass.
#
# Five kinds of pair end up here, and each reason says which:
#
# *   The referent differs. A bucket one day wide is a duration; a period
#     column's header names a date. Simplified Chinese already writes 天 and
#     日 for that pair, which is what a second language quietly disagreeing
#     with a collapse looks like before anyone notices.
# *   One is a value and the other a command. A countdown reading "now" and
#     a button that jumps the chart back to now are two words English
#     spells alike, not one word doing two jobs.
# *   The same adjective on two different axes. "Compact" agrees with a
#     different noun in every language that inflects one, so a menu-bar
#     template and a popover density cannot share a key.
# *   One spelling is a rendering the app sets on purpose — an all-caps hero
#     metric, a lowercase caption under a bar. Collapsing those moves the
#     casing into a call site, which is a code change and not a rename, and
#     casing is not something a catalogue can do on a client's behalf
#     anyway: Turkish is the standing reminder.
# *   The value is a joiner: two placeholders and a separator. Its
#     placeholder names are the only part carrying meaning, so one key
#     cannot serve six different pairs of values.
# Keyed by the *surviving* key; the partner it is distinct from is worked
# out from the signature groups, because the validator names the first key
# in the group and that is not something a hand-written table can promise to
# keep saying. A group with more than one key and no reason here fails the
# conversion rather than reaching the validator.
DISTINCT_REASONS: Dict[str, str] = {
    # --- third import ---------------------------------------------------
    "common.duration.full.hoursMinutes":
        "the same shape with different placeholders: one takes days and "
        "hours, the other hours and minutes, so one key could not "
        "declare both argument sets",
    "menuBar.composer.resetFormat.automatic":
        "a colour that follows the strip and a date format that follows "
        "the day are different automatics, and their pickers should be "
        "free to phrase them apart",
    "resetHistory.axis.time":
        "a reset-time format showing only the clock and a chart axis "
        "measured in time are different things",
    "popover.header.mini":
        "the smallest block size and the floating window are different "
        "words already: zh-Hans says 迷你 for one and 迷你窗口 for the "
        "other",

    # --- carried over from the first import ----------------------------
    "resetHistory.axisNow":
        "a cycle grid's live column and a countdown that reached zero "
        "are different things",
    "resetHistory.window.all":
        "every recorded cycle and an all-time date range are different "
        "spans",
    "quota.forecast.verdict.learning":
        "a verdict about the quota and a confidence level in the "
        "forecast are different judgements",
    "status.component.other":
        "a status component outside the named groups and a quota group "
        "without an L3 name sit on different naming axes (vibe-bar "
        "AGENTS.md 7.1)",
    "settings.section.system":
        "the Settings section for login items and the language option "
        "meaning 'follow the OS language' are different things",
    "settings.section.refreshing":
        "a Settings section for refresh intervals, a button label while "
        "a fetch runs, and a provider's in-flight state are three "
        "things",
    "settings.pricing.refreshing":
        "a Settings section for refresh intervals, a button label while "
        "a fetch runs, and a provider's in-flight state are three "
        "things",
    "status.overview.refreshing":
        "a Settings section for refresh intervals, a button label while "
        "a fetch runs, and a provider's in-flight state are three "
        "things",
    "settings.section.components":
        "the Settings section listing bundled components and a provider "
        "status page's components are different lists",
    "status.card.components":
        "the Settings section listing bundled components and a provider "
        "status page's components are different lists",
    "settings.mcp.status":
        "the local MCP socket's listening state and a provider's "
        "service status are different things",
    "status.overview.title":
        "the local MCP socket's listening state and a provider's "
        "service status are different things",
    # --- a value on one screen, a command on another --------------------
    "usage.trend.now":
        "a countdown that has reached zero is a value; the chart's Now "
        "button is a command that jumps the window back to the present",
    "workbench.resets.calendar.today":
        "a timeframe covering today is a value; the calendar's Today "
        "button is a command that returns the grid to the current month",
    # --- the same word, a different referent ----------------------------
    "cost.timeframe.all":
        "a filter chip that selects every item and a timeframe covering "
        "all recorded history are different spans",
    "settings.credentialSource.off":
        "a toggle's state read back in a summary row and a picker "
        "option meaning 'do not fetch at all' are different things — "
        "Simplified Chinese already writes 已关闭 and 关闭",
    "usage.table.column.day":
        "a chart bucket one day wide is a duration; a period column's "
        "header names the date its row covers — Simplified Chinese "
        "already writes 天 and 日",
    "usage.table.column.hour":
        "a chart bucket one hour wide is a duration; a period column's "
        "header names the hour its row covers, which is the same split "
        "Simplified Chinese already spells out for Day",
    "onboarding.pricing.interval.hour":
        "a chart's bucket width is a picker option; a refresh interval "
        "of one hour is read inside a sentence",
    "quota.history.forecastLegend":
        "a colour-basis option naming what the colour follows and a "
        "chart legend naming the projection line are different things",
    "quota.history.tooltipPace":
        "the metric is how far usage has drifted from the linear "
        "expectation; the tooltip row is that expectation itself — "
        "Simplified Chinese already writes 消耗速度 and 进度",
    "settings.miniWindow.mode.strip":
        "the label over the menu-bar mode control and a mini-window "
        "layout named Strip are different things",
    "platform.macos.menuBar.fieldStyle.label":
        "the starting text of a new block, which the user types over, "
        "and a field style that shows the field's name are different "
        "things — Simplified Chinese already writes 标签 and 名称",
    "settings.section.layout":
        "the menu-bar item's own arrangement and the Settings section "
        "about page layout are different things — Simplified Chinese "
        "already writes 排布 and 布局",
    "usage.filters.refreshInterval":
        "an age — how long ago a reading was taken — and an interval — "
        "how often to poll — are different quantities",
    "usage.filters.allModels":
        "a quota group covering every model on a plan and a menu item "
        "that clears the model filter are different things",
    "usage.trend.granularityDaily":
        "a quota window's group label and a chart's bucket width are "
        "different things",
    "usage.trend.granularityWeekly":
        "a quota window's group label and a chart's bucket width are "
        "different things",
    "usage.mix.other":
        "a quota bucket with no group of its own and a donut slice that "
        "collapses everything below the top five are different "
        "remainders",
    "usage.table.column.time":
        "a segmented control that places cycles on a shared calendar "
        "and a request's timestamp column are different things",
    "usage.table.column.input":
        "a price per million input tokens and a count of input tokens "
        "are different quantities",
    "usage.mix.flow.output":
        "a price per million output tokens and a count of output tokens "
        "are different quantities",
    "usage.table.column.requests":
        "the breakdown tab lists one row per request; the table column "
        "counts them — Simplified Chinese already writes 请求 and 请求数",
    "usage.tokens.title":
        "the lowercase unit under a donut's total and the name of the "
        "token metric are different words in the sentences they sit in",
    "workbench.resets.risk.badge.out":
        "'Out' is the output-token legend there and 'has run out' here "
        "— Simplified Chinese already writes 输出 and 耗尽",
    "workbench.skills.wiring.source":
        "a session log's path on disk and a skill's one real copy are "
        "different sources — Simplified Chinese already writes 来源文件 and "
        "源目录",
    "workbench.status.updated":
        "a toast naming the skill it re-fetched and a header line "
        "naming the time it refreshed are different sentences",
    # --- an ellipsis is the promise that a confirmation follows ---------
    "workbench.sessions.deleteEllipsis":
        "the ellipsis promises a confirmation; the plain button is the "
        "confirmed action itself",
    "workbench.skills.uninstall":
        "the menu item's ellipsis promises the confirmation dialog; "
        "this is the destructive button inside it",
    "menuBar.composer.mode.custom":
        "a colour the user picks, whose ellipsis promises the well that "
        "opens, and the strip the user assembles are different things",
    # --- the same adjective on two different axes -----------------------
    "usage.filters.rangeCustom":
        "a colour the user picks and a date range the user picked are "
        "options on different axes; the adjective agrees with a "
        "different noun in every language that inflects one",
    "menuBar.composer.weight.regular":
        "Regular names a type weight in one list and a type size in the "
        "other, which are options on different axes; the adjective "
        "agrees with a different noun in every language that inflects "
        "one",
    "settings.miniWindow.mode.regular":
        "a type size and a mini-window layout are options on different "
        "axes; the adjective agrees with a different noun in every "
        "language that inflects one",
    "settings.popoverDensity.regular":
        "a type size and a popover density are options on different "
        "axes; the adjective agrees with a different noun in every "
        "language that inflects one",
    "settings.miniWindow.mode.compact":
        "a menu-bar template and a mini-window layout are options on "
        "different axes; the adjective agrees with a different noun in "
        "every language that inflects one",
    "settings.popoverDensity.compact":
        "a menu-bar template and a popover density are options on "
        "different axes; the adjective agrees with a different noun in "
        "every language that inflects one",
    "settings.miniWindow.density.roomy":
        "a menu-bar template and a mini-window strip density are "
        "options on different axes; the adjective agrees with a "
        "different noun in every language that inflects one",
    # --- one spelling is a rendering the app sets on purpose ------------
    "settings.displayMode.remaining":
        "the lowercase caption under a bar and the title-case picker "
        "option are two renderings the app sets deliberately; "
        "collapsing them would move the casing into a call site, which "
        "is a code change rather than a rename",
    "settings.displayMode.used":
        "the lowercase caption under a bar and the title-case picker "
        "option are two renderings the app sets deliberately; "
        "collapsing them would move the casing into a call site, which "
        "is a code change rather than a rename",
    "usage.hero.requests":
        "the breakdown tab lists one row per request; the hero metric "
        "counts them in all caps, and the two spellings are two "
        "renderings the app sets deliberately; collapsing them would "
        "move the casing into a call site, which is a code change "
        "rather than a rename",
    "usage.mix.harness.title":
        "the all-caps section label and the title-case donut card title "
        "are two renderings the app sets deliberately; collapsing them "
        "would move the casing into a call site, which is a code change "
        "rather than a rename",
    "workbench.resets.risk.badge.watch":
        "the forecast verdict is read as a sentence and the risk badge "
        "is all caps, and the two spellings are two renderings the app "
        "sets deliberately; collapsing them would move the casing into "
        "a call site, which is a code change rather than a rename",
    # --- a joiner whose only words are its placeholder names ------------
    "popover.machines.labelWithStatus":
        "the value is two placeholders and a separator, so the "
        "placeholder names are the only part of it that carries "
        "meaning; one key cannot name both halves of six different "
        "pairs",
    "settings.remote.statusWithCode":
        "the value is two placeholders and a separator, so the "
        "placeholder names are the only part of it that carries "
        "meaning; one key cannot name both halves of six different "
        "pairs",
    "usage.filters.companyHelp":
        "the value is two placeholders and a separator, so the "
        "placeholder names are the only part of it that carries "
        "meaning; one key cannot name both halves of six different "
        "pairs",
    "usage.table.harnessHelp":
        "the value is two placeholders and a separator, so the "
        "placeholder names are the only part of it that carries "
        "meaning; one key cannot name both halves of six different "
        "pairs",
    "usage.yearHeatmap.tooltip":
        "the value is two placeholders and a separator, so the "
        "placeholder names are the only part of it that carries "
        "meaning; one key cannot name both halves of six different "
        "pairs",
    "workbench.sessions.filter.harnessCount":
        "a legend marker's name and the figure it marks, and a harness "
        "with its session count, are different pairs; the placeholder "
        "names are the sentence",
    "workbench.sessions.fraction":
        "a page counter and the Sessions page's general-purpose counter "
        "count different things; the placeholder names are the sentence",
    "usage.trend.showProvider":
        "a page label is translated and sits tight against 显示; a "
        "provider name stays Latin and takes a space around it — "
        "Simplified Chinese already writes the two differently",
    "workbench.header.refreshPage":
        "a provider name stays Latin and takes a space in Simplified "
        "Chinese; a page title is translated and does not",
}

# ---------------------------------------------------------------------------
# 5. Comments a collapse made untrue
# ---------------------------------------------------------------------------

# A survivor keeps the app's comment, and for almost every collapse that is
# fine: the comment says what the string *means*, which is what a translator
# needs, and naming one of the surfaces it appears on costs nothing. These two
# are the exceptions — each would make a translator believe something about the
# string that stopped being true when the other key folded into it.
COMMENT_OVERRIDE: Dict[str, str] = {
    "settings.usageMode.oauthOnly":
        "Usage-source option offered for both Claude and Codex: read usage "
        "over OAuth only. The app keyed it twice, once per picker.",
    "usage.table.column.harness":
        "Column header on the Requests table, where the call site uppercases "
        "it, and the Sessions filter menu that ticks individual harnesses, "
        "where it is read as written. 'Harness' is the usage-axis unit and "
        "stays as spelled.",
}

# ---------------------------------------------------------------------------
# 6. Keys this repository already had
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
# Keys where this repository's *meaning* is wider than the app's use of it,
# so the shared comment and the shared translation both stand.
#
# The app's comment describes one call site; a shared key's comment describes
# what every client uses it for, and a translator reading the narrow one
# translates the narrow thing. This is the same rule as § 7's — the shared
# name wins — applied to the metadata rather than the sentence, and it is
# declared here rather than inferred because "the app's is newer" is true of
# the register pass and false of this.
# Spelled out rather than read off disk: an earlier import already rewrote
# `catalog/` in place, so the working tree is no longer the baseline it looks
# like. A declaration that depends on the state it is meant to restore is not
# a declaration.
KEEP_SHARED_MEANING: Dict[str, Dict[str, str]] = {
    "common.noData": {
        "why": "a generic empty state for any chart or list; the app comments "
               "it as one observation tooltip, and 暂无记录 reads for both "
               "uses where 无记录数据 reads for neither",
        "comment": 'Empty state where a chart or list has nothing to draw yet.',
        "en": 'No data recorded',
        "zh-Hans": '暂无记录',
    },
}

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

# The other direction, and the quieter one. `reconcile_existing` compares
# source values, so a key whose English both catalogues agree on takes the
# app's *translation* without anyone being asked. That is usually right —
# the app has 67 Chinese strings saying 额度 and this repository had two
# older ones saying 配额, and freezing the two would leave the odd pair out.
# It is not right often enough to be silent about, so it is reported too:
# locale, key, what this repository said, what the app says.
TRANSLATION_REPLACED: Dict[str, List[Tuple[str, str, str]]] = {}


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
    for key, comment in COMMENT_OVERRIDE.items():
        if key not in converted:
            problems.append(
                "COMMENT_OVERRIDE: %r is not a key the conversion produced"
                % key
            )
            continue
        converted[key]["comment"] = comment
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
                was = previous.get(renamed, {}).get("value")
                if renamed in ADOPT_FROM_APP:
                    was = None  # declared in § 7, and overwritten below
                if renamed in KEEP_SHARED_MEANING:
                    was = None  # this repository's value is restored below
                if was is not None and was != raw[key]["value"]:
                    TRANSLATION_REPLACED.setdefault(locale, []).append(
                        (renamed, was, raw[key]["value"])
                    )
        for key in kept:
            if (shared := KEEP_SHARED_MEANING.get(key)) is not None:
                if locale in shared:
                    out[key] = {"value": shared[locale]}
            elif key in previous:
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
        if (shared := KEEP_SHARED_MEANING.get(key)) is not None:
            converted[key]["value"] = shared["en"]
            converted[key]["comment"] = shared["comment"]
            kept.add(key)
            continue
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


def rename_map(app_root: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Every app key whose name does not survive, and what replaced it.

    The app has to move its call sites when the catalogue leaves it, and a
    list of decisions spread across four tables is not something a call site
    can be moved by. So the tables are collapsed into one map the app can
    apply mechanically: old key on the left, the name that survives on the
    right. Drops are reported separately because they have no survivor — a
    dropped key is a call site that has to start formatting instead of
    looking a sentence up, which is a code change and not a rename.
    """
    source = load(
        os.path.join(app_root, "Resources", "i18n", "%s.json" % SOURCE_LOCALE)
    )
    alias_of: Dict[str, str] = {}
    for survivor, aliases in COLLAPSE.items():
        for alias in aliases:
            alias_of[alias] = survivor
    renames: Dict[str, str] = {}
    dropped: Dict[str, str] = {}
    for key in sorted(source):
        if key in DROP:
            dropped[key] = DROP[key]
            continue
        if key in alias_of:
            survivor = alias_of[key]
            renames[key] = target_key(survivor) or survivor
            continue
        renamed = target_key(key)
        if renamed and renamed != key:
            renames[key] = renamed
    return renames, dropped


def write_rename_map(renames: Dict[str, str], dropped: Dict[str, str]) -> None:
    document = {
        "$comment": (
            "Produced by scripts/import_from_app.py. Left: a key "
            "AstroQore/vibe-bar's own Resources/i18n used. Right: the name "
            "it has in this catalogue. `dropped` has no right-hand side: "
            "those keys are formatting rather than copy (AGENTS.md 3), and "
            "each call site formats the value instead of looking it up."
        ),
        "renames": renames,
        "dropped": dropped,
    }
    os.makedirs(MIGRATION_DIR, exist_ok=True)
    with open(RENAME_MAP_PATH, "w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")


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
        renames, drops = rename_map(args.app_root)
        print("rename map:                %d renames, %d drops"
              % (len(renames), len(drops)))
        if refused:
            print("\nkept this repository's wording over the app's — each one "
                  "changes what the native app renders, at extraction time:")
            for key, (app_value, ours) in sorted(refused.items()):
                print("  %s\n      app:    %r\n      shared: %r" % (key, app_value, ours))
        for locale in sorted(TRANSLATION_REPLACED):
            print("\ntook the app's %s over this repository's, on keys whose "
                  "English both agree on:" % locale)
            for key, was, now in sorted(TRANSLATION_REPLACED[locale]):
                print("  %s\n      shared: %r\n      app:    %r" % (key, was, now))
        return 0

    write(converted, translations)
    renames, drops = rename_map(args.app_root)
    write_rename_map(renames, drops)
    print("import: %d keys x %d locales written to catalog/"
          % (len(converted), len(LOCALES)))
    print("import: %d renames and %d drops written to %s"
          % (len(renames), len(drops), os.path.relpath(RENAME_MAP_PATH, ROOT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
