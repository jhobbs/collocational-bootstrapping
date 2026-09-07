import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prepare_full_population import prepare_population, record_spacy_availability


def write_csv(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def rows(path):
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def test_filters_age_and_cds_streams_pairs_and_audits_zero_pair_rows(tmp_path):
    source = tmp_path / "source"; source.mkdir()
    base = {"collection": "Eng-NA", "corpus": "C", "transcript": "a.cha",
            "speaker": "MOT", "target_child_age_months": "24"}
    utterances = [
        {**base, "utterance_id": "1", "included_in_cds": "True", "annotation_status": "annotated", "n_pairs": "1", "text": "I run"},
        {**base, "utterance_id": "2", "included_in_cds": "True", "annotation_status": "missing_tiers", "n_pairs": "0", "text": ""},
        {**base, "utterance_id": "3", "included_in_cds": "False", "annotation_status": "annotated", "n_pairs": "1", "text": "skip"},
        {**base, "utterance_id": "4", "included_in_cds": "True", "target_child_age_months": "nan", "annotation_status": "annotated", "n_pairs": "1", "text": "skip"},
        {**base, "utterance_id": "5", "included_in_cds": "True", "target_child_age_months": "96", "annotation_status": "annotated", "n_pairs": "0", "text": "edge"},
    ]
    pairs = [{**base, "utterance_id": "1", "subject_lemma": "i", "verb_lemma": "run"},
             {**base, "utterance_id": "4", "target_child_age_months": "nan", "subject_lemma": "x", "verb_lemma": "go"}]
    write_csv(source / "english_ud_utterances.csv", utterances)
    write_csv(source / "english_ud_subject_verb_pairs.csv", pairs)
    (source / "metadata.json").write_text(json.dumps({"scope": "old", "files": [{"id": 1}], "sources": [{"id": 2}]}))
    out = tmp_path / "out"
    report = prepare_population(source, out, verify_expected=False)
    assert [r["utterance_id"] for r in rows(out / "english_ud_utterances.csv")] == ["1", "2", "5"]
    assert [r["utterance_id"] for r in rows(out / "english_ud_subject_verb_pairs.csv")] == ["1"]
    assert report["counts"]["n_cds_utterances"] == 3
    assert report["counts"]["n_pairs"] == 1
    assert report["text_counts"] == {"nonempty": 2, "empty": 1}
    assert report["annotation_status_counts"]["overall"]["missing_tiers"] == 1
    assert report["scope"] == "earlier_talkbank_population"
    assert report["files"] == [{"id": 1}] and report["sources"] == [{"id": 2}]
    assert "matching_provenance" not in report
    assert report["selection_provenance"]["rule"] == "included_in_cds == true and finite 0 <= target_child_age_months <= 96"


def test_existing_output_refused(tmp_path):
    source = tmp_path / "source"; source.mkdir()
    out = tmp_path / "out"; out.mkdir()
    with pytest.raises(FileExistsError):
        prepare_population(source, out, verify_expected=False)


def test_records_spacy_pairs_without_ud_annotation_by_age_and_collection(tmp_path):
    output = tmp_path / "spacy"; output.mkdir()
    write_csv(output / "english_ud_subject_verb_pairs.csv", [
        {"annotation_status": "annotated", "collection": "Eng-NA", "target_child_age_months": "24", "subject_lemma": "i"},
        {"annotation_status": "missing_tiers", "collection": "Eng-UK", "target_child_age_months": "25", "subject_lemma": "she"},
        {"annotation_status": "missing_tiers", "collection": "Eng-UK", "target_child_age_months": "25", "subject_lemma": "he"},
    ])
    (output / "metadata.json").write_text(json.dumps({"counts": {"n_pairs": 3}}))
    report = record_spacy_availability(output)
    assert report["pair_counts"]["overall"] == {"annotated": 1, "missing_tiers": 2}
    assert report["non_annotated_pairs"] == 2
    assert report["pair_counts"]["by_collection"]["Eng-UK"]["missing_tiers"] == 2
    assert report["pair_counts"]["by_age_group"]["24-36mo"]["missing_tiers"] == 2
    metadata = json.loads((output / "metadata.json").read_text())
    assert metadata["annotation_availability"]["sha256"]
