import csv
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fit_matched_comparison import prepare_eng_na_inputs, run, validate_inputs


def write_csv(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_inputs(tmp_path):
    matched = tmp_path / "matched"
    chat = tmp_path / "chat"
    original = tmp_path / "original"
    for directory in (matched, chat, original):
        directory.mkdir()
    utterances = [
        {"collection": "Eng-NA", "corpus": "Hall", "transcript": "Hall/a.cha",
         "utterance_id": "7", "target_child_age_months": "57.0", "included_in_cds": "True",
         "text": "CHAT surface here", "original_text": "historical gloss here"},
        {"collection": "Eng-UK", "corpus": "Wells", "transcript": "Wells/b.cha",
         "utterance_id": "8", "target_child_age_months": "31.5", "included_in_cds": "True",
         "text": "another surface", "original_text": "another historical gloss"},
    ]
    write_csv(matched / "english_ud_utterances.csv", utterances)
    pair_rows = [
        {"collection": row["collection"], "corpus": row["corpus"], "transcript": row["transcript"],
         "utterance_id": row["utterance_id"], "target_child_age_months": row["target_child_age_months"],
         "subject_lemma": "child", "verb_lemma": "speak"}
        for row in utterances
    ]
    for directory in (matched, chat, original):
        write_csv(directory / "english_ud_subject_verb_pairs.csv", pair_rows)
    source_hash = digest(matched / "english_ud_utterances.csv")
    (matched / "metadata.json").write_text(json.dumps({
        "scope": "matched_repository_english", "collection": "Eng-NA;Eng-UK",
        "counts": {"n_cds_utterances": 2}, "matching_provenance": {"utterance_rule": "exact"}
    }))
    for directory, column in ((chat, "text"), (original, "original_text")):
        (directory / "metadata.json").write_text(json.dumps({
            "scope": "matched_repository_english", "collection": "Eng-NA;Eng-UK",
            "annotation_scheme": "spaCy", "counts": {"n_input_rows": 2, "n_included_utterances": 2,
                                                          "n_pairs": 2},
            "input": {"utterances_path": str(matched / "english_ud_utterances.csv"),
                      "utterances_sha256": source_hash, "metadata_path": str(matched / "metadata.json"),
                      "metadata_sha256": "a" * 64, "text_column": column, "renamed_columns": {}},
            "parser": {"model": "en_core_web_sm"},
            "denominator": {"included_column": "included_in_cds"}
        }))
    return matched, chat, original


@pytest.mark.parametrize("broken", ["hash", "count"])
def test_validation_rejects_nonidentical_arm_inputs(tmp_path, broken):
    matched, chat, original = fixture_inputs(tmp_path)
    metadata_path = original / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    if broken == "hash":
        metadata["input"]["utterances_sha256"] = "0" * 64
    else:
        metadata["counts"]["n_included_utterances"] = 1
    metadata_path.write_text(json.dumps(metadata))

    with pytest.raises(ValueError, match=broken):
        validate_inputs(matched, chat, original)


def test_eng_na_filter_preserves_age_and_both_text_identities(tmp_path):
    matched, chat, original = fixture_inputs(tmp_path)
    destination = tmp_path / "eng_na"

    prepared = prepare_eng_na_inputs(matched, {"ud_chat": matched, "spacy_chat": chat,
                                                "spacy_original": original}, destination)

    with prepared["utterances"].open() as stream:
        rows = list(csv.DictReader(stream))
    assert rows == [{
        "collection": "Eng-NA", "corpus": "Hall", "transcript": "Hall/a.cha",
        "utterance_id": "7", "target_child_age_months": "57.0", "included_in_cds": "True",
        "text": "CHAT surface here", "original_text": "historical gloss here"
    }]
    for arm in ("ud_chat", "spacy_chat", "spacy_original"):
        with prepared["pairs"][arm].open() as stream:
            pairs = list(csv.DictReader(stream))
        assert [(p["collection"], p["target_child_age_months"], p["utterance_id"]) for p in pairs] == [
            ("Eng-NA", "57.0", "7")
        ]


def test_small_end_to_end_fit_publishes_live_paths_and_equal_arm_denominators(tmp_path):
    matched, chat, original = fixture_inputs(tmp_path)
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    labels = ["overall", "0-12mo", "12-24mo", "24-36mo", "36-48mo",
              "48-60mo", "60-72mo", "72-84mo", "84-96mo"]
    write_csv(baseline / "baseline_summary.csv", [
        {"age_group": label, "alpha": "1.4", "status": "ok"} for label in labels
    ])
    output = tmp_path / "new-parent" / "comparison"

    run(matched, chat, original, baseline, output)

    with (output / "matched_comparison_summary.csv").open() as stream:
        summary = list(csv.DictReader(stream))
    assert len(summary) == 54
    overall = [row for row in summary if row["age_group"] == "overall"]
    assert {(row["scope"], row["arm"], row["n_utterances"]) for row in overall} == {
        (scope, arm, count)
        for scope, count in (("matched_repository_english", "2"), ("matched_eng_na", "1"))
        for arm in ("ud_chat", "spacy_chat", "spacy_original")
    }
    for fit_metadata in output.glob("matched_*/*/fit_metadata.json"):
        metadata = json.loads(fit_metadata.read_text())
        assert all(Path(path).exists() for path in metadata["inputs"].values())
