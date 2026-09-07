import csv
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from parse_matched_spacy import extract_doc_pairs, iter_included_utterances, parse_matched


def write_csv(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    with path.open() as stream:
        return list(csv.DictReader(stream))


def token(text, lemma, pos, dep, index):
    return SimpleNamespace(text=text, lemma_=lemma, pos_=pos, dep_=dep, i=index, head=None)


def test_exact_spacy_rule_and_native_indices():
    subject = token("Dogs", "DOG", "NOUN", "nsubj", 0)
    verb = token("run", "RUN", "VERB", "ROOT", 1)
    ignored = token("cats", "cat", "NOUN", "nsubjpass", 2)
    subject.head = verb
    ignored.head = verb
    assert list(extract_doc_pairs([subject, verb, ignored])) == [{
        "subject_surface": "Dogs", "subject_lemma": "dog", "subject_pos": "NOUN",
        "verb_surface": "run", "verb_lemma": "run", "verb_pos": "VERB",
        "dependency": "nsubj", "subject_index": 0, "verb_index": 1,
        "index_space": "spacy_token",
    }]


def test_input_generator_is_lazy_and_selects_true_rows(tmp_path):
    source = tmp_path / "utterances.csv"
    write_csv(source, [
        {"included_in_cds": "True", "text": "one", "marker": "a"},
        {"included_in_cds": "False", "text": "skip", "marker": "b"},
        {"included_in_cds": "TRUE", "text": "two", "marker": "c"},
    ])
    rows = iter_included_utterances(source, "text")
    first = next(rows)
    assert first[0] == "one" and first[1]["marker"] == "a"
    assert [text for text, _ in rows] == ["two"]


class FakeNLP:
    meta = {"name": "fake", "version": "1.2.3"}

    def __init__(self):
        self.pipe_args = None

    def pipe(self, records, **kwargs):
        self.pipe_args = kwargs
        for text, row in records:
            if text == "pair":
                subj = token("I", "I", "PRON", "nsubj", 0)
                verb = token("go", "GO", "AUX", "ROOT", 1)
                subj.head = verb
                yield [subj, verb], row
            else:
                yield [], row


class CapturingNLP(FakeNLP):
    def __init__(self):
        super().__init__()
        self.texts = []

    def pipe(self, records, **kwargs):
        self.pipe_args = kwargs
        for text, row in records:
            self.texts.append(text)
            yield [], row


def test_parse_streams_all_included_rows_and_preserves_zero_pair_denominator(tmp_path):
    utterances = tmp_path / "utterances.csv"
    metadata = tmp_path / "input-metadata.json"
    rows = [
        {"included_in_cds": "True", "collection": "Eng-NA", "corpus": "C",
         "transcript": "a.cha", "utterance_id": "1", "target_child_age_months": "18",
         "text": "pair", "historical_note": "kept", "n_pairs": "7"},
        {"included_in_cds": "True", "collection": "Eng-NA", "corpus": "C",
         "transcript": "a.cha", "utterance_id": "2", "target_child_age_months": "18",
         "text": "zero", "historical_note": "also kept", "n_pairs": "0"},
        {"included_in_cds": "False", "collection": "Eng-NA", "corpus": "C",
         "transcript": "a.cha", "utterance_id": "3", "target_child_age_months": "18",
         "text": "pair", "historical_note": "excluded", "n_pairs": "1"},
    ]
    write_csv(utterances, rows)
    metadata.write_text(json.dumps({"scope": "thesis_eng_na", "collection": "Eng-NA",
                                    "counts": {"n_cds_utterances": 2},
                                    "files": [{"large": "omitted"}], "sources": [{"large": "omitted"}]}))
    nlp = FakeNLP()
    output = tmp_path / "out"
    parse_matched(utterances, metadata, output, "text", 3, "fake_model", nlp=nlp)

    pairs = read_csv(output / "english_ud_subject_verb_pairs.csv")
    assert len(pairs) == 1
    assert pairs[0]["historical_note"] == "kept"
    assert pairs[0]["source_ud_n_pairs"] == "7"
    assert "n_pairs" not in pairs[0]
    assert pairs[0]["subject_index"] == "0"
    report = json.loads((output / "metadata.json").read_text())
    assert report["counts"] == {"n_input_rows": 3, "n_included_utterances": 2, "n_pairs": 1}
    assert report["scope"] == "thesis_eng_na"
    assert report["collection"] == "Eng-NA"
    assert report["annotation_scheme"] == "spaCy"
    assert report["input"]["text_column"] == "text"
    assert report["input"]["renamed_columns"] == {
        "n_pairs": "source_ud_n_pairs (pair count from the source UD annotation)"
    }
    assert report["input"]["utterances_sha256"]
    assert "files" not in report and "sources" not in report
    assert nlp.pipe_args == {"as_tuples": True, "batch_size": 128, "n_process": 3,
                             "disable": ["ner"]}


def test_existing_output_is_refused_without_changes(tmp_path):
    utterances = tmp_path / "utterances.csv"
    metadata = tmp_path / "metadata.json"
    write_csv(utterances, [{"included_in_cds": "True", "text": "zero"}])
    metadata.write_text(json.dumps({"scope": "x", "collection": "y"}))
    output = tmp_path / "out"
    output.mkdir()
    marker = output / "keep"
    marker.write_text("safe")
    with pytest.raises(FileExistsError):
        parse_matched(utterances, metadata, output, "text", 1, "fake", nlp=FakeNLP())
    assert marker.read_text() == "safe"


def test_custom_text_column_is_sent_exactly_and_count_mismatch_is_atomic(tmp_path):
    utterances = tmp_path / "utterances.csv"
    metadata = tmp_path / "metadata.json"
    write_csv(utterances, [{
        "included_in_cds": "True", "collection": "Eng-NA", "corpus": "C",
        "transcript": "a.cha", "utterance_id": "1", "target_child_age_months": "18",
        "text": "normalized", "original_text": "It's  spaced !",
    }])
    metadata.write_text(json.dumps({"scope": "x", "collection": "y",
                                    "counts": {"n_included_utterances": 2}}))
    nlp = CapturingNLP()
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="differs"):
        parse_matched(utterances, metadata, output, "original_text", 1, "fake", nlp=nlp)
    assert nlp.texts == ["It's  spaced !"]
    assert not output.exists()


def test_real_spacy_model_when_installed():
    spacy = pytest.importorskip("spacy")
    try:
        nlp = spacy.load("en_core_web_sm")
    except OSError:
        pytest.skip("en_core_web_sm is not installed")
    pairs = list(extract_doc_pairs(nlp("The child eats apples.")))
    assert [(p["subject_lemma"], p["verb_lemma"]) for p in pairs] == [("child", "eat")]
