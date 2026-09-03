# vibe-bar-i18n

Every user-facing string in
[Vibe Bar](https://github.com/AstroQore/vibe-bar) and
[Vibe Bar Desktop](https://github.com/AstroQore/vibe-bar-desktop), authored
once in `catalog/en.json` and consumed by both — the macOS menu-bar app
through SwiftPM, the cross-platform client through npm. Strings only: no
product logic lives here.

`catalog/` is the source. `implementations/swift` and
`implementations/typescript` are generated from it by `scripts/generate.py`
and checked in, so neither consumer needs Python to build.

[AGENTS.md](AGENTS.md) is the operating manual — key naming, translation
style, versioning, what does not belong here. This file is just the parts
you need on the way in.

## Use it

**Swift**

```swift
.package(url: "https://github.com/AstroQore/vibe-bar-i18n.git", exact: "0.1.0")
```

```swift
Text(L10n.Common.retry)
Text(L10n.Quota.resetsIn(days: 3, hours: 18))
Text(L10n.Quota.cyclesRecorded(count: 2))
```

Strings resolve through the standard bundle lookup, so the app follows the
macOS language automatically. To offer an in-app language picker, set the
override — it takes effect on the next read, no relaunch:

```swift
L10n.availableLocales        // ["en", "zh-Hans"]
L10n.localeOverride = "zh-Hans"   // force a language
L10n.localeOverride = nil         // back to the system language
```

`"zh"`, `"zh-Hans"` and `"zh-Hans-CN"` all resolve to `zh-Hans`; a tag this
package does not ship falls back to the system language rather than to raw
keys.

With no override, the language comes from `Locale.preferredLanguages`
matched against `L10n.availableLocales` — deliberately *not* from
`Bundle.module.preferredLocalizations`, which CFBundle constrains to the
host app's `CFBundleLocalizations` and which therefore pins this package to
English in a host that has not listed the language. Still add the tag to
the app's `Info.plist`: that is what drives the system's own per-app
language picker.

**TypeScript**

```jsonc
"@astroqore/vibe-bar-i18n": "0.1.0"
```

```ts
import { t, setLocale } from "@astroqore/vibe-bar-i18n";

t("common.retry");
t("quota.resetsIn", { days: 3, hours: 18 });
t("quota.cyclesRecorded", { count: 2 });

setLocale("zh-Hans"); // returns false for a locale the catalog does not ship
```

Keys are a union type, so a typo is a compile error and a missing or
invented placeholder is a compile error. The package ships TypeScript
sources with no dependencies and no build step; your bundler compiles them.

## Add a key

1. Add it to `catalog/en.json` with a `comment` saying where it appears, and
   to every other locale. Placeholders are named — `{days}`, never `%@`.
2. `python3 scripts/generate.py`
3. `python3 scripts/validate.py`
4. Commit the catalog change and the regenerated files together.

`validate.py` is the gate: schema, key parity, placeholder parity, ICU
plural syntax, glossary compliance, and whether the generated files match
the catalog. It names the file, the key and the problem.

## Add a language

1. `catalog/<bcp47>.json` — same keys, `locale` matching the file name, no
   `comment` or `placeholders` (those live in the source locale only).
2. `python3 scripts/generate.py && python3 scripts/validate.py`. The Swift
   lane gains a `<locale>.lproj`; the TypeScript lane gains a column in the
   generated catalog. No script edit is needed — locales come from the
   directory listing.
3. In the consuming apps: add the tag to `CFBundleLocalizations` in the
   native app's `Info.plist`, and to each language picker's option list.

## Work on it

```sh
python3 scripts/validate.py      # everything, including generated freshness
python3 scripts/generate.py      # rewrite both lanes; deterministic
swift build && swift test        # Swift lane
npm test                         # TypeScript lane (no install needed)
npm run typecheck                # needs npx to fetch tsc
```

Only Python 3.9+ is required for the scripts — the standard library, no
packages, so the version macOS ships is enough.

## License

MIT. See [LICENSE](LICENSE).
