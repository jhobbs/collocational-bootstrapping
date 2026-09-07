"""Small constructed CHAT examples, not research observations."""
import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from extract_pairs import extract, inspect_sources, lemma_from_mor


def chat(age="1;00.15", mor="%mor", gra="%gra"):
    return f"""@UTF8
@Begin
@Languages:	eng
@Participants:	CHI Alice Target_Child, MOT Mother, SIS Sister, OTH Child
@ID:	eng|Sample|CHI|{age}|female|||Target_Child|||
@ID:	eng|Sample|MOT||female|||Mother|||
@ID:	eng|Sample|SIS|4;00.00|female|||Sister|||
@ID:	eng|Sample|OTH|4;00.00|female|||Child|||
*MOT:	you go .
{mor}:	pron|you-Prs-Nom-S2 verb|go-Fin-Ind-Pres-S2 .
{gra}:	1|2|NSUBJ 2|0|ROOT 3|2|PUNCT
*CHI:	I go .
{mor}:	pron|I-Prs verb|go-Fin .
{gra}:	1|2|NSUBJ 2|0|ROOT 3|2|PUNCT
*SIS:	he is happy .
{mor}:	pron|he-Prs aux|be-Fin adj|happy .
{gra}:	1|3|NSUBJ 2|3|COP 3|0|ROOT 4|3|PUNCT
*OTH:	they run .
{mor}:	pron|they-Prs verb|run-Fin .
{gra}:	1|2|NSUBJ 2|0|ROOT 3|2|PUNCT
*MOT:	okay .
@End
"""


def manifest(tmp_path, text, collection="Eng-NA"):
    corpus = tmp_path / collection / "Sample"
    corpus.mkdir(parents=True)
    (corpus / "sample.cha").write_text(text)
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"scope": "thesis_eng_na", "sources": [{
        "path": str(corpus.parent), "collection": collection,
        "source_url": "https://example.invalid/constructed-fixture",
        "download_date": None, "archive_id": "synthetic-test-only",
    }]}))
    return path


def rows(path):
    with path.open() as f:
        return list(csv.DictReader(f))


def test_role_filter_age_and_nonverbal_copula_head(tmp_path):
    source = manifest(tmp_path, chat())
    out = tmp_path / "out"
    extract(source, out, "%mor", "%gra")
    pairs = rows(out / "english_ud_subject_verb_pairs.csv")
    assert [(p["subject_lemma"], p["verb_lemma"]) for p in pairs] == [("you", "go")]
    assert float(pairs[0]["target_child_age_months"]) == pytest.approx(12.5)
    assert pairs[0]["corpus"] == "Sample"
    assert pairs[0]["transcript"] == "Sample/sample.cha"
    assert pairs[0]["subject_index"] == "1"
    utterances = rows(out / "english_ud_utterances.csv")
    assert len(utterances) == 5
    assert sum(u["included_in_cds"] == "True" for u in utterances) == 3
    assert utterances[-1]["annotation_status"] == "missing_tiers"
    metadata = json.loads((out / "metadata.json").read_text())
    assert metadata["counts"]["n_pairs"] == 1
    assert metadata["counts"]["n_cds_utterances"] == 3
    assert len(metadata["files"][0]["sha256"]) == 64


def test_missing_age_keeps_pairs_for_audit(tmp_path):
    source = manifest(tmp_path, chat(age=""))
    out = tmp_path / "out"
    extract(source, out, "%mor", "%gra")
    assert rows(out / "english_ud_subject_verb_pairs.csv")[0]["target_child_age_months"] == ""
    meta = json.loads((out / "metadata.json").read_text())
    assert meta["counts"]["n_files_missing_age"] == 1
    assert meta["counts"]["n_pairs_missing_age"] == 1


def test_custom_tiers_and_inventory(tmp_path):
    source = manifest(tmp_path, chat(mor="%umor", gra="%ugra"))
    report = inspect_sources(source)
    assert report["tier_counts"]["%umor"] == 4
    out = tmp_path / "out"
    extract(source, out, "%umor", "%ugra")
    assert len(rows(out / "english_ud_subject_verb_pairs.csv")) == 1


def test_asr_comment_is_preserved_for_age_recovery_checks(tmp_path):
    note = "ASR transcript, unchecked."
    source = manifest(tmp_path, chat().replace("@Begin", "@Begin\n@Comment:\t" + note))
    report = inspect_sources(source)
    out = tmp_path / "out"
    extract(source, out, "%mor", "%gra")
    metadata = json.loads((out / "metadata.json").read_text())
    assert note in report["files"][0]["annotation_comments"]
    assert note in metadata["files"][0]["annotation_comments"]


