// Hand-written. The public surface of the TypeScript lane; everything
// under `generated/` is produced by `scripts/generate.py`.
//
// Relative imports carry a `.js` extension, which is the TypeScript
// convention for `NodeNext`/`bundler` resolution and the only spelling
// that keeps a consumer's `tsc --noEmit` green while this package ships
// its sources. (`./generated/catalog.ts` would raise TS5097 in the
// consuming project — measured, not assumed.)

import { formatMessage, type ParamValue } from "./icu.js";
import {
  locales,
  messages,
  sourceLocale,
  type Locale,
  type MessageKey,
  type MessageParams,
} from "./generated/catalog.js";

export { locales, sourceLocale };
export type { Locale, MessageKey, MessageParams };

/**
 * Keys whose value contains at least one placeholder. `t` requires a
 * parameter object for exactly these.
 */
type KeysWithParams = {
  [K in MessageKey]: MessageParams[K] extends undefined ? never : K;
}[MessageKey];

/** Keys with no placeholders. `t` takes no second argument for these. */
type KeysWithoutParams = Exclude<MessageKey, KeysWithParams>;

// --------------------------------------------------------------------------
// Current locale
// --------------------------------------------------------------------------

let current: Locale = detectLocale();

/** The locale `t` is currently resolving against. */
export function getLocale(): Locale {
  return current;
}

/**
 * Choose the interface language.
 *
 * Takes any BCP 47 tag and matches it leniently — `"zh"`, `"zh-Hans"` and
 * `"zh-Hans-CN"` all select `zh-Hans`. A tag the catalog does not ship
 * leaves the current locale untouched and returns `false`, so a stale
 * preference cannot blank the UI.
 */
export function setLocale(locale: Locale | (string & {})): boolean {
  const matched = matchLocale(locale);
  if (matched === undefined) return false;
  current = matched;
  return true;
}

/** The shipped locale a tag corresponds to, or `undefined`. */
export function matchLocale(tag: string): Locale | undefined {
  const wanted = tag.replace(/_/g, "-").toLowerCase();
  const exact = locales.find((locale) => locale.toLowerCase() === wanted);
  if (exact) return exact;

  const segments = wanted.split("-");
  while (segments.length > 1) {
    segments.pop();
    const prefix = segments.join("-");
    const shorter = locales.find((locale) => locale.toLowerCase() === prefix);
    if (shorter) return shorter;
  }
  // A bare language still finds a scripted locale: `zh` -> `zh-Hans`.
  const language = wanted.split("-")[0] ?? "";
  return locales.find((locale) => locale.toLowerCase().startsWith(`${language}-`));
}

/** Narrowing helper for values that come out of storage as `string`. */
export function isLocale(value: string): value is Locale {
  return (locales as readonly string[]).includes(value);
}

function detectLocale(): Locale {
  const navigatorLike = (globalThis as { navigator?: { language?: string } }).navigator;
  const preferred = navigatorLike?.language;
  return (preferred !== undefined ? matchLocale(preferred) : undefined) ?? sourceLocale;
}

// --------------------------------------------------------------------------
// t()
// --------------------------------------------------------------------------

/**
 * Look up a key in the current locale.
 *
 * ```ts
 * t("common.retry");
 * t("quota.resetsIn", { days: 3, hours: 18 });
 * t("quota.cyclesRecorded", { count: 2 });
 * ```
 *
 * The key is a union type and the parameter object is derived from it, so
 * a typo, a missing value and an invented value are all compile errors.
 * Keys without placeholders reject a second argument outright.
 */
export function t<K extends KeysWithoutParams>(key: K): string;
export function t<K extends KeysWithParams>(key: K, params: MessageParams[K]): string;
export function t(key: MessageKey, params?: Readonly<Record<string, ParamValue>>): string {
  return formatMessage(lookup(key), params ?? {}, current);
}

/**
 * Look up a key in a specific locale, ignoring the current one. For a
 * preview row in a language picker, or a string that must not follow the
 * UI language.
 */
export function tIn<K extends KeysWithoutParams>(locale: Locale, key: K): string;
export function tIn<K extends KeysWithParams>(
  locale: Locale,
  key: K,
  params: MessageParams[K],
): string;
export function tIn(
  locale: Locale,
  key: MessageKey,
  params?: Readonly<Record<string, ParamValue>>,
): string {
  return formatMessage(lookup(key, locale), params ?? {}, locale);
}

/**
 * Runtime fallback chain: requested locale, then the source locale, then
 * the key itself.
 *
 * `validate.py` makes the middle step unreachable from this repository —
 * every locale carries every key. It exists because a client can be
 * pinned to an older catalog than the code calling into it, and a
 * half-updated app should show English, not `quota.resetsIn`.
 */
function lookup(key: MessageKey, locale: Locale = current): string {
  const table = messages[locale] ?? messages[sourceLocale];
  return table[key] ?? messages[sourceLocale][key] ?? key;
}
