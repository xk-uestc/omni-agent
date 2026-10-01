"""Literal wrapped sources remain verifiable without authorizing wrong relations."""
import pytest

from backend.grounded_generation import GroundedGenerator, INSTRUCTIONS
from backend.responses_client import GenerationError


SOURCE = 'Also appearing were Dana Reed and Elliot Stone\non behalf of Defendant Meridian Ltd.'


def result(text, quote):
    return {'abstain': False, 'claims': [{'text': text, 'support': [{'citation_id': 7, 'quote': quote}]}]}


def test_wrapped_english_relation_accepts_literal_quote_and_original_language():
    assert GroundedGenerator.validate(result(SOURCE, SOURCE), {7: SOURCE})


def test_display_whitespace_can_change_without_mutating_literal_support():
    assert GroundedGenerator.validate(result(SOURCE.replace('\n', ' '), SOURCE), {7: SOURCE})


def test_quote_whitespace_is_not_silently_rewritten():
    with pytest.raises(GenerationError, match='原文不能核验'):
        GroundedGenerator.validate(result(SOURCE, SOURCE.replace('\n', ' ')), {7: SOURCE})


def test_wrong_subject_cannot_borrow_same_literal_source():
    with pytest.raises(GenerationError):
        GroundedGenerator.validate(result(SOURCE.replace('Meridian', 'Harbour'), SOURCE), {7: SOURCE})


def test_instructions_allow_original_language_and_preserve_literal_linebreaks():
    assert '事实结论使用证据原文语言' in INSTRUCTIONS
    assert '不把换行替换成空格' in INSTRUCTIONS
