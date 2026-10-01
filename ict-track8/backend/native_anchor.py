"""Lossless native-PDF anchor locations, with explicit legacy wrap aliases.

An ASCII line-ending hyphen is not proof of a soft hyphen. Optional deletion
is only a navigation alias for older stored chunks; callers return the actual
native text and pin its original source bytes. Every literal/alias occurrence
participates in uniqueness, including overlapping occurrences.
"""
from __future__ import annotations

import re


ANCHOR_POLICY_VERSION = 'native-literal-and-legacy-wrap-alias-union-v2'

# Only an alphabetic word (at least two letters) or dotted acronym followed
# by a lowercase continuation can have a legacy alias. Numeric/operator
# boundaries, a standalone minus, internal compound hyphens, Unicode minus
# signs and a new uppercase sentence remain literal. These aliases do not
# establish that the source word should actually be dehyphenated.
_WRAP = re.compile(
    r'(?<![A-Za-z0-9._+\-*/=−–—])(?:[A-Za-z]{2,}|(?:[A-Za-z]\.){2,})'
    r'(?P<hyphen>-)[ \t]*\n[ \t]*(?=[a-z])')


def line_wrap_alias_positions(text: str) -> frozenset[int]:
    """Original offsets of hyphens eligible for navigation-only deletion."""
    return frozenset(match.start('hyphen') for match in _WRAP.finditer(text))


def literal_compact(text: str) -> str:
    """Whitespace equivalence only; all signs and hyphens remain significant."""
    return re.sub(r'\s+', '', text)


def anchor_ranges(source: str, anchor: str) -> list[tuple[int, int]]:
    """Union all literal/optional-wrap matches as original half-open ranges.

    Marking eligible source hyphens lets one regex match arbitrary subsets of
    aliases without enumerating 2**N strings. A query's internal hyphen stays
    mandatory, and a marked source hyphen can be skipped only between query
    characters. Match offsets map back to the unmodified literal source.
    """
    if not isinstance(source, str) or not isinstance(anchor, str):
        return []
    anchor_characters = [(index, char) for index, char in enumerate(anchor)
                         if not char.isspace()]
    if not anchor_characters:
        return []
    # The sentinel is internal and absent from both inputs; it cannot collide
    # with a real private-use glyph or alter the returned evidence.
    marker = next((chr(value) for value in range(0xF0000, 0xF0100)
                   if chr(value) not in source and chr(value) not in anchor), None)
    if marker is None:
        return []
    source_wraps, query_wraps = (line_wrap_alias_positions(source),
                                line_wrap_alias_positions(anchor))
    offsets, marked = [], []
    for index, char in enumerate(source):
        if char.isspace():
            continue
        offsets.append(index)
        marked.append(marker if index in source_wraps else char)
    optional_source_hyphen = '(?>' + re.escape(marker) + '*)'
    query = []
    for position, (index, char) in enumerate(anchor_characters):
        if char == '-':
            atom = '(?:-|'+ re.escape(marker) + ')'
            if index in query_wraps:
                # Its next query character is proven lowercase by _WRAP.
                # Consuming a matching hyphen cannot steal a later mandatory
                # '-' atom, so lock this equivalent choice against exponential
                # consume/skip permutations on an eventual failed suffix.
                atom = '(?>' + atom + '?)'
        else:
            atom = re.escape(char)
            # Never skip before a mandatory query '-': that character must be
            # allowed to consume a marked native hyphen itself. Before every
            # other character, consuming optional source hyphens is uniquely
            # determined and may be atomic. Do not skip at the anchor's edge.
            if position:
                atom = optional_source_hyphen + atom
        query.append(atom)
    pattern = re.compile('(?=(' + ''.join(query) + '))')
    matches = set()
    for match in pattern.finditer(''.join(marked)):
        left, right = match.span(1)
        if right > left:
            matches.add((offsets[left], offsets[right - 1] + 1))
    return sorted(matches)
