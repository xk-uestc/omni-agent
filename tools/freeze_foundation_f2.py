"""Freeze a source-family-isolated, synthetic but parser-real F2 RAG benchmark."""
import json
from foundation_common import ROOT, dump, sha

BENCH = ROOT / 'benchmarks/foundation_f2_20261011'
FAMILIES = [
    ('cedar', '杉木', '北岬研究院', 'E-17'), ('harbor', '港湾', '东湾实验室', 'H-29'),
    ('meadow', '草原', '西岭观测站', 'M-43'), ('summit', '峰顶', '南谷中心', 'S-58'),
    ('island', '岛屿', '青屿研究所', 'I-64'), ('orchard', '果园', '云田监测局', 'O-82'),
]


def make_family(en, zh, institution, code):
    """Return 5 source files and 8 independent queries for one family."""
    did = f'f2-{en}'
    rows = []
    for i in range(1, 31):
        status = '合格' if i % 3 != 0 else '隔离'
        category = ['北区', '南区', '北区', '东区'][i % 4]
        rows.append(f'{code}-{i:02} | {category} | {status} | {10+i}.5 | 2026-04-{(i%28)+1:02}')
    target_rows = [r for r in rows if ' | 北区 | 合格 | ' in r]
    split_pages = [
        ['批次验收记录', '编号 | 区域 | 结论 | 读数(毫克/升) | 日期', *rows[:10]],
        ['批次验收记录（续）', '编号 | 区域 | 结论 | 读数(毫克/升) | 日期', *rows[10:20], '注：本页表头重复，单位沿用第一页。'],
        ['批次验收记录（续）', '编号 | 区域 | 结论 | 读数(毫克/升) | 日期', *rows[20:], '脚注：隔离记录不纳入合格样本统计。'],
    ]
    long_pages = []
    for p in range(1, 13):
        filler = f'{en} {zh} calibration inspection monitoring station quarterly report archived measurement '
        body = ' '.join([filler * 10 for _ in range(4)])
        if p == 3:
            body += f' 唯一有效校准周期为{23 + len(en)}天；记录日期2026-05-17，适用于{institution}的代码{code}。'
        if p == 9:
            body += ' 例外：停机日不计入周期；不得将维修等待时间纳入。'
        long_pages.append(body)
    policy_pages = [
        f'{institution}季度运行公报。标题重复：季度运行公报。对象代码{code}，年份2026。',
        f'对象“林青”在{institution}任采样负责人；另一份同名记录中的林青不属于本机构。',
        f'2026年预算为{1200+len(en)*7}元，明确不含设备租赁费；仅对{code}有效。',
        f'不得把隔离状态记录计入合格记录；停机日不计校准周期。',
    ]
    misleading = (f'{institution}季度运行公报。对象代码{code}，年份2025。林青是临时联络员，'
                  f'预算{900+len(en)}元含设备租赁费。该版本已撤销，不可作为2026年现行口径。')
    docs = [
        {'id': did+'-policy-2026', 'family': en, 'title': '季度运行公报', 'type': 'pdf', 'pages': policy_pages},
        {'id': did+'-policy-2025', 'family': en, 'title': '季度运行公报', 'type': 'txt', 'text': misleading},
        {'id': did+'-long', 'family': en, 'title': '校准与巡检总册', 'type': 'pdf', 'pages': long_pages},
        {'id': did+'-table', 'family': en, 'title': '批次验收记录', 'type': 'pdf', 'pages': ['\n'.join(p) for p in split_pages]},
        {'id': did+'-other-org', 'family': en, 'title': '季度运行公报', 'type': 'txt',
         'text': f'另一个样本库。对象林青属于{institution}的邻近机构{institution}附属组，不适用代码{code}。'}
    ]
    tasks = [
        {'kind':'same_title_time','query':f'{zh} 研究站2026年公报中，代码{code}的预算多少？租赁费是否包含？',
         'required':{docs[0]['id']:[f'{1200+len(en)*7}元','不含设备租赁费',code]},'wrong_binding_sources':[docs[1]['id']],'expected_abstain':False},
        {'kind':'long_distributed_conditions','query':f'{institution}代码{code}校准周期几天？计算时停机日和维修等待时间如何处理？',
         'required':{docs[2]['id']:[f'{23+len(en)}天','停机日不计入周期','不得将维修等待时间纳入']},'expected_abstain':False},
        {'kind':'complete_table_enumeration','query':f'请完整列出{zh}代码{code}批次中北区且结论合格的所有编号，并带上读数和日期。',
         'required':{docs[3]['id']:target_rows},'expected_abstain':False},
        {'kind':'cross_page_header_unit','query':f'{institution}批次验收记录里，所有北区合格记录使用什么单位？逐条列出编号、读数、日期，并说明隔离项是否计入。',
         'required':{docs[3]['id']:target_rows+['读数(毫克/升)','隔离记录不纳入合格样本统计']},'expected_abstain':False},
        {'kind':'same_name_org_binding','query':f'{institution}的林青在2026公报中担任什么职务？',
         'required':{docs[0]['id']:[f'林青”在{institution}任采样负责人']},'wrong_binding_sources':[docs[4]['id']],'expected_abstain':False},
        {'kind':'negation_date_scope','query':f'只看{code}的2026现行季度公报：租赁费包含在预算里吗？隔离状态可以计入合格样本吗？',
         'required':{docs[0]['id']:['不含设备租赁费','不得把隔离状态记录计入合格记录']},'expected_abstain':False},
        {'kind':'chinese_alias_evidence','query':f'{zh}那边林青的岗位是什么？预算是多少钱（口语问法）？',
         'required':{docs[0]['id']:[f'林青”在{institution}任采样负责人',f'{1200+len(en)*7}元']},'expected_abstain':False},
        {'kind':'unsupported_abstention','query':f'{institution}{code}在2026年授权的月球站点密码是什么？',
         'required':{},'expected_abstain':True},
    ]
    return docs, tasks


def main():
    docs, splits, scoring = [], {'dev': [], 'retained': []}, {}
    for i, family in enumerate(FAMILIES):
        split = 'dev' if i < 3 else 'retained'
        source_docs, tasks = make_family(*family)
        docs.extend(source_docs)
        for j, task in enumerate(tasks, 1):
            task_id = f'{split}-{family[0]}-{j:02}'
            task = {'id': task_id, 'source_family': family[0], **task, 'top_k': 4}
            splits[split].append(task)
            scoring[task_id] = {'required': task['required'], 'expected_abstain': task['expected_abstain']}
    BENCH.mkdir(exist_ok=False)
    dump(BENCH/'documents.json', docs)
    for split, tasks in splits.items(): dump(BENCH/f'{split}.json', tasks)
    dump(BENCH/'scoring.json', scoring)
    dump(BENCH/'protocol.json', {'task_count':48,'dev_tasks':24,'retained_tasks':24,
        'source_family_partition':{'dev':[x[0] for x in FAMILIES[:3]],'retained':[x[0] for x in FAMILIES[3:]]},
        'documents_per_family':5,'top_k':4,'answer_generation':'not_run','dataset':'authored synthetic, parser-real PDF and TXT; not official benchmark',
        'freeze_before_algorithm_change':True})
    files=[p for p in BENCH.glob('*.json')]
    files += [ROOT/'tools/freeze_foundation_f2.py',ROOT/'tools/evaluate_foundation_f2.py']
    dump(BENCH/'manifest.json',{'files':{str(p.relative_to(ROOT)):sha(p) for p in files}})
    print('Frozen F2: 48 tasks; three source families per split; heldout score sealed until final evaluation.')


if __name__=='__main__': main()
