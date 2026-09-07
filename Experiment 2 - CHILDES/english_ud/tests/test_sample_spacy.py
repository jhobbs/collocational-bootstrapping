from types import SimpleNamespace
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sample_spacy import surface_groups


def test_clitics_share_source_word_and_repeated_words_remain_distinct():
    tokens = [SimpleNamespace(word=w, gra=SimpleNamespace(dep=i))
              for i, w in enumerate(["it's", "", "it", "."], 1)]
    text, groups = surface_groups(tokens)
    assert text == "it's it ."
    assert groups[1] == groups[2] == (1, "it's")
    assert groups[3] == (2, "it")
