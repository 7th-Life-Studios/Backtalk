from __future__ import annotations

import re

_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_INDENT_CODE = re.compile(r"(?:^|\n)(?: {4}|\t)[^\n]*(?:\n(?: {4}|\t)[^\n]*)*")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HTML = re.compile(r"<[^>]{1,120}>")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_BLOCKQUOTE = re.compile(r"^\s{0,3}>\s?", re.MULTILINE)
_RULE = re.compile(r"^\s{0,3}(?:[-*_]\s?){3,}\s*$", re.MULTILINE)
_BULLET = re.compile(r"^\s*[-*+•]\s+", re.MULTILINE)
_EMPHASIS = re.compile(r"(\*{1,3}|_{1,3})(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]{5,}\|?\s*$", re.MULTILINE)
_MULTI_BLANK = re.compile(r"\n{3,}")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_SNAKE = re.compile(r"(?<=\w)_(?=\w)")

_SMART = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "–": ", ", "—": ", ", "―": ", ",
    "…": "...", " ": " ", "​": "",
}

_ABBREV = (
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "eg", "ie",
    "approx", "dept", "est", "fig", "inc", "ltd", "no", "vol", "min", "max",
)
_ABBREV_RE = re.compile(
    r"\b(" + "|".join(_ABBREV) + r")\.\s", re.IGNORECASE
)
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+")

_SPEAKABLE = re.compile(r"[^\W_]", re.UNICODE)


def has_speakable(text: str) -> bool:
    return bool(_SPEAKABLE.search(text or ""))


def _code_block_note(match: re.Match[str]) -> str:
    lines = [ln for ln in match.group(1).splitlines() if ln.strip()]
    n = len(lines)
    if n == 0:
        return " Code block. "
    return f" Code block, {n} line{'s' if n != 1 else ''}. "


def clean_for_speech(text: str) -> str:
    if not text:
        return ""

    for bad, good in _SMART.items():
        text = text.replace(bad, good)

    text = _FENCE.sub(_code_block_note, text)
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _TABLE_SEP.sub("", text)
    text = _TABLE_ROW.sub(lambda m: m.group(0).strip().strip("|").replace("|", ", "), text)
    text = _HTML.sub("", text)
    text = _RULE.sub("", text)
    text = _HEADING.sub("", text)
    text = _BLOCKQUOTE.sub("", text)
    text = _BULLET.sub("", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _EMPHASIS.sub(r"\2", text)
    text = _SNAKE.sub(" ", text)

    text = _MULTI_SPACE.sub(" ", text)
    text = _MULTI_BLANK.sub("\n\n", text)

    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            lines.append("")
            continue
        if line[-1] not in ".!?:;,":
            line += "."
        lines.append(line)
    text = "\n".join(lines)

    return _MULTI_BLANK.sub("\n\n", text).strip()


def split_sentences(text: str) -> list[str]:
    if not text:
        return []

    guarded = _ABBREV_RE.sub(lambda m: m.group(1) + ".\x00", text)
    parts: list[str] = []
    for block in guarded.split("\n"):
        block = block.strip()
        if not block:
            continue
        for piece in _SENTENCE_END.split(block):
            piece = piece.replace("\x00", " ").strip()
            if piece:
                parts.append(piece)
    return parts


def chunk_for_synthesis(
    text: str, first_chunk_sentences: int = 1, max_chars: int = 140
) -> list[str]:
    sentences = split_sentences(text)
    if not sentences:
        return []

    chunks: list[str] = []
    head = sentences[: max(1, first_chunk_sentences)]
    chunks.append(" ".join(head))

    current = ""
    for sentence in sentences[len(head):]:
        if not current:
            current = sentence
        elif len(current) + 1 + len(sentence) <= max_chars:
            current = f"{current} {sentence}"
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)

    return _fold_unspeakable(chunks)


def _fold_unspeakable(chunks: list[str]) -> list[str]:
    result: list[str] = []
    pending: list[str] = []
    for chunk in chunks:
        if has_speakable(chunk):
            if pending:
                chunk = " ".join(pending + [chunk])
                pending = []
            result.append(chunk)
        elif result:
            result[-1] = f"{result[-1]} {chunk}".strip()
        else:
            pending.append(chunk)
    if not result:
        return []
    return result
