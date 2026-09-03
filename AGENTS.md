# AGENTS.md — Vibe Bar Localization Catalog

This file is the operating manual for AI agents and humans working on this
repository. It is **self-contained**: read it top to bottom and you can add a
key, add a language, or wire a new client without asking anyone.

## 1. What this repository is

One catalog of user-facing strings, shared by every Vibe Bar client:

| Repo | Client | Consumes this via |
| --- | --- | --- |
| `AstroQore/vibe-bar` | macOS native (Swift, AppKit/SwiftUI) | SwiftPM dependency, exact-pinned tag |
| `AstroQore/vibe-bar-desktop` | Cross-platform (Tauri 2 + Rust + React) | npm package, exact-pinned version |

It holds **strings only**. It has no product logic, no quota maths, no
provider adapters. If a change here needs a code change there, the change
here is probably wrong.

The layout mirrors `AstroQore/agent-session-kit`, which solved the same
problem for session storage: one repository, sibling implementation lanes,
and shared facts that both lanes must honour. Both manifests live at the
repository root and point into `implementations/` with explicit paths, so a
consumer's dependency URL never mentions a lane.

```text
catalog/
  en.json            # source of truth — the only file where a string is authored
  zh-Hans.json       # translations
  _glossary.json     # proper nouns that must never be translated
schema/              # JSON Schema for both file shapes
scripts/             # validate + generate, pure functions of catalog/
implementations/
  swift/             # generated .strings/.stringsdict + typed Swift API
  typescript/        # generated key union + ICU runtime
Package.swift        # root manifest, points into implementations/swift
package.json         # root manifest, points into implementations/typescript
```

## 2. The rules that matter

These are the ones that cause silent breakage months later. The rest of this
file is detail.

1. **A key is an identifier, never an English sentence.** `quota.resetsIn`,
   not `"Resets in %@"`. Renaming a key is a breaking change; changing what a
   key *means* is not allowed at all — add a new key and retire the old one.
2. **Placeholders are named, never positional.** Author `{provider}` and
   `{days}`. The Swift generator converts them to positional `%@` / `%lld`
   for `.strings` **and** emits a typed function so call sites pass named
   arguments and never see the order. Positional authoring is exactly what
   breaks when a translation reorders a sentence, and the TypeScript client
   cannot consume `%@` at all.
3. **Only `en.json` declares `comment` and `placeholders`.** Translations
   carry `value` and nothing else. Two files declaring the same metadata is
   two files that will disagree.
4. **The glossary is data, not a convention.** Company, SubProvider, product,
   model and harness names live in `catalog/_glossary.json`. A term that
   appears in a source string must appear verbatim in every translation of
   that string; CI enforces it. This is how "don't translate brand names"
   stops being folklore.
5. **A sentence the app builds by concatenation is a bug.** Chinese word
   order does not survive `"Resets in " + duration`. One key, placeholders
   inside it.
6. **Generated files are checked in.** `implementations/**/Generated/**` is
   produced by `scripts/`, committed, and verified by CI. A consumer must not
   need Python to build.
7. **Nothing in a client reads `catalog/*.json` at runtime.** Clients call
   the generated API. That is what lets this repository change shape without
   touching a call site.

## 3. Catalog format

`catalog/en.json` — the source locale:

```json
{
  "$schema": "../schema/catalog.schema.json",
  "locale": "en",
  "keys": {
    "common.retry": {
      "value": "Retry",
      "comment": "Button that runs the failed action again."
    },
    "quota.resetsIn": {
      "value": "resets in {days}d {hours}h",
      "comment": "Countdown beside a quota bar. Both values are whole numbers.",
      "placeholders": { "days": "int", "hours": "int" }
    }
  }
}
```

`catalog/zh-Hans.json` — a translation:

```json
{
  "$schema": "../schema/catalog.schema.json",
  "locale": "zh-Hans",
  "keys": {
    "common.retry": { "value": "重试" },
    "quota.resetsIn": { "value": "{days} 天 {hours} 小时后重置" }
  }
}
```

**Key namespaces.** `common.*`, `menuBar.*`, `popover.*`, `quota.*`,
`resetHistory.*`, `cost.*`, `session.*`, `settings.*`, `workbench.*`,
`onboarding.*`, `error.*`. A string only one client can ever show goes under
`platform.macos.*` or `platform.desktop.*` — use it sparingly; a key that
needs a platform prefix is usually a key that needs rethinking.

**Placeholder types** are `string`, `int` and `double`. Formatting a
percentage, a currency or a date is the client's job — the catalog receives
the formatted value or the raw number, never a pre-formatted sentence. Money
is always `string`: the symbol, the grouping and which side it sits on are
the platform's answer, not the catalog's.

