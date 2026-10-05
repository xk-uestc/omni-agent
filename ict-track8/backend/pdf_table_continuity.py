"""Explicit source-preserving cross-page table assembly, with review candidates."""
from copy import deepcopy
import re
import unicodedata
from .pdf_table_preview import PdfTablePreviewAgent


class PdfTableContinuityAgent:
    @staticmethod
    def validate_links(links, selected):
        if not isinstance(links,list) or len(links)>7:raise ValueError('一次最多指定7条跨页表格连接')
        selected=set(selected);edges=[];outgoing=set();incoming=set()
        for item in links:
            keys={'from_page','from_table','to_page','to_table'}
            if not isinstance(item,dict) or set(item)!=keys or any(type(item[k]) is not int for k in keys):
                raise ValueError('跨页表格连接需要明确起止页码和表格序号')
            left=(item['from_page'],item['from_table']);right=(item['to_page'],item['to_table'])
            if left not in selected or right not in selected or right[0]!=left[0]+1:
                raise ValueError('仅能连接已选择的相邻PDF页面表格')
            if left in outgoing or right in incoming:raise ValueError('跨页表格连接不能重复或分叉')
            outgoing.add(left);incoming.add(right);edges.append((left,right))
        return edges

    @staticmethod
    def headers(table):
        return [tuple(unicodedata.normalize('NFKC',part).strip() for part in column['header_path'])
                for column in table['columns']]

    @staticmethod
    def usable(table):
        try:
            sha=table['source_pdf_sha256'];page=table['page_no'];cols=table['column_count']
            return (table.get('status')=='structure_observed' and isinstance(sha,str)
                and re.fullmatch(r'[a-f0-9]{64}',sha) is not None and type(page) is int and page>0
                and type(cols) is int and 1<=cols<=64 and len(table['columns'])==cols
                and [column['column'] for column in table['columns']]==list(range(cols))
                and all(isinstance(column['header_path'],list) and all(isinstance(part,str) for part in column['header_path']) for column in table['columns'])
                and 0<=table['header_rows']<table['row_count']<=100
                and len(table['body_cells'])==table['row_count']-table['header_rows']
                and all(len(row)==cols for row in table['body_cells'])
                and PdfTablePreviewAgent.coordinates_match(table,sha,page))
        except (KeyError,TypeError,ValueError):return False

    def compatible(self,left,right,*,inherited=None):
        if not self.usable(left) or not self.usable(right):return 'incomplete_or_unlocated_table'
        if left['source_pdf_sha256']!=right['source_pdf_sha256']:return 'different_pdf_sources'
        if right['page_no']!=left['page_no']+1:return 'nonadjacent_pages'
        if left['column_count']!=right['column_count']:return 'column_count_changed'
        left_headers=inherited or self.headers(left);right_headers=self.headers(right)
        if any(left_headers) and (any(not path or not all(path) for path in left_headers) or len(set(left_headers))!=len(left_headers)):
            return 'ambiguous_header_paths'
        if right['header_rows']:
            if not any(left_headers):return 'first_page_header_missing'
            if any(not path or not all(path) for path in right_headers) or len(set(right_headers))!=len(right_headers):
                return 'ambiguous_header_paths'
            if left_headers!=right_headers:return 'header_paths_changed'
        return None

    def run(self,structures,links=()):
        mapping={(table['page_no'],table['table_index']):table for table in structures}
        if len(mapping)!=len(structures):raise ValueError('PDF表格选择存在重复')
        edges=self.validate_links(list(links),mapping)
        candidates=[]
        page_counts={p:sum(key[0]==p for key in mapping) for p,_ in mapping}
        for left_key,left in mapping.items():
            for right_key,right in mapping.items():
                if right_key[0]!=left_key[0]+1:continue
                reason=self.compatible(left,right)
                candidates.append({'from_page':left_key[0],'from_table':left_key[1],
                    'to_page':right_key[0],'to_table':right_key[1],
                    'status':'needs_confirmation' if reason is None else 'rejected',
                    'reason':reason or ('multiple_tables_on_page' if page_counts[left_key[0]]>1 or page_counts[right_key[0]]>1
                        else 'matching_repeated_headers' if right['header_rows'] else 'continuation_without_header'),
                    'automatic_join':False})
        successors=dict(edges);incoming={right for _,right in edges};assemblies=[]
        for root in successors:
            if root in incoming:continue
            chain=[root]
            while chain[-1] in successors:chain.append(successors[chain[-1]])
            tables=[mapping[key] for key in chain]
            headers=self.headers(tables[0]) if self.usable(tables[0]) else []
            reason=None
            for left,right in zip(tables,tables[1:]):
                reason=self.compatible(left,right,inherited=headers)
                if reason:break
            identity={'tables':[{'page_no':p,'table_index':t} for p,t in chain]}
            if reason:
                assemblies.append({**identity,'status':'rejected','reason':reason,'rows':[],
                    'cell_values_verified':False,'ready_for_sql_import':False});continue
            rows=[];segments=[]
            for table in tables:
                start=len(rows)
                for cells in table['body_cells']:
                    rows.append({'page_no':table['page_no'],'table_index':table['table_index'],
                        'original_row':cells[0]['row'],'cells':deepcopy(cells)})
                segments.append({'page_no':table['page_no'],'table_index':table['table_index'],
                    'start_row':start,'body_row_count':len(rows)-start,
                    'unbound_region_indices':deepcopy(table.get('unbound_region_indices',[])),
                    'repeated_header_rows':table['header_rows'],'columns':deepcopy(table['columns'])})
            assemblies.append({**identity,'status':'assembled_preview','confirmation':'explicit_user_links',
                'source_pdf_sha256':tables[0]['source_pdf_sha256'],'columns':deepcopy(tables[0]['columns']),
                'column_count':tables[0]['column_count'],'row_count':len(rows),'rows':rows,'segments':segments,
                'cell_values_verified':False,'header_meanings_verified':False,'ready_for_sql_import':False,
                'all_observations_assigned':not any(table.get('unbound_region_indices') for table in tables),
                'warnings':['仅按你确认的续表关系拼接；不能据此证明表格语义连续或金额正确。',
                    '保留重复数据行、空白和合并范围；不填零，不跨页猜补拆开的单元格。']+
                    (['部分OCR观察框未归入单元格，逐页未绑定编号已保留，请核对空白和缺失文字。']
                        if any(table.get('unbound_region_indices') for table in tables) else [])})
        from .table_arithmetic import TableArithmeticAgent
        for assembly in assemblies:
            if assembly['status']=='assembled_preview':
                assembly['arithmetic_check']=TableArithmeticAgent().run({**assembly,'body_cells':[row['cells'] for row in assembly['rows']]})
        return {'agent':'PdfTableContinuityAgent','candidates':candidates,'assemblies':assemblies,
                'native_text_replaced':False,'automatic_join':False}
