"""Mixed text-outline parsing with line evidence and explicit uncertainty flags."""
import re
from .document_analysis import DocumentAnalyzer


def chunk_text(chunker, text, *, document_id, modality):
    # Whole-document cleaning collapses blank lines, shifting every subsequent
    # locator away from its original file. Clean each source line separately.
    raw_lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    lines = [chunker.clean_text(line) for line in raw_lines]
    explicit = {item.line_no: item for item in DocumentAnalyzer._headings(lines)}
    chunks, warnings, stack, body = [], [], [], []
    body_start = 1

    def flush(end):
        nonlocal body
        if body:
            chunks.extend(chunker._make_chunks(document_id, modality, 'paragraph', '\n'.join(body),
                locator=f'lines:{body_start}-{end}', title_path=tuple(title for _,title in stack), overlap=True,
                metadata={'line_start': body_start, 'line_end': end}))
            body = []

    for number, line in enumerate(lines, 1):
        heading = explicit.get(number)
        heuristic = bool(re.fullmatch(r'【[^【】。；]{2,60}】', line))
        if heading or heuristic:
            flush(number-1)
            level = heading.level if heading else (2 if stack else 1)
            if stack and level > stack[-1][0]+1:
                warnings.append(f'outline_level_jump:line:{number}')
            if heuristic:
                warnings.append(f'heuristic_heading:line:{number}')
            stack = [(old_level,title) for old_level,title in stack if old_level < level] + [(level,line)]
            chunks.extend(chunker._make_chunks(document_id, modality, 'heading', line,
                locator=f'line:{number}', title_path=tuple(title for _,title in stack), overlap=False,
                metadata={'heading_rule': heading.rule if heading else 'bracket_heuristic', 'declared_level': level}))
        else:
            if not body:
                if not line:
                    continue
                body_start = number
            body.append(line)
    flush(len(lines))
    return {'chunks':[chunk.to_dict() for chunk in chunks], 'warnings':list(dict.fromkeys(warnings)),
            'stats':{'source_lines':len(lines),'headings':len(explicit),'chunks':len(chunks)}}
