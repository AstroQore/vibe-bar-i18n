"""Shared catalog primitives for validate.py and generate.py.

Pure standard library, targeting the Python that ships with macOS
(3.9.6 as of macOS 26). No third-party imports, ever: a consumer must be
able to run `python3 scripts/validate.py` on a clean machine.

Nothing in here writes to disk or prints; callers decide what to do with
the errors this module returns.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG_DIR = os.path.join(REPO_ROOT, "catalog")
SCHEMA_DIR = os.path.join(REPO_ROOT, "schema")

SOURCE_LOCALE = "en"
GLOSSARY_BASENAME = "_glossary.json"

# Placeholder type -> C format specifier used in .strings / .stringsdict.
#   string -> %@   (NSString / Swift String)
#   int    -> %lld (Swift Int is 64-bit on every platform Vibe Bar ships to)
#   double -> %f   (Swift Double promotes to C double in a va_list)
FORMAT_SPECIFIER = {"string": "@", "int": "lld", "double": "f"}

# Swift types for the generated typed API.
SWIFT_TYPE = {"string": "String", "int": "Int", "double": "Double"}

# TypeScript types for the generated typed API.
TS_TYPE = {"string": "string", "int": "number", "double": "number"}

# ICU plural categories, in the order .stringsdict wants to see them.
PLURAL_CATEGORIES = ("zero", "one", "two", "few", "many", "other")


class CatalogError(Exception):
    """A problem precise enough to print verbatim and exit non-zero."""


# --------------------------------------------------------------------------
# JSON loading with duplicate-key detection
# --------------------------------------------------------------------------


def _reject_duplicate_keys(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    seen: Dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError("duplicate object key %r" % key)
        seen[key] = value
    return seen


def load_json(path: str) -> Any:
    """Parse `path`, rejecting duplicate object keys.

    `json.load` keeps the last of a duplicated pair silently, which is how
    a catalog can look complete in the diff and be missing a key at runtime.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle, object_pairs_hook=_reject_duplicate_keys)
    except ValueError as exc:  # JSONDecodeError is a subclass
        raise CatalogError("%s: invalid JSON: %s" % (rel(path), exc))
    except OSError as exc:
        raise CatalogError("%s: cannot read: %s" % (rel(path), exc))


def rel(path: str) -> str:
    """Repository-relative path, for error messages a human can paste."""
    try:
        return os.path.relpath(path, REPO_ROOT)
    except ValueError:
        return path


# --------------------------------------------------------------------------
# Minimal JSON Schema (draft 2020-12 subset)
# --------------------------------------------------------------------------
#
# Only the keywords `schema/*.schema.json` actually use are implemented:
# type, required, properties, additionalProperties, propertyNames, pattern,
# enum, minLength, items. An unknown keyword is ignored rather than
# guessed at; `check_schema_coverage` fails loudly if the schema starts
# using something this validator does not understand, so the checker can
# never silently pass a rule it cannot enforce.

_SUPPORTED_KEYWORDS = {
    "$schema",
    "$id",
    "title",
    "description",
    "type",
    "required",
    "properties",
    "additionalProperties",
    "propertyNames",
    "pattern",
    "enum",
    "minLength",
    "items",
}

_JSON_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "number": (int, float),
    "integer": int,
    "null": type(None),
}


def check_schema_coverage(schema: Any, label: str, pointer: str = "") -> List[str]:
    """Fail if the schema uses a keyword this validator cannot enforce."""
    problems: List[str] = []
    if isinstance(schema, dict):
        for keyword in schema:
            if keyword not in _SUPPORTED_KEYWORDS:
                problems.append(
                    "%s: schema keyword %r at %s is not implemented by "
                    "scripts/_catalog.py; extend the validator or drop the keyword"
                    % (label, keyword, pointer or "/")
                )
        for keyword in ("properties", "additionalProperties", "propertyNames", "items"):
            child = schema.get(keyword)
            if isinstance(child, dict):
                if keyword == "properties":
                    for name, sub in child.items():
                        problems += check_schema_coverage(
                            sub, label, "%s/properties/%s" % (pointer, name)
                        )
                else:
                    problems += check_schema_coverage(
                        child, label, "%s/%s" % (pointer, keyword)
                    )
    return problems


