from pathlib import Path


def test_powershell_wrapper_delegates_to_single_bash_source_and_fails_closed():
    script = (Path(__file__).parents[1] / "eval" / "run_all.ps1").read_text(encoding="utf-8")
    assert 'Join-Path $PSScriptRoot "run_all.sh"' in script
    assert "未找到 bash" in script
    assert "不会静默跳过评测" in script
    assert "$env:MSYSTEM" in script
    assert "$env:WSL_DISTRO_NAME" in script
    for variable in ("BASE_REF", "BASE_DIR", "BENCH", "OCR", "STRICT", "GEN_SEED"):
        assert f'$env:{variable}' in script
