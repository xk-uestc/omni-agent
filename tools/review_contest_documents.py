"""Render the actual exported PDFs and record objective delivery constraints."""
import hashlib
import json
import os
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import fitz
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'delivery'
PREVIEW = Path(os.environ.get('ICT8_DOCUMENT_PREVIEW', str(ROOT/'runtime/document-preview')))


def main():
    PREVIEW.mkdir(parents=True, exist_ok=True)
    result = []
    limits = {'01':20,'02':30,'03':50,'04':5}
    for file in sorted(OUT.glob('*.pdf')):
        pdf = fitz.open(file)
        pages = []
        thumbs = []
        for i, page in enumerate(pdf):
            text = page.get_text()
            blocks = page.get_text('blocks')
            outside = [b[:4] for b in blocks if b[0] < -1 or b[1] < -1 or b[2] > page.rect.width+1 or b[3] > page.rect.height+1]
            pix = page.get_pixmap(matrix=fitz.Matrix(1.25,1.25),alpha=False)
            image = Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
            image.save(PREVIEW/f'{file.stem}-{i+1:02}.png')
            image.thumbnail((280,396))
            thumbs.append(image)
            pages.append({'page':i+1,'characters':len(text),'replacement_characters':text.count('\ufffd'),
                          'out_of_page_blocks':outside})
        sheet = Image.new('RGB',(300*4,428*((len(thumbs)+3)//4)),'#E5EAF0')
        draw=ImageDraw.Draw(sheet)
        for i,im in enumerate(thumbs):
            x,y=300*(i%4),428*(i//4)
            sheet.paste(im,(x+10,y+22))
            draw.text((x+10,y+4),str(i+1),fill='#142735')
        sheet.save(PREVIEW/f'{file.stem}-contact.png')
        limit=limits[file.name[:2]]
        result.append({'path':file.relative_to(ROOT).as_posix(),'pages':len(pdf),'page_limit':limit,
                       'within_limit':len(pdf)<=limit,'pages_nonempty':all(p['characters']>30 for p in pages),
                       'page_checks':pages,'sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
    pptx=OUT/'05-anonymous-defense.pptx'
    with zipfile.ZipFile(pptx) as z:
        slides=[n for n in z.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml')]
        combined=''.join(z.read(n).decode('utf8') for n in slides)
        config=json.loads((ROOT/'runtime/model_config.json').read_text(encoding='utf8'))
        if config['api_key'] in combined:
            raise ValueError('Presentation contains a local credential')
        core=ET.fromstring(z.read('docProps/core.xml'))
        author=core.find('{http://purl.org/dc/elements/1.1/}creator')
        ppt_info={'slides':len(slides),'within_limit':len(slides)<=20,
                  'metadata_author':author.text if author is not None else '',
                  'local_credential_in_slide_xml':False}
    report={'pdfs':result,'presentation':ppt_info,
            'renderer':{'canonical_docx_renderer':'unavailable','reason':'bundled runtime has no LibreOffice soffice.exe',
                        'actual_pdf_export':'WPS kwps.Application','page_images':'PyMuPDF rasterization of actual WPS PDFs'},
            'ok': all(r['within_limit'] and r['pages_nonempty'] and all(not p['out_of_page_blocks'] and not p['replacement_characters'] for p in r['page_checks']) for r in result) and ppt_info['within_limit'],
            'claim':'PDF geometry and page counts, not semantic correctness or native PowerPoint behavior'}
    (OUT/'MATERIAL_VALIDATION.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'pdfs':[{k:r[k] for k in ('path','pages','within_limit')} for r in result], 'presentation':ppt_info},ensure_ascii=False))
    return 0 if report['ok'] else 1


if __name__=='__main__':
    raise SystemExit(main())
