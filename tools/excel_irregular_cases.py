"""Known-answer XLSX fixtures; synthetic developer tests, not public benchmarks."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
import json
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.worksheet.table import Table


@dataclass
class ExcelCase:
    name: str
    description: str
    raw: bytes
    records: list[dict] = field(default_factory=list)
    required_text: list[str] = field(default_factory=list)
    forbidden_text: list[str] = field(default_factory=list)


def build_cases() -> list[ExcelCase]:
    cases: list[ExcelCase] = []

    def fresh(name):
        wb = Workbook(); wb.active.title = name
        return wb, wb.active

    def records(headers, rows, *, start=2, sheet='数据'):
        return [{'sheet': sheet, 'row': start+i, 'headers': headers, 'values': row}
                for i, row in enumerate(rows)]

    def save(name, description, wb, expected, required=(), forbidden=(), cache=None):
        stream = BytesIO(); wb.save(stream); raw = stream.getvalue(); wb.close()
        if cache:
            result = BytesIO()
            with ZipFile(BytesIO(raw)) as source, ZipFile(result, 'w', ZIP_DEFLATED) as target:
                for info in source.infolist():
                    content = source.read(info.filename)
                    if info.filename == 'xl/worksheets/sheet1.xml':
                        root = ET.fromstring(content)
                        ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
                        for cell in root.iter(ns+'c'):
                            if cell.attrib.get('r') in cache:
                                value = cell.find(ns+'v')
                                if value is None: value = ET.SubElement(cell, ns+'v')
                                value.text = str(cache[cell.attrib['r']])
                        content = ET.tostring(root, encoding='utf-8')
                    target.writestr(info, content)
            raw = result.getvalue()
        cases.append(ExcelCase(name, description, raw, expected, list(required), list(forbidden)))

    h=['地区','销售额']; data=[['华东',120],['华南',80]]
    wb,ws=fresh('数据'); ws.append(h)
    for row in data:ws.append(row)
    save('01_standard','标准数据与首末行',wb,records(h,data))

    wb,ws=fresh('数据'); ws.merge_cells('A1:B1'); ws['A1']='2025年度经营报表'
    ws.append(h)
    for row in data:ws.append(row)
    ws.merge_cells('A5:B5'); ws['A5']='备注：金额单位为万元'
    save('02_title_footer','合并标题与脚注不混入记录',wb,records(h,data,start=3),['2025年度经营报表','备注：金额单位为万元'])

    wb,ws=fresh('数据'); ws.merge_cells('A1:A2');ws['A1']='地区';ws.merge_cells('B1:C1');ws['B1']='2025年'
    ws['B2']='销售额';ws['C2']='订单数';ws.append(['华东',120,3]);ws.append(['华南',80,2])
    save('03_merged_header','两层横纵合并表头',wb,records(['地区','2025年 / 销售额','2025年 / 订单数'],[['华东',120,3],['华南',80,2]],start=3))

    wb,ws=fresh('数据');ws.append(['地区','产品','销售额']);ws.append(['华东','设备甲',120]);ws.append([None,'设备乙',80]);ws.merge_cells('A2:A3')
    save('04_merged_dimension','合并数据维度保留锚点，不伪造原值',wb,records(['地区','产品','销售额'],[['华东','设备甲',120],[None,'设备乙',80]]),['华东'])

    wb,ws=fresh('数据');ws.append(h);ws.append(data[0]);ws.append([]);ws.append(['产品','数量']);ws.append(['设备乙',7])
    save('05_stacked_tables','纵向两个不同业务表',wb,records(h,[data[0]])+records(['产品','数量'],[['设备乙',7]],start=5))

    wb,ws=fresh('数据');ws.append(['地区','销售额',None,'产品','数量']);ws.append(['华东',120,None,'设备乙',7]);ws.append(['华南',80,None,'设备丙',9])
    save('06_side_by_side','并排表格不串列',wb,records(h,data)+records(['产品','数量'],[['设备乙',7],['设备丙',9]]))

    wb,ws=fresh('数据');ws['D6']='地区';ws['F6']='销售额';ws['D7']='华东';ws['F7']=120;ws['D9']='华南';ws['F9']=80
    save('07_offsets_gaps','偏移表格、内部空列与空行',wb,[{'sheet':'数据','row':r,'headers':['地区','column_E','销售额'],'values':v} for r,v in [(7,['华东',None,120]),(9,['华南',None,80])]])

    wb,ws=fresh('数据');ws.append(h);ws.append(data[0]);ws.append(h);ws.append(data[1])
    save('08_repeated_header','分页重复表头仅移除有证据的重复',wb,records(h,[data[0]])+records(h,[data[1]],start=4))

    wb,ws=fresh('数据');ws.append([101,202]);ws.append([303,404]);ws.append([101,202])
    save('09_headerless_numeric','无表头纯数值，重复数据不丢',wb,records(['column_A','column_B'],[[101,202],[303,404],[101,202]],start=1))

    wb,ws=fresh('数据');ws.append(['华东旗舰','设备甲']);ws.append(['华南直营','设备乙'])
    save('10_headerless_text','全字符串无表头，首行不可丢失',wb,records(['column_A','column_B'],[['华东旗舰','设备甲'],['华南直营','设备乙']],start=1))

    wb,ws=fresh('数据');ws.append(['地区','金额','金额',None]);ws.append(['华东',12,13,'已核对'])
    save('11_duplicate_headers','重复字段和空表头使用唯一名称',wb,records(['地区','金额','金额_2','column_D'],[['华东',12,13,'已核对']]))

    wb,ws=fresh('数据');hh=['编号','日期','增长率','金额','启用'];ws.append(hh);ws.append(['000123',datetime(2025,10,5),.125,1234.5,True])
    ws['C2'].number_format='0.00%';ws['D2'].number_format='¥#,##0.00'
    save('12_typed_formats','前导零编号、日期、百分数、货币与布尔',wb,records(hh,[['000123','2025-10-05T00:00:00',.125,1234.5,True]]),['12.5%','000123'])

    wb,ws=fresh('数据');ws.append(['产品','数量','单价','金额']);ws.append(['设备甲',3,20,'=B2*C2'])
    save('13_formula_no_cache','未计算公式不捏造结果',wb,records(['产品','数量','单价','金额'],[['设备甲',3,20,'=B2*C2']]))
    wb,ws=fresh('数据');ws.append(['产品','数量','金额']);ws.append(['设备甲',3,'=B2*20'])
    save('14_formula_stale_cache','故意错误缓存999仍不可作为验证值',wb,records(['产品','数量','金额'],[['设备甲',3,'=B2*20']]),cache={'C2':999})

    wb,ws=fresh('数据');ws.append(['地区','金额','内部备注']);ws.append(['华东',12,'不可公开内部内容']);ws.append(['隐藏行',999,'秘密']);ws.row_dimensions[3].hidden=True;ws.column_dimensions['C'].hidden=True
    secret=wb.create_sheet('隐藏');secret.append(['隐藏表机密',123]);secret.sheet_state='veryHidden'
    save('15_hidden','隐藏行、列、工作表不进入可检索内容',wb,records(h,[['华东',12]]),forbidden=['隐藏行','秘密','内部内容','隐藏表机密'])
    # This sheet's visible header is 金额, not 销售额.
    cases[-1].records[0]['headers']=['地区','金额']

    wb,ws=fresh('数据');ws.append(h);ws.append(data[0]);ws.append(['小计',120]);ws.append(data[1]);ws.append(['合计',200])
    save('16_subtotals','小计和总计保留但与明细区分',wb,records(h,[data[0],['小计',120],data[1],['合计',200]]))

    wb,ws=fresh('数据');ws.append(['地区','金额','数量']);ws.append(['华东',None,0]);ws.append(['华南','#DIV/0!',False]);ws['B3'].data_type='e'
    save('17_null_errors','空值不变零，Excel错误不冒充数值',wb,records(['地区','金额','数量'],[['华东',None,0],['华南','#DIV/0!',False]]))

    wb,ws=fresh('数据');ws.append(h);ws.append(data[0]);ws['XFD1048576']='远端注释';ws['G100000'].font=Font(bold=True)
    save('18_sparse_far_cell','最远单元格与格式膨胀不扫描万亿空格',wb,records(h,[data[0]]),['远端注释'])

    wb,ws=fresh('数据');ws['B3']='甲';ws['C3']='乙';ws['B4']='a';ws['C4']='b';ws.add_table(Table(displayName='ExplicitRange',ref='B3:C4'))
    save('19_explicit_table','Excel正式Table范围确认未知文本字段',wb,records(['甲','乙'],[['a','b']],start=4))

    wb,ws=fresh('数据');ws.merge_cells('A1:A3');ws['A1']='地区';ws.merge_cells('B1:E1');ws['B1']='经营情况';ws.merge_cells('B2:C2');ws['B2']='2024年';ws.merge_cells('D2:E2');ws['D2']='2025年'
    for cell,value in [('B3','销售额'),('C3','订单数'),('D3','销售额'),('E3','订单数')]:ws[cell]=value
    ws.append(['华东',90,2,120,3])
    save('20_three_header_levels','三层嵌套合并表头展开完整路径',wb,records(['地区','经营情况 / 2024年 / 销售额','经营情况 / 2024年 / 订单数','经营情况 / 2025年 / 销售额','经营情况 / 2025年 / 订单数'],[['华东',90,2,120,3]],start=4))

    wb,ws=fresh('数据');ws.append(['字段','值']);ws.append(['地区','华东']);ws.append(['销售额',120]);ws.append(['订单数',3])
    save('21_key_value','纵向键值表不错误转置',wb,records(['字段','值'],[['地区','华东'],['销售额',120],['订单数',3]]))

    wb,ws=fresh('数据');ws.append(h);ws.append(data[0]);other=wb.create_sheet('第二表');other.append(['产品','数量']);other.append(['设备乙',7]);wb.create_sheet('空表')
    save('22_multiple_sheets','多工作表与空工作表',wb,records(h,[data[0]])+records(['产品','数量'],[['设备乙',7]],sheet='第二表'))

    wb,ws=fresh('数据');ws.append(h);ws.append(data[0]);ws.append(['产品','数量']);ws.append(['设备乙',7])
    save('23_adjacent_sections','没有空行的不同表头分段',wb,records(h,[data[0]])+records(['产品','数量'],[['设备乙',7]],start=4))

    wb,ws=fresh('数据');ws.append(['地区','数量','销售额']);ws.append([None,'台','万元']);ws.append(['华东',3,120])
    save('24_separate_unit_row','独立单位行不混进业务记录',wb,records(['地区','数量','销售额'],[['华东',3,120]],start=3),['万元'])

    wb,ws=fresh('预算! 空格');ws.append(['编号','金额']);ws.append([123,120]);ws['A2'].number_format='000000'
    from openpyxl.comments import Comment
    ws['B2'].comment=Comment('原表金额，不包括税费','测试作者');ws['A2'].hyperlink='https://example.org/reference'
    save('25_identifiers_annotations','特殊表名、数字编号格式、批注和链接',wb,records(['编号','金额'],[[123,120]],sheet='预算! 空格'),['000123'])

    wb,ws=fresh('数据');ws.append(['地区','金额']);ws.append(['华东',0]);ws.append(['华南',-12.5]);ws.append(['华北',1234567890.125])
    save('26_numeric_precision','零、负数和大数精度不改变',wb,records(['地区','金额'],[['华东',0],['华南',-12.5],['华北',1234567890.125]]))
    return cases


def evaluate_case(case: ExcelCase, payload: dict) -> dict:
    import unicodedata
    checks=[]; chunks=payload['chunks']
    for expected in case.records:
        def matches(chunk):
            meta=chunk['metadata']
            headers=meta.get('headers',[])
            # Baseline generic column labels used ordinal numbers.
            if headers and all(str(h).startswith('column_') for h in headers):
                header_ok=all(h.startswith('column_') for h in expected['headers']) and len(headers)==len(expected['headers'])
            else:header_ok=headers==expected['headers']
            values=[value.get('raw_value') if isinstance(value,dict) else value for value in meta.get('values',[])]
            return (chunk.get('sheet_name')==expected['sheet'] and chunk.get('row_start')==expected['row']
                    and header_ok and values==expected['values'])
        checks.append({'check':f"row:{expected['sheet']}:{expected['row']}:{'|'.join(expected['headers'])}", 'passed':any(matches(chunk) for chunk in chunks)})
    text='\n'.join(chunk['text'] for chunk in chunks)
    for item in case.required_text:checks.append({'check':'text:'+item,'passed':unicodedata.normalize('NFKC',item) in text})
    for item in case.forbidden_text:checks.append({'check':'hidden_absent:'+item,'passed':item not in text})
    unexpected=[]
    for chunk in chunks:
        if chunk['content_type']!='row':continue
        meta=chunk['metadata'];headers=meta.get('headers',[])
        if not any(chunk.get('sheet_name')==record['sheet'] and chunk.get('row_start')==record['row'] and
                   (headers==record['headers'] or headers and all(str(h).startswith('column_') for h in headers) and
                    all(h.startswith('column_') for h in record['headers']) and len(headers)==len(record['headers'])) for record in case.records):
            unexpected.append((chunk.get('sheet_name'),chunk.get('row_start')))
    checks.append({'check':'no_extra_title_note_unit_or_header_records','passed':not unexpected,'unexpected_rows':sorted(set(unexpected))})
    return {'name':case.name,'description':case.description,'passed':all(c['passed'] for c in checks),
            'checks':checks,'warnings':payload.get('warnings',[]),'stats':payload['stats']}


def write_samples(output: Path, cases: list[ExcelCase]):
    output.mkdir(parents=True,exist_ok=True)
    manifest=[]
    import hashlib
    for case in cases:
        path=output/(case.name+'.xlsx');path.write_bytes(case.raw)
        manifest.append({'name':case.name,'description':case.description,'file':path.name,
                         'sha256':hashlib.sha256(case.raw).hexdigest(),'records':case.records,
                         'required_text':case.required_text,'forbidden_text':case.forbidden_text})
    (output/'expected.json').write_text(json.dumps({'synthetic_developer_fixtures':True,'cases':manifest},ensure_ascii=False,indent=2),encoding='utf-8')
