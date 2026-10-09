"""Mixed text-outline parsing with line evidence and explicit uncertainty flags."""
import re

from .document_analysis import DocumentAnalyzer


_ORDERED_LIST_PREFIX = re.compile(r'^\s{0,3}\d{1,3}\s*[.)、]\s+')
_PROCEDURE_START = re.compile(
    r'^(?:将|请|按|旋转|拔|插|连接|安装|更换|打开|关闭|检查|取出|放入|选择|点击|保持|确保|拧|拆|拉|推|'
    r'每月|每次|需|可|不得|切勿|勿|使用|然后|如需|先|依次)'
)
_PROCEDURE_START_EN = re.compile(
    r'^(?:press|install|remove|insert|connect|turn|rotate|open|close|hold|push|pull|select|choose|clean|replace|'
    r'check|ensure|tighten|unplug|plug|use|pour|fill|set|slide|do not|never)\b', re.I
)
_SENTENCE_PUNCTUATION = re.compile(r'[。！？!?；;]')


def _is_ordered_procedure(line):
    prefix = _ORDERED_LIST_PREFIX.match(line)
    if not prefix:
        return False
    item = line[prefix.end():].strip()
    return bool(_SENTENCE_PUNCTUATION.search(item) or _PROCEDURE_START.match(item)
                or _PROCEDURE_START_EN.match(item))


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
        if heading and _is_ordered_procedure(line):
            heading = None
        heuristic = bool(heading and heading.rule == 'bracket_heuristic')
        if heading or heuristic:
            flush(number-1)
            level = (2 if stack else 1) if heuristic else heading.level
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
