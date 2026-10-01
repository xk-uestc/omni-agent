"""Evaluator-only checks; no backend execution, real questions, credentials or API."""
import unittest
from types import SimpleNamespace
from freeze_new_document_ohr import pick, DOMAINS, CATEGORIES
from evaluate_northwind_generalization import assess, allowed


class Contracts(unittest.TestCase):
    def test_new_document_selection_uses_only_metadata_and_keeps_all_categories(self):
        rows, members = [], {}
        for d, domain in enumerate(DOMAINS):
            name = domain+'/unseen'; members[name] = SimpleNamespace(file_size=100+d)
            for c, category in enumerate(CATEGORIES):
                rows.append({'ID': f'{d}-{c}', 'doc_name': name, 'questions': f'Unseen {d} {c}',
                             'evidence_source': category})  # No gold fields exist.
        rows.append({'ID': 'old', 'doc_name': 'academic/old', 'questions': 'old', 'evidence_source': 'text'})
        members['academic/old'] = SimpleNamespace(file_size=1)
        chosen, docs = pick(rows, members, {'academic/old'}, {'old'}, set())
        self.assertEqual(len(chosen), 18); self.assertEqual(len(docs), 7)
        self.assertEqual(set(r['evidence_source'] for r in chosen), set(CATEGORIES))
        self.assertTrue(all(sum(r['doc_name'] == doc for r in chosen) >= 2 for doc in docs))
        self.assertNotIn('academic/old', docs)

    def test_duplicate_rows_are_not_lost_by_set_scoring(self):
        result = {'status': 'ok', 'rows': [{'name': 'A', 'count': 2}]}
        self.assertFalse(assess({}, result, [('A',2),('A',2)])['execution_result'])

    def test_alias_and_column_order_ignored_but_row_association_preserved(self):
        result = {'status': 'ok', 'rows': [{'metric': 2, 'label': 'A'}, {'metric': 3, 'label': 'B'}]}
        self.assertTrue(assess({}, result, [('B',3),('A',2)])['execution_result'])
        self.assertFalse(assess({}, result, [('B',2),('A',3)])['execution_result'])

    def test_numeric_tolerance_does_not_coerce_text_or_null(self):
        self.assertTrue(assess({}, {'status':'ok','rows':[{'x':1.00000001}]}, [(1,)])['execution_result'])
        self.assertFalse(assess({}, {'status':'ok','rows':[{'x':'1'}]}, [(1,)])['execution_result'])
        self.assertFalse(assess({}, {'status':'ok','rows':[{'x':0}]}, [(None,)])['execution_result'])

    def test_ranking_order_is_checked_separately_from_result_bag(self):
        case = {'metric_order': 'descending'}
        result = {'status':'ok','rows':[{'name':'A','metric':2},{'name':'B','metric':3}]}
        checks = assess(case, result, [('B',3),('A',2)])
        self.assertTrue(checks['execution_result']); self.assertFalse(checks['ranking_order'])

    def test_model_audit_requires_real_allowed_model_and_medium(self):
        audit = {'model':'gpt-6-luna','reasoning':'medium','model_verified':True,
                 'status':'completed','http_status':200,'response_model':'gpt-6-luna'}
        self.assertTrue(allowed(audit))
        for key, value in [('reasoning','high'),('response_model','gpt-6-sol'),
                           ('http_status',401),('model_verified',False)]:
            self.assertFalse(allowed({**audit,key:value}))

    def test_optional_rank_display_is_independently_verified(self):
        case = {'metric_order': 'descending'}
        result = {'status': 'ok', 'plan': {'analysis_mode': 'rank'}, 'rows': [
            {'name': 'A', 'stock': 7, '排名': 1}, {'name': 'B', 'stock': 7, '排名': 1},
            {'name': 'C', 'stock': 3, '排名': 2}]}
        expected = [('A',7), ('B',7), ('C',3)]
        self.assertTrue(all(assess(case, result, expected).values()))
        result['rows'][1]['排名'] = 2
        self.assertFalse(assess(case, result, expected)['ranking_order'])
        result['rows'][1]['排名'] = 1
        result['rows'][2]['排名'] = 3  # Sparse RANK is not dense rank.
        self.assertFalse(assess(case, result, expected)['ranking_order'])

    def test_optional_rank_does_not_hide_bad_or_extra_values(self):
        case = {'metric_order': 'descending'}
        base = {'status':'ok', 'plan':{'analysis_mode':'rank'}}
        for rows in ([{'name':'A','stock':8,'排名':1}],
                     [{'name':'A','stock':7,'排名':True}],
                     [{'name':'A','stock':7,'排名':1,'extra':0}]):
            self.assertFalse(all(assess(case, {**base,'rows':rows}, [('A',7)]).values()))
        self.assertFalse(all(assess({}, {**base,'rows':[{'name':'A','stock':7,'排名':1}]}, [('A',7)]).values()))


if __name__ == '__main__': unittest.main()
