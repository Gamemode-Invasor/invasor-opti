"""OptiScaler.ini: read it into sections and keys, guess a control for each key from the comments
OptiScaler writes above it, and change one value without touching anything else in the file.

Pure functions, no ctx: the backend does the file handling.

The comments follow a loose pattern that holds for most keys:

    ; Selects upscaler backend for FSR3.X/4
    ; 0 = FSR 4.0.2 | 1 = FSR 3.1.5 | 2 = FSR 2.3.4
    ; Depending on GPU - Default (auto) is 0 - FSR4 for RDNA4, FSR3 for the rest
    UpscalerIndex=auto

Every key takes "auto" (OptiScaler's own default), so it is always the first option. A key whose
comments match no rule is a text field, and a value the rules don't offer is kept as an option:
the user's value is never lost.
"""
import re

AUTO = "auto"
MAX_STEPS = 101  # a range with more values than this is a text field
MAX_VALUE = 1024

SECTION = re.compile(r"^\s*\[([^\]]+)\]\s*$")
KEY = re.compile(r"^(\s*)([A-Za-z0-9_.]+)(\s*=\s*)(.*?)(\s*)$")
RULER = re.compile(r"^-+$")
DEFAULT_TAIL = re.compile(r"\s+-\s+default\b.*$|\s*-?\s*\bdefault\s*\(auto\).*$", re.IGNORECASE)
# "0 = FSR 4.0.2", "0: Input", "2 - AntiLag 2"
NUMBERED = re.compile(r"^\s*(-?\d+)\s*(?:=|:|-)\s*(\S.*?)\s*$")
IDENT = re.compile(r"^([a-z][a-z0-9_]*)(\s*\(.*\))?$")
NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
RANGE = re.compile(
    r"^(?:from\s+|value range\s+(?:between\s+)?)?(-?\d+(?:\.\d+)?)\s*(?:-|to|and)\s*(-?\d+(?:\.\d+)?)(?![\d.])",
    re.IGNORECASE,
)
BOOL = re.compile(r"\btrue or false\b", re.IGNORECASE)


def decode(raw):
    """(text, encoding) of the file's bytes: UTF-8 (with or without a BOM; "utf-8-sig" writes it
    back), else Latin-1 (which reads anything)."""
    try:
        if raw.startswith(b"\xef\xbb\xbf"):
            return raw.decode("utf-8-sig"), "utf-8-sig"
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


def parse(text):
    """[{"name", "keys": [{"key", "value", "comments": [str]}]}] in file order. The comment lines
    right above a key (rulers excluded) are its comments; keys before the first section go to a
    section named "". A file isn't always tidy: a section that appears twice is one section (where
    it first appears), a key that appears twice in it is its first one (the one set_value changes),
    and lines that are neither a section, a key nor a comment are skipped."""
    sections = []
    by_name = {}
    current = None
    comments = []
    for line in text.splitlines():
        stripped = line.strip()
        m = SECTION.match(line)
        if m:
            current = _section(sections, by_name, m.group(1).strip())
            comments = []
            continue
        if stripped.startswith((";", "#")):
            body = stripped.lstrip(";#").strip()
            if RULER.match(body):
                comments = []
            elif body:
                comments.append(body)
            continue
        if not stripped:
            continue
        m = KEY.match(line)
        if not m:
            comments = []
            continue
        if current is None:
            current = _section(sections, by_name, "")
        if not any(k["key"] == m.group(2) for k in current["keys"]):
            current["keys"].append({"key": m.group(2), "value": m.group(4), "comments": comments})
        comments = []
    return sections


def _section(sections, by_name, name):
    """The section called name, added at the end the first time."""
    if name not in by_name:
        by_name[name] = {"name": name, "keys": []}
        sections.append(by_name[name])
    return by_name[name]


def _strip_default(text):
    return DEFAULT_TAIL.sub("", text).strip()


def _numbered(comments):
    """[(value, label)] from "0 = A | 1 = B", "0 = A, 1 = B" or one "0 = A" per line, else None."""
    for line in comments:
        for sep in ("|", ",", " / "):
            parts = [p for p in line.split(sep) if p.strip()]
            if len(parts) < 2:
                continue
            found = [NUMBERED.match(_strip_default(p)) for p in parts]
            if all(found):
                return [(m.group(1), f"{m.group(1)} - {m.group(2)}") for m in found]
    lines = [NUMBERED.match(line) for line in comments if "default" not in line.lower()]
    found = [m for m in lines if m and "=" in m.group(0)]
    if len(found) >= 2:
        return [(m.group(1), f"{m.group(1)} - {m.group(2)}") for m in found]
    return None


