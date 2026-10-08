"""Deterministic selection of usable textual excerpts, never a claim of full extraction."""

import re
from dataclasses import dataclass
from itertools import pairwise

from legal_core.corpus_loader import CorpusFragment

_ARTICLE = re.compile(r"(?m)^Статья[ \t]+(?P<number>[0-9]+(?:[.][0-9]+)*)[. \t]")
_POINT = re.compile(r"(?m)^(?P<number>[0-9]+(?:[.][0-9]+)*)[.)][ \t]+\S")
# Exclude the entire structural block; do not retain an orphan explanation of a lost formula.
_GRAPHICS_DEPENDENCY = re.compile(
    r"формул|рисун[окка]|на\s+графике|графическ|схем[ауы]|таблиц", re.IGNORECASE
)
_BARE_LOSS = re.compile(r"(?m)^\s*(?:[,;=]|где)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class TextLayerSelection:
    fragments: list[CorpusFragment]
    excluded_blocks: int


def _pieces(text: str) -> list[str]:
    """Preserve contiguous original substrings, including a small final tail."""
    pieces = []
    start = 0
    while len(text) - start > 10_000:
        end = text.rfind("\n", start + 5000, start + 9900)
        if end <= start:
            end = start + 9900
        pieces.append(text[start:end])
        start = end
    tail = text[start:]
    if len(tail.strip()) < 20 and pieces:
        previous = pieces.pop()
        boundary = len(previous) - 100
        pieces.extend([previous[:boundary], previous[boundary:] + tail])
    else:
        pieces.append(tail)
    return [piece.strip() for piece in pieces if len(piece.strip()) >= 20]


def compile_text_layer(text: str) -> TextLayerSelection:
    """Select exact excerpts and exclude graph/formula/table-dependent blocks conservatively."""
    matches = list(_ARTICLE.finditer(text))
    article_mode = bool(matches)
    if not matches:
        matches = list(_POINT.finditer(text))
    boundaries = [0, *(item.start() for item in matches if item.start() > 0), len(text)]
    labels = {item.start(): item.group("number") for item in matches}
    fragments: list[CorpusFragment] = []
    excluded = 0
    for start, end in pairwise(boundaries):
        block = text[start:end]
        if _GRAPHICS_DEPENDENCY.search(block) or _BARE_LOSS.search(block):
            excluded += 1
            continue
        label = labels.get(start)
        article = label if article_mode else None
        point = label if not article_mode else None
        for piece in _pieces(block):
            ordinal = len(fragments) + 1
            fragments.append(
                CorpusFragment(
                    ordinal=ordinal,
                    article=article,
                    part=None,
                    point=point,
                    heading=None,
                    structural_path=(
                        f"article:{article}"
                        if article
                        else f"point:{point}"
                        if point
                        else "document:text"
                    )
                    + f"/excerpt:{ordinal}",
                    text=piece,
                )
            )
    if not fragments:
        raise ValueError("no usable textual fragments; original requires separate review")
    if len(fragments) > 10_000:
        raise ValueError("textual fragment count exceeds contract")
    return TextLayerSelection(fragments, excluded)
