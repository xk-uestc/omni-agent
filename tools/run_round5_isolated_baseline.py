"""Run the unchanged e89113d OHR evaluator with excluded project credentials.

The external helper must have identical source after CRLF/LF normalization.
Only its configuration location differs; no credentials are copied to the
worktree and no answer, prompt, source, evaluator or score is patched.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path('D:/ICT8-Backups/round5-baseline-unseen-20261002')
DATA = BASELINE/'runtime/round5-assets'
COMMIT = 'e89113db5134e1f789217e363726940fd82e8f1b'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_sha(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def main():
    observed = subprocess.check_output(['git', '-C', str(BASELINE), 'rev-parse', 'HEAD'], text=True).strip()
    if observed != COMMIT:
        raise ValueError('baseline_commit_changed')
    baseline_helper = BASELINE/'tools/model_runtime.py'
    external_helper = ROOT/'tools/model_runtime.py'
    if code_sha(baseline_helper) != code_sha(external_helper):
        raise ValueError('credential_helper_code_must_match_baseline')
    spec = importlib.util.spec_from_file_location('excluded_current_project_runtime', external_helper)
    external_runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(external_runtime)
    sys.path.insert(0, str(BASELINE/'tools'))
    import model_runtime
    # Reuse the same validated configuration loader from its authorized root.
    # The evaluator and baseline source files remain byte-for-byte unchanged.
    model_runtime.enable_local_model = external_runtime.enable_local_model
    manifest = ROOT/'benchmarks/ohr_bench/MANIFEST_ROUND5_UNSEEN_DOCUMENTS.json'
    output = BASELINE/'docs/OHR_ROUND5_UNSEEN_BASELINE_20261002.json'
    if output.exists():
        raise FileExistsError('baseline_run_already_recorded_no_repeat')
    provenance = {'baseline_commit': observed, 'baseline_worktree': str(BASELINE),
        'isolated_data_root': str(DATA), 'manifest_sha256': sha(manifest),
        'runner_sha256': sha(Path(__file__)), 'credential_helper_code_sha256': sha(baseline_helper),
        'external_credential_helper_sha256': sha(external_helper),
        'credential_helper_normalized_code_sha256': code_sha(baseline_helper),
        'credential_helper_difference': 'Git checkout CRLF/LF only; normalized source identical',
        'credentials_copied_to_worktree': False, 'evaluator_or_scoring_modified': False,
        'embedding_asset_manifest_sha256': sha(BASELINE/'models/bge-small-zh-v1.5/ASSET_MANIFEST.json'),
        'embedding_assets': 'Exact validated original bundle copied; Git ignores weight file and changes text checkout endings',
        'model': 'gpt-6-luna', 'reasoning': 'medium', 'planned_cases': 36,
        'rendered_ocr_diagnostics': 'explicitly_skipped_for_both_paired_runs',
        'preparation_attempts_before_any_model_call': [
            {'status': 'source_guard_stopped', 'reason': 'Git CRLF/LF only', 'api_calls': 0},
            {'status': 'asset_guard_stopped', 'reason': 'junction outside isolated data root', 'api_calls': 0},
            {'status': 'embedding_guard_stopped', 'reason': 'ignored weights absent and model text assets CRLF', 'api_calls': 0}],
        'report': str(output)}
    record = ROOT/'docs/ROUND5_BASELINE_ISOLATION_VERIFIED_20261002.json'
    with record.open('x', encoding='utf-8') as stream:
        json.dump(provenance, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    sys.argv = ['evaluate_ohr_bench.py', '--manifest', str(manifest), '--data-root', str(DATA),
                '--with-model', '--skip-rendered-ocr', '--output', str(output)]
    runpy.run_path(str(BASELINE/'tools/evaluate_ohr_bench.py'), run_name='__main__')


if __name__ == '__main__':
    main()
