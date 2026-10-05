"""Explicit per-page scan-table preview without replacing native PDF text."""
import hashlib
from .scanned_table_structure import ScannedTableStructureAgent
from .chunk_cleaning import DocumentChunker
from .image_geometry import bind_pdf_coordinates


class PdfTablePreviewAgent:
    def run(self,raw,selections,*,ocr_pipeline,chunks=(),language='eng'):
        import fitz
        sha=hashlib.sha256(raw).hexdigest()
        with fitz.open(stream=raw,filetype='pdf') as document:
            selected=self.validate(selections,len(document))
            cache={}
            for chunk in chunks:
                meta=chunk.metadata.get('ocr_metadata',{})
                binding=meta.get('pdf_coordinate_mapping',{})
                if (chunk.metadata.get('ocr_status')=='ok' and binding.get('status')=='mapped'
                        and binding.get('source_pdf_sha256')==sha and binding.get('page_no')==chunk.page_no):
                    cache.setdefault(chunk.page_no,(meta,'reused_page_ocr'))
            results=[]
            for selection in selected:
                page_no=selection['page_no']
                if page_no not in cache:
                    if ocr_pipeline is None:
                        cache[page_no]=({'preview_failure':'ocr_not_configured'},'not_run')
                    else:
                        page=document[page_no-1]
                        scale=min(2.,2400/max(page.rect.width,page.rect.height,1))
                        pixmap=page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False)
                        if pixmap.width*pixmap.height>6_000_000:
                            cache[page_no]=({'preview_failure':'render_pixel_limit'},'not_run')
                        else:
                            png=pixmap.tobytes('png')
                            try:
                                response=ocr_pipeline.run(png,language=language)
                                payload=response.to_dict() if hasattr(response,'to_dict') else dict(response)
                                if payload.get('status')!='ok':
                                    cache[page_no]=({'preview_failure':'page_ocr_not_accepted'},'explicit_page_ocr')
                                else:
                                    geometry=DocumentChunker._pdf_page_geometry(page,render_scale=scale,
                                        render_size=(pixmap.width,pixmap.height))
                                    meta=bind_pdf_coordinates(payload.get('metadata',{}),geometry,
                                        hashlib.sha256(png).hexdigest(),[pixmap.width,pixmap.height],sha,page_no)
                                    cache[page_no]=(meta,'explicit_page_ocr')
                            except (ValueError,RuntimeError):
                                cache[page_no]=({'preview_failure':'page_ocr_failed'},'explicit_page_ocr')
                meta,origin=cache[page_no]
                structure=({'status':'unavailable','reason':meta['preview_failure'],'cell_values_verified':False}
                    if meta.get('preview_failure') else ScannedTableStructureAgent().run(meta,
                        table_index=selection['table_index'],header_rows=selection['header_rows']))
                if structure['status']=='structure_observed' and not self.coordinates_match(structure,sha,page_no):
                    structure={'status':'unavailable','reason':'pdf_cell_coordinates_unavailable','cell_values_verified':False}
                if structure['status']=='structure_observed':
                    import re
                    from .pdf_amount_verification import amount
                    if any(amount(cell.get('text')) is None and re.search(r'[$€£¥￥]|USD|EUR|GBP|CNY|RMB',cell.get('text',''),re.I)
                        for row in structure['body_cells'] for cell in row if cell.get('status')!='covered_by_merged_cell'):
                        from .amount_cell_review import AmountCellReviewAgent
                        page=document[page_no-1];scale=min(2.,2400/max(page.rect.width,page.rect.height,1))
                        rendered=page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False).tobytes('png')
                        structure['amount_cell_review']=AmountCellReviewAgent().run(rendered,structure,
                            getattr(getattr(ocr_pipeline,'executor',None),'recognize_line',None))
                results.append({**structure,**selection,'source_pdf_sha256':sha,'ocr_origin':origin,
                                'native_text_replaced':False})
            from .pdf_amount_verification import PdfAmountVerificationAgent
            return PdfAmountVerificationAgent().run(raw,results)

    @staticmethod
    def validate(selections,page_count):
        if not isinstance(selections,list) or not 1<=len(selections)<=8:
            raise ValueError('请指定1到8项PDF表格选择')
        seen=set();pages=set();result=[]
        for item in selections:
            if not isinstance(item,dict) or set(item)!={'page_no','table_index','header_rows'}:
                raise ValueError('PDF表格选择必须明确页码、表格序号及表头行数')
            p,t,h=(item[key] for key in ('page_no','table_index','header_rows'))
            if (any(type(value) is not int for value in (p,t,h)) or not 1<=p<=page_count
                    or not 0<=t<32 or not 0<=h<=5):
                raise ValueError('PDF表格页码或表头选择越界')
            if (p,t) in seen:
                raise ValueError('同一PDF表格不能重复指定表头')
            seen.add((p,t));pages.add(p);result.append(dict(item))
        if len(pages)>4:
            raise ValueError('一次最多预览4个指定PDF页面')
        return result

    @staticmethod
    def coordinates_match(structure,sha,page_no):
        cells=[cell for row in structure['body_cells'] for cell in row
               if cell.get('status')!='covered_by_merged_cell']
        cells.extend(cell for column in structure['columns'] for cell in column['header_cells'])
        return bool(cells) and all(
            (cell.get('original_geometry') or {}).get('pdf_geometry',{}).get('pdf_highlight_eligible') is True
            and cell['original_geometry']['pdf_geometry'].get('source_pdf_sha256')==sha
            and cell['original_geometry']['pdf_geometry'].get('page_no')==page_no for cell in cells)
