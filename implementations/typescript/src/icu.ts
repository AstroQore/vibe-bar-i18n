// Hand-written. The smallest ICU MessageFormat subset the catalog uses:
// a named argument `{name}` and a plural `{name, plural, one {#} other {#}}`,
// with `#` standing for the plural argument's value.
//
// Deliberately not a dependency. `intl-messageformat` is ~40 kB of runtime
// for a catalog that uses two constructs, and a localization package that
// drags a transitive tree into a Tauri bundle is a localization package
// people route around. Plural *rules* still come from the platform:
// `Intl.PluralRules` ships in every JavaScript runtime this package
// targets, so nothing here hard-codes CLDR.
//
// `scripts/validate.py` parses the same grammar with the same rules, and
// fails the build on anything this cannot render — the two parsers are
// kept honest by the catalog they both read.

export type ParamValue = string | number;

type Node =
  | { readonly kind: "text"; readonly text: string }
  | { readonly kind: "arg"; readonly name: string }
  | { readonly kind: "hash" }
  | { readonly kind: "plural"; readonly name: string; readonly branches: readonly Branch[] };

interface Branch {
  readonly selector: string;
  readonly nodes: readonly Node[];
}

/** Parsed messages are cached: a menu-bar UI re-renders the same key often. */
const parsed = new Map<string, readonly Node[]>();

/** One `Intl.PluralRules` per locale, for the same reason. */
const pluralRules = new Map<string, Intl.PluralRules>();

/**
 * Render an ICU message.
 *
 * Unknown or missing parameters render as an empty string rather than
 * throwing: a formatting error should not take down a status bar. The
 * catalog cannot get into that state — `validate.py` fails on it — but a
 * client built against an older catalog can.
 */
/**
 * Every argument name a message uses, in order of first appearance.
 *
 * Walks the parsed message rather than scanning for `{name`, because a
 * plural's branch body is text, not arguments: `one {once}` names no
 * argument called `once`. The Python side learned this the same way — see
 * `_reuse_signature` in `scripts/validate.py`, which had the identical bug
 * with the identical cause.
 */
export function placeholderNames(message: string): string[] {
  const found: string[] = [];
  const walk = (nodes: readonly Node[]): void => {
    for (const node of nodes) {
      if (node.kind === "arg" || node.kind === "plural") {
        if (!found.includes(node.name)) found.push(node.name);
      }
      if (node.kind === "plural") {
        for (const branch of node.branches) walk(branch.nodes);
      }
    }
  };
  walk(parse(message));
  return found;
}

export function formatMessage(
  message: string,
  params: Readonly<Record<string, ParamValue>>,
  locale: string,
): string {
  return render(parse(message), params, locale, undefined);
}

function render(
  nodes: readonly Node[],
  params: Readonly<Record<string, ParamValue>>,
  locale: string,
  hashValue: number | undefined,
): string {
  let out = "";
  for (const node of nodes) {
    switch (node.kind) {
      case "text":
        out += node.text;
        break;
      case "arg":
        out += stringify(params[node.name]);
        break;
      case "hash":
        out += hashValue === undefined ? "" : String(hashValue);
        break;
      case "plural": {
        const value = Number(params[node.name]);
        const branch = selectBranch(node.branches, value, locale);
        out += branch ? render(branch.nodes, params, locale, value) : "";
        break;
      }
    }
  }
  return out;
}

function stringify(value: ParamValue | undefined): string {
  // Numbers are printed raw on purpose: the catalog's contract is that the
  // caller has already formatted anything needing a number formatter, so
  // adding digit grouping here would double-format it.
  return value === undefined ? "" : String(value);
}

function selectBranch(
  branches: readonly Branch[],
  value: number,
  locale: string,
): Branch | undefined {
  const exact = branches.find((branch) => branch.selector === `=${value}`);
  if (exact) return exact;

  let rules = pluralRules.get(locale);
  if (!rules) {
    try {
      rules = new Intl.PluralRules(locale);
    } catch {
      rules = new Intl.PluralRules("en");
    }
    pluralRules.set(locale, rules);
  }
  const category = Number.isFinite(value) ? rules.select(value) : "other";
  return (
    branches.find((branch) => branch.selector === category) ??
    branches.find((branch) => branch.selector === "other")
  );
}

// --------------------------------------------------------------------------
// Parsing
// --------------------------------------------------------------------------

function parse(message: string): readonly Node[] {
  const cached = parsed.get(message);
  if (cached) return cached;
  const nodes = parseMessage(message, 0, false).nodes;
  parsed.set(message, nodes);
  return nodes;
}

function parseMessage(
  source: string,
  start: number,
  inPlural: boolean,
): { nodes: readonly Node[]; index: number } {
  const nodes: Node[] = [];
  let text = "";
  let index = start;

  const flush = (): void => {
    if (text.length > 0) {
      nodes.push({ kind: "text", text });
      text = "";
    }
  };

  while (index < source.length) {
    const char = source[index] as string;
    if (char === "}") break;
    if (char === "#" && inPlural) {
      flush();
      nodes.push({ kind: "hash" });
      index += 1;
      continue;
    }
    if (char === "{") {
      flush();
      const argument = parseArgument(source, index);
      nodes.push(argument.node);
      index = argument.index;
      continue;
    }
    text += char;
    index += 1;
  }

  flush();
  return { nodes, index };
}

function parseArgument(source: string, start: number): { node: Node; index: number } {
  let index = start + 1;
  const nameEnd = findAny(source, index, ",}");
  const name = source.slice(index, nameEnd).trim();
  index = nameEnd;

  if (source[index] === "}") {
    return { node: { kind: "arg", name }, index: index + 1 };
  }

  // `{name, plural, …}`. Anything else is rejected by validate.py before it
  // can reach here, so treat it as a plural and let a bad branch list fall
  // through to `other`.
  index = findAny(source, index + 1, ",}");
  index += 1;

  const branches: Branch[] = [];
  while (index < source.length && source[index] !== "}") {
    while (index < source.length && /\s/.test(source[index] as string)) index += 1;
    if (source[index] === "}") break;
    const selectorEnd = findAny(source, index, "{}");
    const selector = source.slice(index, selectorEnd).trim();
    index = selectorEnd;
    if (source[index] !== "{") break;
    const body = parseMessage(source, index + 1, true);
    branches.push({ selector, nodes: body.nodes });
    index = body.index + 1;
  }

  return { node: { kind: "plural", name, branches }, index: index + 1 };
}

function findAny(source: string, from: number, stops: string): number {
  let index = from;
  while (index < source.length && !stops.includes(source[index] as string)) index += 1;
  return index;
}
