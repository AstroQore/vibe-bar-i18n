import assert from "node:assert/strict";
import test from "node:test";

import {
  getLocale,
  isLocale,
  locales,
  matchLocale,
  setLocale,
  sourceLocale,
  t,
  tIn,
  type Locale,
  type MessageKey,
} from "../src/index.js";
import { messages } from "../src/generated/catalog.js";
import { placeholderNames } from "../src/icu.js";

const keys = Object.keys(messages[sourceLocale]) as MessageKey[];

// Placeholder names come from the runtime's own parser. The regex that
// used to live here read a plural's branch body as arguments — `one {once}`
// declared an argument named "once", which no caller passes and no
// translation can reproduce — so the first key with a bare word in a branch
// failed as a placeholder mismatch. One parser, one answer.
const placeholdersIn = placeholderNames;

/** A parameter object that satisfies whatever the key declares. */
function sampleParams(message: string): Record<string, string | number> {
  const params: Record<string, string | number> = {};
  for (const name of placeholdersIn(message)) params[name] = 7;
  return params;
}

test.afterEach(() => {
  setLocale(sourceLocale);
});

test("every key resolves in every locale", () => {
  for (const locale of locales) {
    for (const key of keys) {
      const rendered = tIn(
        locale,
        key as never,
        sampleParams(messages[sourceLocale][key]) as never,
      );
      assert.notEqual(rendered, key, `${locale}: ${key} fell back to its own identifier`);
      assert.ok(rendered.length > 0, `${locale}: ${key} rendered empty`);
    }
  }
});

test("no translation drops or invents a placeholder", () => {
  for (const key of keys) {
    const expected = [...placeholdersIn(messages[sourceLocale][key])].sort();
    for (const locale of locales) {
      const actual = [...placeholdersIn(messages[locale][key])].sort();
      assert.deepEqual(actual, expected, `${locale}: ${key} placeholder set differs from source`);
    }
  }
});

test("nothing unrendered is left in the output", () => {
  for (const locale of locales) {
    for (const key of keys) {
      const rendered = tIn(
        locale,
        key as never,
        sampleParams(messages[sourceLocale][key]) as never,
      );
      assert.ok(!rendered.includes("{"), `${locale}: ${key} left an unrendered brace`);
      assert.ok(!rendered.includes("#"), `${locale}: ${key} left an unrendered '#'`);
    }
  }
});

test("plurals pick the right branch per locale", () => {
  setLocale("en");
  assert.equal(t("common.duration.full.days", { count: 1 }), "1 day");
  assert.equal(t("common.duration.full.days", { count: 2 }), "2 days");

  setLocale("zh-Hans");
  // Simplified Chinese has only `other`; both counts take it.
  assert.equal(t("common.duration.full.days", { count: 1 }), "1 天");
  assert.equal(t("common.duration.full.days", { count: 2 }), "2 天");
});

test("named parameters survive a reordered translation", () => {
  setLocale("en");
  assert.equal(
    t("common.duration.full.daysHours", { days: "3 days", hours: "18 hours" }),
    "3 days and 18 hours",
  );
  assert.equal(t("quota.usedPercent", { percent: 42 }), "42% used");
  assert.equal(
    t("resetHistory.wastedSummary", { used: 61, wasted: 39, cycles: 7 }),
    "61% used · 39% wasted · 7 cycles",
  );

  setLocale("zh-Hans");
  assert.equal(
    t("common.duration.full.daysHours", { days: "3 天", hours: "18 小时" }),
    "3 天 18 小时",
  );
  assert.equal(t("quota.usedPercent", { percent: 42 }), "已用 42%");
  assert.equal(
    t("resetHistory.wastedSummary", { used: 61, wasted: 39, cycles: 7 }),
    "已用 61% · 浪费 39% · 7 个周期",
  );
  assert.equal(t("error.networkWithReason", { reason: "timed out" }), "网络错误：timed out");
});

test("the locale setter changes what t returns", () => {
  setLocale("en");
  const english = t("common.retry");
  setLocale("zh-Hans");
  const chinese = t("common.retry");

  assert.equal(english, "Retry");
  assert.equal(chinese, "重试");
  assert.equal(getLocale(), "zh-Hans");
});

test("an unshipped locale is refused rather than blanking the UI", () => {
  setLocale("en");
  assert.equal(setLocale("qq-Fake"), false);
  assert.equal(getLocale(), "en");
  assert.equal(t("common.retry"), "Retry");
});

test("locale tags are matched leniently", () => {
  assert.equal(matchLocale("zh"), "zh-Hans");
  assert.equal(matchLocale("zh-Hans-CN"), "zh-Hans");
  assert.equal(matchLocale("zh_Hans"), "zh-Hans");
  assert.equal(matchLocale("en-US"), "en");
  assert.equal(matchLocale("EN"), "en");
  assert.equal(matchLocale("qq"), undefined);

  assert.ok(isLocale("zh-Hans"));
  assert.ok(!isLocale("zh-Hant"));
});

test("a key missing from a locale falls back to the source locale", () => {
  // Reachable only for a client pinned to an older catalog than its caller;
  // `validate.py` makes it impossible inside this repository. Simulated by
  // deleting the key from the live table and putting it back.
  const table = messages["zh-Hans"] as Record<string, string>;
  const saved = table["common.retry"] as string;
  delete table["common.retry"];
  try {
    setLocale("zh-Hans");
    assert.equal(t("common.retry"), "Retry");
  } finally {
    table["common.retry"] = saved;
  }
});

test("an unknown key falls back to the key itself", () => {
  setLocale("en");
  const unknown = "never.authored" as MessageKey;
  assert.equal(t(unknown as never), "never.authored");
});

test("locales are exported in a stable order with the source locale present", () => {
  assert.ok(locales.includes(sourceLocale as Locale));
  assert.deepEqual([...locales], [...locales].sort());
});
