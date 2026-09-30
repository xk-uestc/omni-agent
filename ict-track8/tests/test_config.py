from __future__ import annotations

from backend.config import env_float, env_int


def test_invalid_numeric_environment_values_fall_back(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setenv("ICT8_TEST_FLOAT", "not-a-number")
    monkeypatch.setenv("ICT8_TEST_INT", "99")

    assert env_float("ICT8_TEST_FLOAT", 8.0, minimum=0.1, maximum=60.0, warnings=warnings) == 8.0
    assert env_int("ICT8_TEST_INT", 1, minimum=0, maximum=3, warnings=warnings) == 1
    assert len(warnings) == 2
    assert all("not-a-number" not in item for item in warnings)


def test_finite_values_are_clamped_by_contract(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setenv("ICT8_TEST_FLOAT", "nan")
    monkeypatch.setenv("ICT8_TEST_INT", "-1")

    assert env_float("ICT8_TEST_FLOAT", 12.0, minimum=0.1, maximum=60.0, warnings=warnings) == 12.0
    assert env_int("ICT8_TEST_INT", 1, minimum=0, maximum=3, warnings=warnings) == 1
    assert len(warnings) == 2