def test_legacy_scheme_rejected_without_publishing_output(tmp_path):
    source = manifest(tmp_path, chat().replace("NSUBJ", "SUBJ").replace("verb|", "v|"))
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="legacy|Legacy"):
        extract(source, out, "%mor", "%gra")
    assert not out.exists()


def test_no_selected_annotations_rejected(tmp_path):
    source = manifest(tmp_path, chat())
    with pytest.raises(ValueError, match="selected|annotation"):
        extract(source, tmp_path / "out", "%umor", "%ugra")


def test_scope_mixing_rejected(tmp_path):
    source = manifest(tmp_path, chat(), collection="Eng-UK")
    with pytest.raises(ValueError, match="Eng-NA"):
        extract(source, tmp_path / "out", "%mor", "%gra")


def test_lemma_retains_lexical_hyphens():
    assert lemma_from_mor("ice-cream-Plur") == "ice-cream"
    assert lemma_from_mor("go-Fin-Ind-Pres-S3") == "go"
    assert lemma_from_mor("I-Prs-Nom-S1") == "i"
    assert lemma_from_mor("peek-a-boo") == "peek-a-boo"


def test_zip_input_same_identifiers(tmp_path):
    import zipfile
    source = manifest(tmp_path, chat())
    config = json.loads(source.read_text())
    archive = tmp_path / "Sample.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("Sample/sample.cha", chat())
    config["sources"][0]["path"] = str(archive)
    source.write_text(json.dumps(config))
    extract(source, tmp_path / "out", "%mor", "%gra")
    assert rows(tmp_path / "out/english_ud_subject_verb_pairs.csv")[0]["transcript"] == "Sample/sample.cha"


def test_changeable_headers_are_not_utterances(tmp_path):
    source = manifest(tmp_path, chat().replace("*CHI:", "@Comment:\tmid-transcript note\n*CHI:"))
    extract(source, tmp_path / "out", "%mor", "%gra")
    utterances = rows(tmp_path / "out/english_ud_utterances.csv")
    assert len(utterances) == 5
    assert [r["utterance_id"] for r in utterances] == ["1", "2", "3", "4", "5"]


def test_relaxed_main_tier_validation_is_audited(tmp_path):
    source = manifest(tmp_path, chat().replace("you go .", "you (.)” go ."))
    extract(source, tmp_path / "out", "%mor", "%gra")
    assert len(rows(tmp_path / "out/english_ud_subject_verb_pairs.csv")) == 1
    meta = json.loads((tmp_path / "out/metadata.json").read_text())
    assert meta["counts"]["n_files_relaxed_validation"] == 1
    assert meta["files"][0]["strict_validation_error"]


def test_incomplete_dependency_tier_cannot_misattach_head(tmp_path):
    source = manifest(tmp_path, chat().replace("1|2|NSUBJ 2|0|ROOT 3|2|PUNCT", "1|3|NSUBJ 3|0|ROOT", 1))
    extract(source, tmp_path / "out", "%mor", "%gra")
    assert rows(tmp_path / "out/english_ud_subject_verb_pairs.csv") == []
    meta = json.loads((tmp_path / "out/metadata.json").read_text())
    assert meta["counts"]["n_utterances_invalid_dependencies"] == 1
    assert rows(tmp_path / "out/english_ud_utterances.csv")[0]["annotation_status"] == "invalid_dependencies"


def test_misalignment_skips_only_affected_utterance(tmp_path):
    source = manifest(tmp_path, chat().replace("pron|you-Prs-Nom-S2 verb|go-Fin-Ind-Pres-S2 .", "pron|you-Prs-Nom-S2 .", 1))
    extract(source, tmp_path / "out", "%mor", "%gra")
    assert rows(tmp_path / "out/english_ud_subject_verb_pairs.csv") == []
    meta = json.loads((tmp_path / "out/metadata.json").read_text())
    assert meta["counts"]["n_utterances_misaligned"] == 1
    assert meta["counts"]["n_utterances"] == 5
    assert meta["complete_parse"] is False


def test_chi_is_primary_when_another_target_child_is_present(tmp_path):
    text = chat().replace("OTH Child", "OTH Target_Child").replace("|||Child|||", "|||Target_Child|||")
    source = manifest(tmp_path, text)
    extract(source, tmp_path / "out", "%mor", "%gra")
    pair = rows(tmp_path / "out/english_ud_subject_verb_pairs.csv")[0]
    assert pair["target_child_code"] == "CHI"
    assert float(pair["target_child_age_months"]) == 12.5
    assert pair["target_selection"] == "CHI_code"
