import csv
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from augment_ud_copulas import augment
from extract_pairs import extract


CHAT = """@UTF8
@Begin
@Languages:\teng
@Participants:\tCHI Kid Target_Child, MOT Mom Mother
@ID:\teng|Sample|CHI|2;06.00|female|||Target_Child|||
@ID:\teng|Sample|MOT||female|||Mother|||
*MOT:\tshe is happy .
%mor:\tpron|she-Prs-Nom-S3 aux|be-Fin-Ind-Pres-S3 adj|happy .
%gra:\t1|3|NSUBJ 2|3|COP 3|0|ROOT 4|3|PUNCT
*MOT:\the is a teacher .
%mor:\tpron|he-Prs-Nom-S3 aux|be-Fin-Ind-Pres-S3 det|a noun|teacher .
%gra:\t1|4|NSUBJ 2|4|COP 3|4|DET 4|0|ROOT 5|4|PUNCT
*MOT:\tthey are in school .
%mor:\tpron|they-Prs-Nom-P3 aux|be-Fin-Ind-Pres-P3 adp|in n|school .
%gra:\t1|3|NSUBJ 2|3|COP 3|0|ROOT 4|3|OBL 5|3|PUNCT
*MOT:\tshe runs .
%mor:\tpron|she-Prs-Nom-S3 verb|run-Fin-Ind-Pres-S3 .
%gra:\t1|2|NSUBJ 2|0|ROOT 3|2|PUNCT
*MOT:\tshe is running .
%mor:\tpron|she-Prs-Nom-S3 aux|be-Fin-Ind-Pres-S3 verb|run-Part-Pres .
%gra:\t1|3|NSUBJ 2|3|AUX 3|0|ROOT 4|3|PUNCT
*MOT:\tshe is ready .
%mor:\tpron|she-Prs-Nom-S3 aux|be-Fin-Ind-Pres-S3 adj|ready .
%gra:\t1|3|NSUBJ-OUTER 2|3|COP 3|0|ROOT 4|3|PUNCT
*MOT:\tokay .
@End
"""


def rows(path):
    with path.open() as stream:
        return list(csv.DictReader(stream))


def population(tmp_path):
    archive = tmp_path / "Sample.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("Sample/sample.cha", CHAT)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"scope": "repository_english", "sources": [{
        "path": str(archive), "collection": "Eng-NA", "corpus": "Sample",
        "source_url": "https://example.invalid/Sample.zip", "download_date": "2026-09-07",
        "archive_id": "Eng-NA/Sample.zip"
    }]}))
    output = tmp_path / "population"
    extract(manifest, output, "%mor", "%gra")
    return output, archive


def test_real_chat_adds_only_nonverbal_exact_nsubj_copulas(tmp_path):
    source, _archive = population(tmp_path)
    output = tmp_path / "augmented"

    augment(source, output)

    additions = rows(output / "copula_additions.csv")
    assert [(r["predicate_pos"], r["subject_lemma"], r["verb_lemma"]) for r in additions] == [
        ("ADJ", "she", "be"), ("NOUN", "he", "be"), ("ADP", "they", "be")
    ]
    pairs = rows(output / "english_ud_subject_verb_pairs.csv")
    assert [(r["subject_lemma"], r["verb_lemma"]) for r in pairs] == [
        ("she", "be"), ("he", "be"), ("they", "be"), ("she", "run"), ("she", "run")
    ]
    assert [r["pair_origin"] for r in pairs] == [
        "explicit_copula", "explicit_copula", "explicit_copula", "strict_ud", "strict_ud"
    ]
    assert {r["target_child_age_months"] for r in additions} == {"30.0"}
    before = json.loads((source / "metadata.json").read_text())
    after = json.loads((output / "metadata.json").read_text())
    assert after["counts"]["n_cds_utterances"] == before["counts"]["n_cds_utterances"]
    assert after["counts"]["n_existing_pairs"] == len(rows(source / "english_ud_subject_verb_pairs.csv"))


def test_raw_member_hash_mismatch_is_rejected_atomically(tmp_path):
    source, archive = population(tmp_path)
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("Sample/sample.cha", CHAT.replace("happy", "sad"))
    output = tmp_path / "augmented"

    with pytest.raises(ValueError, match="hash"):
        augment(source, output)
    assert not output.exists()


def test_raw_strict_pair_identity_mismatch_is_rejected(tmp_path):
    source, _archive = population(tmp_path)
    path = source / "english_ud_subject_verb_pairs.csv"
    original = rows(path)
    original[0]["verb_lemma"] = "wrong"
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(original[0]))
        writer.writeheader(); writer.writerows(original)

    with pytest.raises(ValueError, match="identit"):
        augment(source, tmp_path / "augmented")
