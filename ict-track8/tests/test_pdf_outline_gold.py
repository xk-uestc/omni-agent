import json
from pathlib import Path

import pytest

from backend.chunk_cleaning import DocumentChunker

ROOT=Path(__file__).resolve().parents[2]
CASES=json.loads((ROOT/'ict-track8/eval/outline_cases.json').read_text(encoding='utf-8'))['cases']


@pytest.mark.parametrize('case',CASES,ids=lambda case:case['id'])
def test_actual_pdf_heading_paths_match_frozen_gold(case):
    parsed=DocumentChunker().parse_pdf((ROOT/'samples/outline'/f"{case['id']}.pdf").read_bytes()).to_dict()
    assert [c['text'] for c in parsed['chunks'] if c['content_type']=='heading']==case['headings']
    for gold in case['facts']:
        assert any(gold['text'] in c['text'] and c['content_type']!='heading'
                   and c['title_path']==gold['path'] and c['page_no']==gold['page']
                   and c['source_locator'].startswith(f"page:{gold['page']}:") for c in parsed['chunks'])
    for warning in case.get('warnings',[]):
        assert any(warning in actual for actual in parsed['warnings'])
