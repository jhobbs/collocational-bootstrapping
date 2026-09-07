import csv
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit_copula_pairs import audit


def write_csv(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path):
    population, augmented, spacy = (tmp_path / name for name in ("population", "augmented", "spacy"))
    for directory in (population, augmented, spacy): directory.mkdir()
    utterance = {"language": "eng", "collection": "Eng-NA", "corpus": "Sample",
                 "transcript": "Sample/a.cha", "target_child_age_months": "30.0",
                 "speaker": "MOT", "speaker_role": "Mother", "utterance_id": "1",
                 "included_in_cds": "True", "annotation_status": "annotated", "n_pairs": "1",
                 "text": "she is happy", "original_transcript_id": "10", "original_text": "she is happy"}
    strict = {key: utterance[key] for key in ("language", "collection", "corpus", "transcript",
                                               "target_child_age_months", "speaker", "speaker_role",
                                               "utterance_id", "original_transcript_id", "original_text")}
    strict.update(subject_lemma="she", verb_lemma="run", subject_index="1", verb_index="2")
    addition = dict(strict, verb_lemma="be", verb_index="3")
    write_csv(population / "english_ud_utterances.csv", [utterance])
    write_csv(population / "english_ud_subject_verb_pairs.csv", [strict])
    write_csv(augmented / "english_ud_subject_verb_pairs.csv",
              [dict(strict, pair_origin="strict_ud"), dict(addition, pair_origin="explicit_copula")])
    write_csv(augmented / "copula_additions.csv",
              [dict(addition, pair_origin="explicit_copula", predicate_lemma="happy", predicate_index="4")])
    write_csv(spacy / "english_ud_subject_verb_pairs.csv", [dict(strict, verb_lemma="smile")])
    pop_hash = sha(population / "english_ud_utterances.csv")
    strict_hash = sha(population / "english_ud_subject_verb_pairs.csv")
    (population / "metadata.json").write_text(json.dumps({"counts": {"n_cds_utterances": 1, "n_pairs": 1}}))
    (augmented / "metadata.json").write_text(json.dumps({
        "counts": {"n_cds_utterances": 1, "n_existing_pairs": 1, "n_copula_additions": 1,
                   "n_augmented_pairs": 2, "n_pairs": 2}, "raw_source_members_verified": True,
        "input": {"utterances_sha256": pop_hash, "strict_pairs_sha256": strict_hash}
    }))
    (spacy / "metadata.json").write_text(json.dumps({
        "counts": {"n_included_utterances": 1, "n_pairs": 1},
        "input": {"utterances_sha256": pop_hash}
    }))
    return population, augmented, spacy


def test_positive_fixture_records_reconciled_counts(tmp_path):
    population, augmented, spacy = fixture(tmp_path)
    output = tmp_path / "audit.json"
    report = audit(population, augmented, spacy, output)
    assert report["counts"] == {"population_utterances": 1, "strict_pairs": 1,
                                 "copula_additions": 1, "augmented_pairs": 2, "spacy_pairs": 1}
    assert report["status"] == "passed" and output.is_file()


@pytest.mark.parametrize("tamper", ["strict", "age", "duplicate", "missing_age_column", "missing_key_column"])
def test_tampering_is_rejected_without_output(tmp_path, tamper):
    population, augmented, spacy = fixture(tmp_path)
    target = spacy / "english_ud_subject_verb_pairs.csv" if tamper in {"missing_age_column", "missing_key_column"} else augmented / "english_ud_subject_verb_pairs.csv"
    rows = list(csv.DictReader(target.open()))
    if tamper == "strict": rows[0]["verb_lemma"] = "wrong"
    elif tamper == "age": rows[1]["target_child_age_months"] = "31.0"
    elif tamper == "duplicate": rows.append(dict(rows[1]))
    elif tamper == "missing_age_column":
        rows = [{key: value for key, value in row.items() if key != "target_child_age_months"} for row in rows]
    else:
        rows = [{key: value for key, value in row.items() if key != "verb_index"} for row in rows]
    write_csv(target, rows)
    with pytest.raises(ValueError, match={"strict": "strict", "age": "metadata", "duplicate": "duplicate",
                                          "missing_age_column": "missing required metadata",
                                          "missing_key_column": "missing required key"}[tamper]):
        audit(population, augmented, spacy, tmp_path / "audit.json")
    assert not (tmp_path / "audit.json").exists()