def _split_outside_parens(text):
    """text split on the commas that aren't inside parentheses."""
    parts, depth, start = [], 0, 0
    for i, c in enumerate(text):
        if c == "(":
            depth += 1
        elif c == ")":
            depth = max(0, depth - 1)
        elif c == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


def _listed(comments):
    """[(value, label)] from a line of ids or numbers, like "xess, fsr21, ffx (FSR 2.3; 3.1), dlss"
    or "2, 4, 8, 16 - Default (auto) is disabled", else None."""
    for line in comments:
        parts = [p.strip() for p in _split_outside_parens(re.sub(r"\s+or\s+", ",", _strip_default(line)))]
        if len(parts) < 2:
            continue
        if all(NUMBER.match(p) for p in parts):
            return [(p, p) for p in parts]
        found = [IDENT.match(p) for p in parts]
        if all(found):
            return [(m.group(1), m.group(0)) for m in found]
    return None


def _range(comments):
    """[(value, value)] for every step of "0.0 - 1.0" / "From 0 to 4", else None (also when there
    would be more than MAX_STEPS)."""
    for line in comments:
        m = RANGE.match(line)
        if not m:
            continue
        low, high = m.group(1), m.group(2)
        decimals = max(len(x.split(".")[1]) if "." in x else 0 for x in (low, high))
        scale = 10 ** decimals
        a, b = round(float(low) * scale), round(float(high) * scale)
        if a >= b or b - a + 1 > MAX_STEPS:
            return None
        return [(f"{i / scale:.{decimals}f}",) * 2 for i in range(a, b + 1)]
    return None


def infer(comments, value=AUTO):
    """{"kind": "select" | "text", "options": [{"value", "label"}]} for a key, from its comments. A
    select always starts with "auto" and holds the current value."""
    options = _numbered(comments)
    if options is None and any(BOOL.search(c) for c in comments):
        options = [("true", "true"), ("false", "false")]
    if options is None:
        options = _listed(comments)
    if options is None:
        options = _range(comments)
    if options is None:
        return {"kind": "text", "options": []}
    result = [{"value": AUTO, "label": AUTO}]
    seen = {AUTO}
    for v, label in options:
        if v.lower() not in seen:
            seen.add(v.lower())
            result.append({"value": v, "label": label})
    if value.lower() not in seen:
        result.append({"value": value, "label": f"{value} (current)"})
    return {"kind": "select", "options": result}


def match_option(options, value):
    """The option value that stands for `value` (ini values aren't case-sensitive: True = true)."""
    for o in options:
        if o["value"].lower() == value.lower():
            return o["value"]
    return None


def describe(sections):
    """parse() plus, for each key, its control and description (what the UI shows)."""
    out = []
    for s in sections:
        keys = []
        for k in s["keys"]:
            control = infer(k["comments"], k["value"])
            keys.append({
                "key": k["key"],
                "value": k["value"],
                "kind": control["kind"],
                "options": control["options"],
                "description": "\n".join(k["comments"]),
            })
        out.append({"name": s["name"], "keys": keys})
    return out


def check_value(value):
    """The value to write, stripped; ValueError if it can't be one line of an ini."""
    if not isinstance(value, str):
        raise ValueError("the value must be text")
    value = value.strip()
    if any(c in value for c in "\r\n\0"):
        raise ValueError("the value must be one line")
    if len(value) > MAX_VALUE:
        raise ValueError("the value is too long")
    return value if value else AUTO


def set_value(text, section, key, value):
    """text with that key's value changed and everything else (comments, spacing, line ends,
    order) as it was. KeyError if the section has no such key."""
    value = check_value(value)
    lines = text.splitlines(keepends=True)
    current = None if section else ""
    for i, line in enumerate(lines):
        body = line.rstrip("\r\n")
        m = SECTION.match(body)
        if m:
            current = m.group(1).strip()
            continue
        if current != section or body.lstrip().startswith((";", "#")):
            continue
        m = KEY.match(body)
        if m and m.group(2) == key:
            ending = line[len(body):]
            lines[i] = f"{m.group(1)}{m.group(2)}{m.group(3)}{value}{m.group(5)}{ending}"
            return "".join(lines)
    raise KeyError(f"[{section}] {key}")