def validate_schema(instance: Any, schema: Any, label: str, pointer: str = "") -> List[str]:
    """Return a list of human-readable schema violations."""
    problems: List[str] = []
    where = pointer or "(root)"

    expected_type = schema.get("type")
    if expected_type is not None:
        python_type = _JSON_TYPES[expected_type]
        ok = isinstance(instance, python_type)
        if expected_type in ("number", "integer") and isinstance(instance, bool):
            ok = False
        if not ok:
            problems.append(
                "%s: %s should be of type %s, found %s"
                % (label, where, expected_type, type(instance).__name__)
            )
            return problems

    if "enum" in schema and instance not in schema["enum"]:
        problems.append(
            "%s: %s value %r is not one of %s"
            % (label, where, instance, ", ".join(repr(v) for v in schema["enum"]))
        )

    if isinstance(instance, str):
        pattern = schema.get("pattern")
        if pattern is not None and re.search(pattern, instance) is None:
            problems.append(
                "%s: %s value %r does not match pattern %s" % (label, where, instance, pattern)
            )
        min_length = schema.get("minLength")
        if min_length is not None and len(instance) < min_length:
            problems.append(
                "%s: %s value %r is shorter than minLength %d" % (label, where, instance, min_length)
            )

    if isinstance(instance, dict):
        for name in schema.get("required", []):
            if name not in instance:
                problems.append("%s: %s is missing required property %r" % (label, where, name))

        properties = schema.get("properties", {})
        name_schema = schema.get("propertyNames")
        additional = schema.get("additionalProperties", True)

        for name, value in instance.items():
            child_pointer = "%s/%s" % (pointer, name)
            if name_schema is not None:
                problems += validate_schema(name, name_schema, label, child_pointer + " (key)")
            if name in properties:
                problems += validate_schema(value, properties[name], label, child_pointer)
            elif additional is False:
                problems.append("%s: %s has unexpected property %r" % (label, where, name))
            elif isinstance(additional, dict):
                problems += validate_schema(value, additional, label, child_pointer)

    if isinstance(instance, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, value in enumerate(instance):
                problems += validate_schema(
                    value, item_schema, label, "%s/%d" % (pointer, index)
                )

    return problems


# --------------------------------------------------------------------------
# ICU message parsing
# --------------------------------------------------------------------------
#
# The catalog needs exactly two constructs: a named argument `{name}` and a
# plural `{name, plural, one {# thing} other {# things}}`, with `#` standing
# for the plural argument's own value. Anything else is rejected rather than
# half-supported, because a construct the Swift lane cannot express in a
# .stringsdict is a construct that would silently degrade on macOS.

TEXT = "text"
ARG = "arg"
PLURAL = "plural"
HASH = "hash"

Node = Tuple  # ("text", str) | ("arg", str) | ("hash",) | ("plural", str, [(sel, nodes)])

_NAME_RE = re.compile(r"^[a-z][a-zA-Z0-9]*$")
_SELECTOR_RE = re.compile(r"^(=\d+|zero|one|two|few|many|other)$")


class IcuError(CatalogError):
    pass


def parse_icu(value: str) -> List[Node]:
    """Parse an ICU message into a flat node list. Raises IcuError."""
    nodes, index = _parse_message(value, 0, in_plural=False)
    if index != len(value):
        raise IcuError("unexpected %r at offset %d" % (value[index], index))
    return nodes


def _parse_message(source: str, index: int, in_plural: bool) -> Tuple[List[Node], int]:
    nodes: List[Node] = []
    buffer: List[str] = []

    def flush() -> None:
        if buffer:
            nodes.append((TEXT, "".join(buffer)))
            del buffer[:]

    while index < len(source):
        char = source[index]
        if char == "}":
            break
        if char == "#":
            if in_plural:
                flush()
                nodes.append((HASH,))
            else:
                buffer.append("#")
            index += 1
            continue
        if char == "{":
            flush()
            node, index = _parse_argument(source, index)
            nodes.append(node)
            continue
        buffer.append(char)
        index += 1

    flush()
    return nodes, index


def _parse_argument(source: str, index: int) -> Tuple[Node, int]:
    start = index
    index += 1  # consume '{'
    name, index = _read_until(source, index, ",}")
    name = name.strip()
    if not _NAME_RE.match(name):
        raise IcuError(
            "placeholder name %r at offset %d is not lowerCamelCase" % (name, start)
        )
    if index >= len(source):
        raise IcuError("unterminated placeholder {%s} at offset %d" % (name, start))
    if source[index] == "}":
        return (ARG, name), index + 1

    index += 1  # consume ','
    kind, index = _read_until(source, index, ",}")
    kind = kind.strip()
    if kind != "plural":
        raise IcuError(
            "argument {%s, %s, ...} at offset %d uses an unsupported ICU type; "
            "this catalog supports {name} and {name, plural, ...} only"
            % (name, kind, start)
        )
    if index >= len(source) or source[index] != ",":
        raise IcuError("plural {%s} at offset %d has no branches" % (name, start))
    index += 1

    branches: List[Tuple[str, List[Node]]] = []
    while True:
        while index < len(source) and source[index] in " \t\n":
            index += 1
        if index < len(source) and source[index] == "}":
            index += 1
            break
        if index >= len(source):
            raise IcuError("unterminated plural {%s} at offset %d" % (name, start))
        selector, index = _read_until(source, index, "{}")
        selector = selector.strip()
        if selector.startswith("offset:"):
            raise IcuError(
                "plural {%s} at offset %d uses `offset:`, which .stringsdict "
                "cannot express" % (name, start)
            )
        if not _SELECTOR_RE.match(selector):
            raise IcuError(
                "plural {%s} at offset %d has unknown selector %r" % (name, start, selector)
            )
        if index >= len(source) or source[index] != "{":
            raise IcuError(
                "plural {%s} selector %r at offset %d has no message" % (name, selector, start)
            )
        index += 1
        body, index = _parse_message(source, index, in_plural=True)
        if index >= len(source) or source[index] != "}":
            raise IcuError(
                "plural {%s} selector %r at offset %d is unterminated" % (name, selector, start)
            )
        index += 1
        if any(existing == selector for existing, _ in branches):
            raise IcuError(
                "plural {%s} at offset %d repeats selector %r" % (name, start, selector)
            )
        branches.append((selector, body))

    if not branches:
        raise IcuError("plural {%s} at offset %d has no branches" % (name, start))
    if not any(selector == "other" for selector, _ in branches):
        raise IcuError(
            "plural {%s} at offset %d has no `other` branch; every locale needs "
            "one, and CLDR requires it" % (name, start)
        )
    return (PLURAL, name, branches), index


def _read_until(source: str, index: int, stops: str) -> Tuple[str, int]:
    start = index
    while index < len(source) and source[index] not in stops:
        index += 1
    return source[start:index], index


def used_placeholders(nodes: Sequence[Node]) -> Set[str]:
    """Every argument name referenced anywhere in the message."""
    names: Set[str] = set()
    for node in nodes:
        if node[0] == ARG:
            names.add(node[1])
        elif node[0] == PLURAL:
            names.add(node[1])
            for _, body in node[2]:
                names |= used_placeholders(body)
    return names


def plural_arguments(nodes: Sequence[Node]) -> List[str]:
    """Names used as the selector of a plural, in order of appearance."""
    found: List[str] = []
    for node in nodes:
        if node[0] == PLURAL:
            if node[1] not in found:
                found.append(node[1])
            for _, body in node[2]:
                for nested in plural_arguments(body):
                    if nested not in found:
                        found.append(nested)
    return found


def has_plural(nodes: Sequence[Node]) -> bool:
    return bool(plural_arguments(nodes))


def plain_text(nodes: Sequence[Node]) -> str:
    """The message with every argument removed — used for glossary scanning."""
    pieces: List[str] = []
    for node in nodes:
        if node[0] == TEXT:
            pieces.append(node[1])
        elif node[0] == PLURAL:
            for _, body in node[2]:
                pieces.append(plain_text(body))
    return "".join(pieces)


# --------------------------------------------------------------------------
# Catalog model
# --------------------------------------------------------------------------


class Entry(object):
    """One key in one locale."""

    def __init__(self, key, value, comment, placeholders):
        # type: (str, str, Optional[str], "Dict[str, str]") -> None
        self.key = key
        self.value = value
        self.comment = comment
        self.placeholders = placeholders  # name -> "string" | "int" | "double"

    @property
    def placeholder_order(self):
        # type: () -> List[str]
        """Declaration order — the order the typed APIs use for parameters."""
        return list(self.placeholders.keys())


class Catalog(object):
    def __init__(self, locale, path, entries):
        # type: (str, str, "Dict[str, Entry]") -> None
        self.locale = locale
        self.path = path
        self.entries = entries


def locale_files(catalog_dir=CATALOG_DIR):
    # type: (str) -> List[Tuple[str, str]]
    """(locale, path) for every catalog file, source locale first then sorted."""
    found = []
    for name in sorted(os.listdir(catalog_dir)):
        if not name.endswith(".json") or name == GLOSSARY_BASENAME:
            continue
        found.append((name[: -len(".json")], os.path.join(catalog_dir, name)))
    found.sort(key=lambda pair: (pair[0] != SOURCE_LOCALE, pair[0]))
    return found


def load_catalog(locale, path):
    # type: (str, str) -> Catalog
    raw = load_json(path)
    if not isinstance(raw, dict):
        raise CatalogError("%s: top level must be a JSON object" % rel(path))
    entries = {}
    keys = raw.get("keys")
    if not isinstance(keys, dict):
        raise CatalogError("%s: missing or malformed `keys` object" % rel(path))
    for key in sorted(keys):
        body = keys[key]
        if not isinstance(body, dict):
            raise CatalogError("%s: key %r must map to an object" % (rel(path), key))
        placeholders = body.get("placeholders") or {}
        entries[key] = Entry(
            key=key,
            value=body.get("value", ""),
            comment=body.get("comment"),
            placeholders=placeholders,
        )
    return Catalog(locale=raw.get("locale", locale), path=path, entries=entries)


def load_all(catalog_dir=CATALOG_DIR):
    # type: (str) -> List[Catalog]
    return [load_catalog(locale, path) for locale, path in locale_files(catalog_dir)]


def load_glossary(catalog_dir=CATALOG_DIR):
    # type: (str) -> List[Dict[str, Any]]
    raw = load_json(os.path.join(catalog_dir, GLOSSARY_BASENAME))
    return raw.get("neverTranslate", [])


# --------------------------------------------------------------------------
# Format-specifier rendering, shared by the .strings and .stringsdict writers
# --------------------------------------------------------------------------


def specifier(entry, name, positional):
    # type: (Entry, str, bool) -> str
    """`%@` / `%lld` / `%f`, positional when the key takes more than one value."""
    kind = entry.placeholders.get(name)
    if kind is None:
        raise CatalogError(
            "%s: placeholder {%s} is used but not declared" % (entry.key, name)
        )
    if kind not in FORMAT_SPECIFIER:
        raise CatalogError(
            "%s: placeholder {%s} is declared %r; the supported types are %s"
            % (entry.key, name, kind, ", ".join(sorted(FORMAT_SPECIFIER)))
        )
    body = FORMAT_SPECIFIER[kind]
    if not positional:
        return "%" + body
    return "%%%d$%s" % (entry.placeholder_order.index(name) + 1, body)
