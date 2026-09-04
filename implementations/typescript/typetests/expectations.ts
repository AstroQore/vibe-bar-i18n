// Compile-time assertions. This file has no runtime behaviour and is never
// published; it exists so `tsc --noEmit` can prove the claims `t`'s
// signature makes. Every `@ts-expect-error` below is itself an assertion:
// if the line stops being an error, TypeScript reports the unused directive
// and the type-check fails.

import { t, tIn, type Locale, type MessageKey, type MessageParams } from "../src/index.js";

// -- The shapes that must compile -----------------------------------------

const plain: string = t("common.retry");
const placeholder: string = t("common.duration.full.daysHours", { days: "3 days", hours: "18 hours" });
const plural: string = t("common.duration.full.days", { count: 2 });
const scoped: string = tIn("zh-Hans", "quota.usedPercent", { percent: 42 });
const stringArgument: string = t("error.networkWithReason", { reason: "timed out" });

// -- Keys ------------------------------------------------------------------

// @ts-expect-error a key that is not in the catalog
t("common.retryy");

// @ts-expect-error an English sentence is not a key
t("Retry");

// -- Missing, extra and mistyped parameters --------------------------------

// @ts-expect-error `hours` is missing
t("common.duration.full.daysHours", { days: "3 days" });

// @ts-expect-error `minutes` is not a parameter of this key
t("common.duration.full.daysHours", { days: "3 days", hours: "18 hours", minutes: 0 });

// @ts-expect-error `days` is a string, not an int
t("common.duration.full.daysHours", { days: 3, hours: "18 hours" });

// @ts-expect-error the parameter object is required
t("common.duration.full.daysHours");

// @ts-expect-error `reason` is a string, not a number
t("error.networkWithReason", { reason: 404 });

// -- Keys that take no parameters ------------------------------------------

// @ts-expect-error `common.retry` has no placeholders, so it takes no object
t("common.retry", { anything: 1 });

// @ts-expect-error even an empty object is one argument too many
t("common.retry", {});

// -- tIn ------------------------------------------------------------------

// @ts-expect-error a locale the catalog does not ship
tIn("fr", "common.retry");

// -- The exported types are usable by a consumer ---------------------------

const key: MessageKey = "settings.language.title";
const locale: Locale = "zh-Hans";
const params: MessageParams["common.duration.full.daysHours"] = { days: "1 day", hours: "2 hours" };

// @ts-expect-error `MessageParams` for a key with no placeholders is `undefined`
const none: MessageParams["common.retry"] = {};

export const compiled = {
  plain,
  placeholder,
  plural,
  scoped,
  stringArgument,
  key,
  locale,
  params,
  none,
};
