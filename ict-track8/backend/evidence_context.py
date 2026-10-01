"""Literal source boundaries and bounded generation excerpts, without retrieval changes."""
from __future__ import annotations

import hashlib
import re


MAX_EVIDENCE_CHARS = 1800
# Fresh native paragraphs/tables may exceed a retrieval chunk. Plain text
# prefixes keep their old cap; the independent aggregate cap remains fixed.
MAX_NATIVE_EVIDENCE_CHARS = 3200
MAX_TOTAL_EVIDENCE_CHARS = 12000
MAX_EVIDENCE_ITEMS = 8
_ABBREVIATIONS = frozenset({
    'mr', 'mrs', 'ms', 'dr', 'prof', 'sr', 'jr', 'st', 'vs', 'etc',
    'inc', 'ltd', 'co', 'corp', 'dept', 'fig', 'no', 'e.g', 'i.e', 'u.s', 'u.k',
})
_SEPARATORS = re.compile(r'[。；;！？!?]+|\n\s*\n')
_TOKEN_BEFORE_DOT = re.compile(r'(?:[A-Za-z]+\.)*[A-Za-z]+$')


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Keep literal offsets; a PDF single newline is not a fact boundary.

    Dotted acronyms, initials, common abbreviations and decimal points cannot
    break a fact. Ambiguous abbreviation endings deliberately stay attached
    to their following context rather than authorizing a smaller quotation.
    """
    separators = [(m.start(), m.end()) for m in _SEPARATORS.finditer(text)]
    for index, character in enumerate(text):
        if character != '.':
            continue
        before = text[index - 1] if index else ''
        after = text[index + 1] if index + 1 < len(text) else ''
        if before.isdigit() and after.isdigit():
            continue
        if after and not after.isspace():
            continue
        # Bounded look-behind avoids quadratic slicing on punctuation-heavy
        # sources; supported abbreviation tokens are far shorter than 80.
        token = _TOKEN_BEFORE_DOT.search(text[max(0, index - 80):index])
        word = token.group() if token else ''
        if (word.lower() in _ABBREVIATIONS or len(word) == 1 and word.isalpha()
                or re.fullmatch(r'(?:[A-Za-z]\.)+[A-Za-z]', word)):
            continue
        separators.append((index, index + 1))
    spans, start = [], 0
    for left, right in sorted(separators):
        if left < start:
            continue
        if text[start:left].strip():
            spans.append((start, left))
        start = right
    if text[start:].strip():
        spans.append((start, len(text)))
    return spans


def sentence_texts(text: str) -> list[str]:
    return [text[left:right] for left, right in sentence_spans(text)]


def bounded_prefix(text: str, limit: int) -> tuple[str, int, bool]:
    """Use a complete prefix, never a keyword window that deletes source scope.

    When a long first fact has no safe boundary within the cap, return no
    evidence. A single PDF line break, colon or comma is never a cut point.
    """
    if len(text) <= limit:
        return text, len(text), False
    end = 0
    for _, right in sentence_spans(text):
        # Include the original terminal punctuation when it fits.
        if right < len(text) and text[right] in '.。；;！？!?':
            right += 1
        if right <= limit:
            end = right
        else:
            break
    return text[:end], end, True


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()
