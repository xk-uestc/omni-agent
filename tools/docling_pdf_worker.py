"""Isolated, local-only Docling worker. Parent enforces a hard process timeout."""
import argparse
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--pages',type=int,required=True)
    p.add_argument('--timeout',type=int,required=True)
    p.add_argument('--skip-formula',action='store_true')
    args=p.parse_args()
    import torch
    torch.set_num_threads(4)
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions,RapidOcrOptions
    from docling.datamodel.accelerator_options import AcceleratorOptions,AcceleratorDevice
    from docling.document_converter import DocumentConverter,PdfFormatOption
    options=PdfPipelineOptions(artifacts_path=Path('/tmp/omni-docling-models'),document_timeout=args.timeout,
        do_ocr=True,do_table_structure=True,do_formula_enrichment=not args.skip_formula,enable_remote_services=False,
        accelerator_options=AcceleratorOptions(num_threads=4,device=AcceleratorDevice.CPU),ocr_options=RapidOcrOptions())
    converter=DocumentConverter(format_options={InputFormat.PDF:PdfFormatOption(pipeline_options=options)})
    result=converter.convert(args.source,page_range=(1,args.pages),raises_on_error=False)
    result.document.save_as_json(args.output/'docling.json')
    (args.output/'worker-status.json').write_text(json.dumps({'conversion_status':result.status.value,
       'errors':[str(e) for e in result.errors],'formula_enrichment':not args.skip_formula}))
    if result.status.value!='success':raise SystemExit(1)


if __name__=='__main__':main()
