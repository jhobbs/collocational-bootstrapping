import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from parse_matched_spacy import parse_matched
from parse_sharded_spacy import parse_sharded


def write_csv(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def read_rows(path):
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


@pytest.fixture
def real_input(tmp_path):
    spacy = pytest.importorskip("spacy")
    try:
        spacy.load("en_core_web_sm")
    except OSError:
        pytest.skip("en_core_web_sm is not installed")
    utterances = tmp_path / "utterances.csv"
    rows = []
    for index, text in enumerate([
        "The child eats apples.", "Birds fly.", "No dependency fragment",
        "My dog sleeps.", "They can swim.", "Rain fell.",
    ], 1):
        rows.append({"included_in_cds": "True", "collection": "Eng-NA",
                     "corpus": "Fixture", "transcript": "fixture.cha",
                     "utterance_id": str(index), "target_child_age_months": "24",
                     "text": text, "original_text": text, "n_pairs": "0"})
    write_csv(utterances, rows)
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps({
        "scope": "thesis_eng_na", "collection": "Eng-NA",
        "counts": {"n_cds_utterances": len(rows)},
        "matching_provenance": {"method": "fixture"},
    }))
    return utterances, metadata


def test_two_shards_match_single_parser_and_keep_source_order(real_input, tmp_path):
    utterances, metadata = real_input
    single = tmp_path / "single"
    sharded = tmp_path / "sharded"
    parse_matched(utterances, metadata, single, "text", 1, "en_core_web_sm")
    report = parse_sharded(utterances, metadata, sharded, "text", 2,
                           "en_core_web_sm")
    assert read_rows(sharded / "english_ud_subject_verb_pairs.csv") == read_rows(
        single / "english_ud_subject_verb_pairs.csv")
    assert report["counts"]["n_included_utterances"] == 6
    assert report["counts"]["n_pairs"] == len(read_rows(
        sharded / "english_ud_subject_verb_pairs.csv"))
    assert [record["n_utterances"] for record in report["shards"]] == [3, 3]
    assert [row["utterance_id"] for row in read_rows(
        sharded / "english_ud_subject_verb_pairs.csv")] == sorted(
            [row["utterance_id"] for row in read_rows(
                sharded / "english_ud_subject_verb_pairs.csv")], key=int)
    assert report["input"]["utterances_path"] == str(utterances.resolve())
    assert report["input"]["utterances_sha256"]
    assert report["input"]["metadata_sha256"]
    assert report["input"]["text_column"] == "text"
    assert not any(path.name.startswith("shard-") for path in sharded.iterdir())


def test_existing_output_is_refused(real_input, tmp_path):
    utterances, metadata = real_input
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "keep"
    marker.write_text("untouched")
    with pytest.raises(FileExistsError):
        parse_sharded(utterances, metadata, output, "text", 2, "en_core_web_sm")
    assert marker.read_text() == "untouched"