A key with two or more placeholders generates **indexed** specifiers
(`%1$lld`, `%2$lld`) rather than bare ones, because a translation that
reorders the sentence is the normal case, not the exception. A single
placeholder keeps the plain form, which is what `.stringsdict` expects.

Only `{name}` and `{name, plural, …}` are accepted. `select`,
`selectordinal` and `offset:` are rejected by validation: `.stringsdict`
cannot express `offset:`, and the other two would degrade differently on
each client, which is worse than not having them.

**Plurals** use ICU:

```json
"quota.cyclesRecorded": {
  "value": "{count, plural, one {# cycle} other {# cycles}}",
  "comment": "How many completed reset cycles a lane has.",
  "placeholders": { "count": "int" }
}
```

Simplified Chinese has only `other`; English needs `one` and `other`. The
Swift generator emits `.stringsdict` for any key whose value contains a
plural.

## 4. Adding a key

1. Add it to `catalog/en.json` with a `comment` that says where it appears —
   a translator cannot see your screen.
2. Add it to every other locale. `python3 scripts/validate.py` fails on a
   missing key; that is deliberate. If a translation is genuinely not ready,
   ship the key with the English text as its value rather than omitting it,
   so the client renders something and the gap is visible in the diff.
3. `python3 scripts/generate.py` to refresh the generated files.
4. `python3 scripts/validate.py` — key parity, placeholder parity, ICU
   syntax, glossary compliance, schema, and generated-file freshness.
5. Commit the catalog change and the regenerated output together.

## 5. Adding a language

1. `catalog/<bcp47>.json` with the same keys, `locale` matching the filename.
2. Register it in `scripts/generate.py`'s locale list if it is not derived
   from the directory listing.
3. Regenerate; the Swift lane gains `<locale>.lproj`, the TypeScript lane a
   new module.
4. The consuming clients need one change each: `CFBundleLocalizations` in the
   native app's `Info.plist`, and the language picker's option list in both.

## 6. Translation style

Written for Simplified Chinese first; the same spirit applies to any locale.

- Terse UI Chinese, not literal translation. A label is a label, not a
  sentence: `重试`, not `请重试一次`.
- No full stop at the end of a short label or button. Sentences in help text
  and errors do take one.
- Keep glossary terms in English inline: `Claude 每周`, `Gemini Web 用量`.
- Numbers, units and dates follow Chinese convention: `3 天 18 小时`,
  `¥12.34`, `9月4日`.
- An error says what happened and what to do, in that order, in one
  sentence. No apology.
- If a string cannot be translated well because the English is built from
  fragments, fix the key — do not translate the fragments.

## 7. Versioning and release

Semantic versioning on tags (`v0.3.1`).

- **Adding** a key or a locale: minor.
- **Changing a translation** (same meaning, better wording): patch.
- **Changing what a key means, renaming, or removing** one: major — and
  prefer adding a new key over changing an existing one, so consumers can
  migrate on their own schedule.
- Consumers pin exactly, matching the `agent-session-kit` convention: the
  native app pins the tag in `Package.swift`, the desktop app pins the
  version in `package.json`.

`package.json`'s `version` and the git tag are the same number. Bump both in
the release commit; CI fails if they disagree, because two version numbers
with no single source of truth is how an npm consumer ends up pinned to a
version that never existed.

A release is: validate clean, regenerate clean, bump both versions, tag,
push. There is no build artefact to upload — the generated files are in the
tree.

**Licence.** MIT, deliberately, while `vibe-bar` itself is AGPL-3.0-only: a
permissive catalogue is a normal dependency of a copyleft app, and a
translator contributing a language should not have to reason about copyleft
to do it.

## 8. Consuming it

**Swift** (native app):

```swift
.package(url: "https://github.com/AstroQore/vibe-bar-i18n.git", exact: "0.1.0")
```

```swift
Text(L10n.Common.retry)
Text(L10n.Quota.resetsIn(days: 3, hours: 18))
```

Strings resolve through the standard bundle lookup, so the app follows the
macOS language automatically; an in-app override sets the bundle explicitly.

**TypeScript** (desktop app):

```jsonc
"@astroqore/vibe-bar-i18n": "0.1.0"
```

```ts
import { t } from "@astroqore/vibe-bar-i18n";
t("common.retry");
t("quota.resetsIn", { days: 3, hours: 18 });
```

Keys are a union type, so a typo is a compile error and a missing
placeholder is a compile error.

## 9. What does not belong here

- Provider, model or harness names — those come from each client's own
  naming source and are listed in the glossary only so they are protected
  from translation.
- Anything in `vibe-bar`'s `docs/contracts/` — those are generated from Swift
  and describe behaviour, not wording.
- Log messages, MCP tool names, JSON keys, file paths, and anything a machine
  parses. Localizing a machine-readable string breaks the machine.
- Marketing copy, README text, release notes.
