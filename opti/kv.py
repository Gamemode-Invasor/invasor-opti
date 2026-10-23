"""Valve KeyValues files, just enough to read Steam's: text VDF (appmanifest, localconfig, config) and binary (shortcuts.vdf)."""
import re
import struct

TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')
ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


def loads_text(text):
    """Nested dicts of strings. A repeated key keeps its last value."""
    root, stack = {}, []
    node, key = root, None
    for m in TOKEN.finditer(text):
        word, brace = m.groups()
        if brace == "{":
            child = {}
            if key is not None:  # a block with no name is read and left out
                node[key] = child
            stack.append(node)
            node, key = child, None
        elif brace == "}":
            if stack:
                node = stack.pop()
            key = None
        else:
            word = re.sub(r"\\(.)", lambda e: ESCAPES.get(e.group(1), e.group(1)), word)
            if key is None:
                key = word
            else:
                node[key] = word
                key = None
    return root


def get(node, *path, default=None):
    """node[path...] ignoring the case of the keys (Steam writes "Steam" and "steam")."""
    for name in path:
        if not isinstance(node, dict):
            return default
        found = next((v for k, v in node.items() if str(k).lower() == str(name).lower()), None)
        if found is None:
            return default
        node = found
    return node


def loads_binary(data):
    """{key: str | int | dict} from a binary VDF such as shortcuts.vdf. Raises ValueError if truncated."""
    pos = 0

    def cstring():
        nonlocal pos
        end = data.index(b"\0", pos)
        out = data[pos:end].decode("utf-8", errors="replace")
        pos = end + 1
        return out

    def read_map():
        nonlocal pos
        out = {}
        while True:
            kind = data[pos]
            pos += 1
            if kind == 8:
                return out
            name = cstring()
            if kind == 0:
                out[name] = read_map()
            elif kind == 1:
                out[name] = cstring()
            elif kind == 2:
                out[name] = struct.unpack_from("<i", data, pos)[0]
                pos += 4
            else:
                raise ValueError(f"unknown VDF type {kind}")

    try:
        return read_map()
    except (IndexError, ValueError, struct.error, RecursionError) as e:  # RecursionError: nested beyond reason
        raise ValueError("truncated or invalid VDF") from e
