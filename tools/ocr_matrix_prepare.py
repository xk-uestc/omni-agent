"""Fixed public inputs and explicitly synthetic stress cases, never official scores."""
from pathlib import Path
import json,hashlib
import cv2
import numpy as np
from PIL import Image,ImageDraw,ImageFont,ImageFilter
from io import BytesIO
from score_ocr_candidates import GOLD
from score_ocr_repeated import CHECKS

BASE=Path(r'D:\ICT8-OfficialDatasets\ocr-candidates')

def main():
    root=BASE/'matrix-samples';root.mkdir(exist_ok=True)
    manifest=root/'manifest.json'
    if manifest.exists():print(manifest);return
    cases=[]
    def add(name,image,fields,scenario,provenance,reference=None,rows=None):
        path=root/(name+'.png');image.save(path)
        cases.append(dict(name=name,path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            width=image.width,height=image.height,fields=list(dict.fromkeys(fields)),scenario=scenario,
            provenance=provenance,reference_text=reference,expected_rows=rows or []))
    for name in ['budget-5','budget-90','official-chinese','public-second']:
        with Image.open(BASE/'samples'/(name+'.png')) as im:
            add(name,im.convert('RGB'),CHECKS[name+'.png'],
                {'budget-5':'5_degree_skew','budget-90':'90_degree_rotation','official-chinese':'dirty_bilingual_boarding_pass','public-second':'dense_two_column_finance'}[name],
                'public_source_from_OCR_REPEATED_COMPARISON_20261005')
    with Image.open(BASE/'samples/budget-0.png') as im:
        im=im.convert('RGB');w,h=im.size
        add('budget-lowres',im.resize((int(w*.5),int(h*.5))).filter(ImageFilter.GaussianBlur(.4)),
            [v for pair in GOLD for v in pair],'low_resolution_scan','controlled_degradation_of_public_budget')
        src=np.float32([[0,0],[w-1,0],[w-1,h-1],[0,h-1]])
        dst=np.float32([[w*.08,h*.03],[w*.94,h*.1],[w*.99,h*.94],[w*.01,h*.99]])
        matrix=cv2.getPerspectiveTransform(src,dst)
        distorted=cv2.warpPerspective(np.array(im),matrix,(w,h),borderValue=(255,255,255))
        add('budget-perspective',Image.fromarray(distorted),[v for pair in GOLD for v in pair],
            'perspective_distortion','controlled_homography_of_public_budget')
    camera=Path(r'D:\ICT8-OfficialDatasets\document-camera-examples\25.jpg')
    with Image.open(camera) as im:
        add('camera-magazine',im.convert('RGB'),['Letters','The Economist','Disruption and competition','Debt and private equity',
            'The coronavirus epidemic','Love will tear us apart'],'real_camera_curved_multicolumn',
            'https://github.com/fh2019ustc/DocTr-Plus commit 2b12e48d5ba24580eaa7e17e12db4057c8ac6058 distorted/25.jpg')
    font=ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc',32)
    image=Image.new('RGB',(1200,950),'white');draw=ImageDraw.Draw(image)
    title='2026年研发费用明细';head='项目名称 金额（元）';footer='统计口径：仅包含已支付费用。'
    draw.text((70,45),title,font=font,fill='black');draw.text((70,125),'项目名称',font=font,fill='black')
    draw.text((780,125),'金额（元）',font=font,fill='black')
    pairs=[('人员工资','47,000.00'),('办公用品','2,000.00'),('设备采购','35,000.00'),('技术培训','8,000.00'),
        ('差旅费用','1,000.00'),('材料费用','6,500.00'),('税费调整','-500.00'),('合计','99,000.00')]
    expected=[]
    for i,(label,amount) in enumerate(pairs):
        y=200+i*72
        draw.line((55,y-10,1120,y-10),fill='#999999',width=1)
        draw.text((70,y),label,font=font,fill='black');draw.text((780,y),amount,font=font,fill='black')
        expected.append(dict(label=label,amount=amount,bbox=[55,y-5,1120,y+52]))
    draw.text((70,830),footer,font=font,fill='black')
    reference='\n'.join([title,head]+[' '.join(p) for p in pairs]+[footer])
    fields=[title,'项目名称','金额（元）']+[s for p in pairs for s in p]+[footer]
    add('chinese-table-clean',image,fields,'clean_chinese_financial_table','synthetic_known_text_not_public_benchmark',reference,expected)
    degraded=image.resize((840,665)).filter(ImageFilter.GaussianBlur(.7))
    buffer=BytesIO();degraded.save(buffer,format='JPEG',quality=45);buffer.seek(0)
    degraded=Image.open(buffer).convert('RGB')
    rows=[dict(row,bbox=[v*.7 for v in row['bbox']]) for row in expected]
    add('chinese-table-degraded',degraded,fields,'blur_downsample_jpeg_chinese_table',
        'synthetic_known_text_controlled_degradation',reference,rows)
    manifest.write_text(json.dumps(dict(not_official_benchmark=True,cases=cases),ensure_ascii=False,indent=2),encoding='utf8')
    print(manifest)

if __name__=='__main__':main()
