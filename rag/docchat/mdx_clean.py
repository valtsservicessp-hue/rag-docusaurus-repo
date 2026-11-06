"""Turn MDX into plain text that embeds well and reads well in a prompt."""

import re
from collections.abc import Iterator

# A backtick fence cannot have backticks after it: "```x```" is inline code.
_FENCE = re.compile(r"^[ \t]*(?P<marks>`{3,}(?=[^`]*$)|~{3,})(?P<info>.*)$")
_INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`)[^\n]+?(?<!`)\1(?!`)")
_MASK = re.compile(r"\x00(\d+)\x00")

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_MDX_COMMENT = re.compile(r"\{\s*/\*.*?\*/\s*\}", re.DOTALL)
_ESM_START = re.compile(r"^(?:import|export)\s")

# JSX attributes may hold ">" in strings or braces: onClick={() => go()}.
_ATTRS = r"""(?:"[^"]*"|'[^']*'|\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}|[^"'{}>])*"""
_TAB_OPEN = re.compile(rf"<TabItem\b(?P<attrs>{_ATTRS})>\s*")
_TAB_CLOSE = re.compile(r"</TabItem\s*>")
_TAB_NAME = re.compile(r"""\b(?P<key>label|value)=(["'])(?P<name>.*?)\2""")
_TAG = re.compile(rf"</?[A-Za-z][\w.:-]*{_ATTRS}>|</?>")

_ADMONITION_CLOSE = re.compile(r"^[ \t]*:{3,}[ \t]*$", re.MULTILINE)
_ADMONITION_OPEN = re.compile(
    r"^[ \t]*:{3,}(?P<kind>[A-Za-z]+)"
    r"(?:\[(?P<title>[^\]\n]*)\]|[ \t]+(?P<legacy>[^\n{]+?))?"
    r"[ \t]*(?:\{[^\n]*\})?[ \t]*(?:\n\s*|\Z)",
    re.MULTILINE,
)


def scan_fences(text: str) -> Iterator[tuple[str, str]]:
    """Yield (line, kind). Kind is 'text', 'open' (a fence) or 'code'."""
    fence = None
    for line in text.splitlines():
        match = _FENCE.match(line)
        if fence is None:
            if match:
                fence = match["marks"]
                yield line, "open"
            else:
                yield line, "text"
            continue
        # A fence closes on the same mark, at least as long, with nothing after it.
        if match and match["marks"].startswith(fence) and not match["info"].strip():
            fence = None
        yield line, "code"


def _drop_esm(text: str) -> str:
    """Remove import/export blocks, which end at a blank line outside brackets."""
    kept = []
    depth = 0
    in_esm = False
    for line in text.split("\n"):
        if not in_esm and not _ESM_START.match(line):
            kept.append(line)
            continue
        in_esm = True
        depth += sum(line.count(mark) for mark in "([{")
        depth -= sum(line.count(mark) for mark in ")]}")
        if not line.strip() and depth <= 0:
            in_esm = False
            depth = 0
            kept.append(line)
    return "\n".join(kept)


def _tab_label(match: re.Match[str]) -> str:
    """Keep a tab's name, so 'Windows' stays attached to its text."""
    names = {m["key"]: m["name"] for m in _TAB_NAME.finditer(match["attrs"])}
    name = names.get("label") or names.get("value")
    return f"{name}: " if name else ""


def _admonition_label(match: re.Match[str]) -> str:
    kind = match["kind"].capitalize()
    title = (match["title"] or match["legacy"] or "").strip()
    return f"{kind} ({title}): " if title else f"{kind}: "


def _clean_prose(text: str) -> str:
    """Clean text outside code fences. Inline code is protected from every rule."""
    spans: list[str] = []

    def mask(match: re.Match[str]) -> str:
        spans.append(match[0])
        return f"\x00{len(spans) - 1}\x00"

    text = _INLINE_CODE.sub(mask, text)
    text = _HTML_COMMENT.sub("", text)
    text = _MDX_COMMENT.sub("", text)
    text = _drop_esm(text)
    text = _TAB_OPEN.sub(_tab_label, text)
    text = _TAB_CLOSE.sub("\n", text)
    text = _TAG.sub("", text)
    text = _ADMONITION_CLOSE.sub("", text)
    text = _ADMONITION_OPEN.sub(_admonition_label, text)
    text = _MASK.sub(lambda match: spans[int(match[1])], text)

    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def clean_mdx(text: str) -> str:
    """Return plain text. Code blocks stay intact, labelled with their language."""
    blocks: list[str] = []
    prose: list[str] = []

    for line, kind in scan_fences(text):
        if kind == "text":
            prose.append(line)
            continue
        if prose:
            blocks.append(_clean_prose("\n".join(prose)))
            prose = []
        if kind == "open":
            info = _FENCE.match(line)["info"].split()
            blocks.append(f"Code ({info[0]}):\n{line}" if info else line)
        else:
            blocks[-1] += f"\n{line}"

    if prose:
        blocks.append(_clean_prose("\n".join(prose)))
    return "\n\n".join(block for block in blocks if block)
