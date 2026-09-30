from pathlib import Path


def test_run_all_enables_fail_closed_benchmark_gate_for_both_revisions():
    script = (Path(__file__).parents[1] / "eval" / "run_all.sh").read_text(encoding="utf-8")
    benchmark_block = script.split('if [[ "${BENCH:-0}" == "1" ]]; then', 1)[1].split("\nfi", 1)[0]
    commands = [line for line in benchmark_block.splitlines() if "bench_scale.py" in line]
    assert len(commands) == 2
    assert all("--fail-on-error" in command for command in commands)
