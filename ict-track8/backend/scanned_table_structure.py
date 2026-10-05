"""Column paths from explicitly selected OCR header rows; never verify values."""
from copy import deepcopy


class ScannedTableStructureAgent:
    def run(self, metadata, *, table_index=0, header_rows=1):
        rejected = lambda reason: {'status': 'unavailable', 'reason': reason,
                                  'cell_values_verified': False}
        if type(table_index) is not int or type(header_rows) is not int or not 0 <= table_index < 32 or not 0 <= header_rows <= 5:
            return rejected('invalid_header_selection')
        # The grid may use a different frame from the selected prose OCR.
        evidence = metadata.get('scanned_table_evidence') or metadata
        orientation=evidence.get('orientation') or {}
        if orientation.get('status')=='estimated' and orientation.get('rotation_ccw_degrees') not in (None,0):
            return rejected('grid_requires_upright_frame')
        tables = evidence.get('scanned_grids', {}).get('tables', [])
        if not isinstance(tables, list) or table_index >= len(tables):
            return rejected('selected_grid_unavailable')
        table = tables[table_index]
        try:
            rows, cols = table['row_count'], table['column_count']
            cells = table['cells']
            if (table['status'] != 'layout_observed' or type(rows) is not int or type(cols) is not int
                    or not header_rows < rows <= 100 or not 1 <= cols <= 64 or rows * cols > 1000
                    or len(cells) != rows or any(len(row) != cols for row in cells)):
                return rejected('invalid_or_incomplete_grid')
            occupied = {}
            anchors = {}
            for r, row in enumerate(cells):
                for c, cell in enumerate(row):
                    if cell['row'] != r or cell['column'] != c:
                        return rejected('cell_position_mismatch')
                    if cell['status'] == 'covered_by_merged_cell':
                        continue
                    rs, cs = cell['rowspan'], cell['colspan']
                    if (type(rs) is not int or type(cs) is not int or rs < 1 or cs < 1
                            or r + rs > rows or c + cs > cols):
                        return rejected('invalid_cell_span')
                    if r < header_rows < r + rs:
                        return rejected('header_boundary_crosses_merged_cell')
                    anchors[r, c] = cell
                    for rr in range(r, r + rs):
                        for cc in range(c, c + cs):
                            if (rr, cc) in occupied:
                                return rejected('overlapping_cell_spans')
                            occupied[rr, cc] = (r, c)
            if len(occupied) != rows * cols:
                return rejected('uncovered_grid_positions')
            for r, row in enumerate(cells):
                for c, cell in enumerate(row):
                    owner = occupied[r, c]
                    if owner != (r, c) and (cell['status'] != 'covered_by_merged_cell'
                                            or cell.get('anchor') != list(owner)):
                        return rejected('merged_anchor_mismatch')
            columns = []
            for c in range(cols):
                seen, path, refs = set(), [], []
                for r in range(header_rows):
                    owner = occupied[r, c]
                    if owner in seen:
                        continue
                    seen.add(owner)
                    cell = anchors[owner]
                    text = cell['text']
                    if not isinstance(text, str) or len(text) > 4000:
                        return rejected('invalid_header_text')
                    path.append(text.strip())
                    refs.append({'row': owner[0], 'column': owner[1],
                                 'text': text, 'original_geometry': deepcopy(cell.get('original_geometry')),
                                 'observations': deepcopy(cell.get('observations', []))})
                columns.append({'column': c, 'header_path': path, 'header_cells': refs,
                                'status': 'no_header_selected' if not path else 'observed' if all(path) else 'needs_review'})
            counts = {}
            for column in columns:
                key = tuple(column['header_path'])
                counts[key] = counts.get(key, 0) + 1
            for column in columns:
                if column['header_path'] and counts[tuple(column['header_path'])] > 1:
                    column['status'] = 'ambiguous_duplicate_path'
            result = {'status': 'structure_observed', 'table_index': table_index,
                    'header_rows': header_rows, 'header_selection': 'explicit_user_selection',
                    'row_count': rows, 'column_count': cols, 'columns': columns,
                    'body_cells': deepcopy(cells[header_rows:]),
                    'unbound_region_indices': deepcopy(table.get('unbound_region_indices', [])),
                    'source_image_sha256': metadata.get('source_image_sha256'),
                    'cell_values_verified': False, 'header_meanings_verified': False,
                    'ready_for_sql_import': False,
                    'warnings': ['OCR文本和表头含义未核验；空白不是零，合并单元格不自动填充数据。']}
            from .table_arithmetic import TableArithmeticAgent
            result['arithmetic_check']=TableArithmeticAgent().run(result)
            return result
        except (KeyError, TypeError, IndexError):
            return rejected('malformed_grid')
