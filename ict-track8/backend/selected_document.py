"""Explicit document scope and a source-backed, bounded structural overview."""
import re


class SelectedDocumentMissing(ValueError):
    """Safe, actionable public error for a stale selection."""


def scope_reference_conflict(question, selected_title):
    """Do not let a composer selection silently replace a requested history source."""
    from .document_dialogue import DocumentDialogueAgent
    text = question.strip().rstrip('？?。！!')
    if (re.match(r'^(?:回到|接着).*(?:查询编号|SQL查询|问数查询|数据库查询|待补查询)', text, re.I)
            or re.match(r'^(?:选择对话来源|(?:继续|恢复)待补查询)', text)
            or re.search(r'q_[a-f0-9]{32}', text, re.I)
            or re.match(r'^(?:前者|后者|前一份文档|后一份文档|之前那份文档|刚才那份文档|那份文档|那份资料)', text)):
        return True
    explicit = DocumentDialogueAgent.TITLE.fullmatch(text)
    switch = DocumentDialogueAgent.SWITCH.fullmatch(text)
    return bool((explicit and explicit[1] != selected_title) or switch)


def is_overview_request(question):
    # Only a bare document overview request, never a compound analysis/calculation.
    return bool(re.fullmatch(
        r'\s*(?:请|请你|帮我|请帮我)?\s*(?:分析|概览|总结|介绍|概括|解读)'
        r'\s*(?:一下)?\s*(?:(?:这个|这份|该|所选|当前|刚上传的|上传的)\s*)?'
        r'(?:文件|文档|资料|PDF|pdf)(?:的内容)?\s*[。！？?!]?\s*', question))


def document_overview(knowledge, document_id):
    document = knowledge.document(document_id)
    digest = document['sha256']
    knowledge.verify_source(document_id, expected_sha256=digest)
    chunks = document.get('chunks', [])
    elements = document.get('elements', [])
    stats = document.get('stats') or {}
    pages = sorted({chunk['page_no'] for chunk in chunks if type(chunk.get('page_no')) is int})
    page_count = stats.get('page_count') or len(pages) or None
    tables = [element for element in elements if element.get('kind') == 'table']
    formula_records = [element for element in elements if element.get('kind') == 'formula']
    formulas = (document.get('analysis') or {}).get('formulas') or []
    table_count = stats.get('native_table_count')
    if table_count is None:
        table_count = len(tables) if tables else None
    formula_count = len(formula_records) if formula_records else len(formulas)
    candidates = [chunk for chunk in chunks if str(chunk.get('text') or '').strip()]
    # PDF line chunks often begin with only a title/section name. Prefer opening
    # body excerpts while retaining source order; this is still not a summary.
    body = [chunk for chunk in candidates if len(str(chunk['text']).strip()) >= 16
            and not re.match(r'^(?:第[一二三四五六七八九十0-9]+[章节]|[一二三四五六七八九十]+[、.])\s*[^。；;]{0,24}$', str(chunk['text']).strip())]
    selected = (body or candidates)[:4]
    citations = []
    lines = [f'已读取《{document["title"]}》的入库结构。', '',
             f'- 可检索片段：{len(chunks)} 个。']
    if page_count is not None:
        lines.append(f'- 页数：{page_count} 页。')
    if table_count is not None:
        lines.append(f'- 已识别原生表格：{table_count} 张。')
    lines.append(f'- 公式候选：{formula_count} 个；候选数量不代表均可直接计算。')
    if selected:
        lines.extend(['', '以下为开头部分的正文摘录，可据此继续提问：', ''])
    for index, chunk in enumerate(selected, 1):
        snippet = str(chunk['text']).strip()[:420]
        metadata = {**chunk.get('metadata', {}), 'document_id': document_id,
                    'chunk_id': chunk['chunk_id'], 'source_sha256': digest,
                    'source_locator': chunk['source_locator'], 'page_no': chunk.get('page_no')}
        citations.append({'citation_id': index, 'document_id': document_id,
                          'title': document['title'], 'snippet': snippet,
                          'source_uri': f'/api/v1/knowledge/documents/{document_id}/original',
                          'metadata': metadata})
        lines.append(f'{snippet} [{index}]')
        lines.append('')
    warning = '这是实际入库结构与部分原文摘录，未对全文逐页做语义总结。请继续指定要分析的主题、表格或公式。'
    lines.extend(['', warning])
    knowledge.verify_source(document_id, expected_sha256=digest)
    return {'status': 'ok', 'answer': '\n'.join(lines),
            'answer_mode': 'document_structure_overview', 'answer_completeness': 'bounded_overview',
            'citations': citations, 'trace': [],
            'document': {'document_id': document_id, 'title': document['title'],
                         'source_sha256': digest, 'page_count': page_count,
                         'chunk_count': len(chunks), 'native_table_count': table_count,
                         'formula_candidate_count': formula_count,
                         'warnings': list(document.get('warnings') or [])[:8]},
            'coverage': {'scope': 'selected_document_structure_and_opening_excerpts',
                         'total_chunks': len(chunks), 'excerpt_count': len(selected),
                         'excerpt_pages': sorted({chunk['page_no'] for chunk in selected if type(chunk.get('page_no')) is int}),
                         'full_document_semantic_analysis': False, 'note': warning}}
