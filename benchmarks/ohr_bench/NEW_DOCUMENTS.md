# Unseen-document and unseen-query official subset

`MANIFEST_NEW_DOCUMENTS.json` freezes 7 new original OHR-Bench PDFs and 18 new
official questions. All prior manifest documents and every saved OHR report's
documents, IDs and exact normalized questions are excluded. Seven domains are
represented; each of six evidence categories has three questions.

Selection used only domain/category, original PDF size, stable document name
and question ID, plus exact question deduplication. No answers, evidence text,
PDF parser results, model outputs or answerability gates were used. QA selection
was frozen before PDF/GT reading. The originals total about 1.69 MB and 75 pages.

Use the unchanged existing evaluator with:

```powershell
python tools/evaluate_ohr_bench.py --manifest benchmarks/ohr_bench/MANIFEST_NEW_DOCUMENTS.json --help
```

Use separate new output and store paths for each frozen program version,
the same manifest and options, and retain every failure. `--help` displays the
existing evaluator's current arguments without calling the API.

Dataset revision/URLs, per-PDF SHA/ZIP CRC, official GT SHA, QA row hashes and
all exclusion-file hashes are retained in the manifest. Data remains in
`D:/ICT8-OfficialDatasets/ohr-bench`. Dataset card says CC BY 4.0; upstream PDFs
retain source copyright and official README limits their use to research.

This is an unseen-document subset for this project run, not guaranteed unseen
in model pretraining, a representative accuracy estimate or the full official
benchmark. Resource-biased selection and seven-document retrieval scope must
remain explicit. Do not share the new QA/gold with implementation agents while
the evaluation is intended to remain held out.
