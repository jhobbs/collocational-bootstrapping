import sys
from pathlib import Path
from collections import Counter
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from link_original_transcripts import signature, choose_link


def test_short_formulaic_utterances_do_not_supply_evidence():
    assert signature("yes that's right") is None


def test_contraction_spacing_matches_without_changing_words():
    assert signature("I don't think that we can find it.") == signature("I do n't think that we can find it")


def test_single_or_competing_matches_are_not_accepted():
    assert choose_link(Counter({"a": 4}))[0] is None
    assert choose_link(Counter({"a": 10, "b": 9}))[0] is None
    assert choose_link(Counter({"a": 10, "b": 1}))[0] == "a"
