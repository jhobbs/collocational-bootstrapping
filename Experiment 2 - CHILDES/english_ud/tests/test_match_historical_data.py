"""Protect transcript identity and observation multiplicity in matched analyses."""
import csv
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from match_historical_data import canonical_words, eligibility_reason, match_occurrences, build_matched


def test_word_matching_allows_known_clitics_but_preserves_word_boundaries():
    assert canonical_words("I do n't think she's ready .") == ("i", "don't", "think", "she's", "ready")
    assert canonical_words("I don't think she 's ready") == ("i", "don't", "think", "she's", "ready")
    assert canonical_words("a name") != canonical_words("an ame")
    assert canonical_words("the rapist") != canonical_words("therapist")
    assert canonical_words("re-sign") != canonical_words("resign")


def _link(**changes):
    return dict(status="high_confidence_text_match", unique_matching_utterances="12",
                runner_up_matches="1", total_matching_utterances="13",
                original_transcript_id="17", original_age_months="24.0004",
                original_target_child="Alice", original_id_used_by_multiple_current_transcripts="False", **changes)


def test_age_and_identity_corroboration_rejects_unmatched_or_conflicting_children():
    record = dict(target_child_age_months=24, target_child="Alice", target_child_code="CHI", status="parsed")
    assert eligibility_reason(record, _link()) == ""
    for key, value, reason in [("target_child_age_months", None, "missing_chat_age"),
                               ("target_child_age_months", 30, "age_conflict"),
                               ("target_child", "Bob", "target_name_conflict")]:
        assert eligibility_reason({**record, key: value}, _link()) == reason
    link = _link(); link["original_id_used_by_multiple_current_transcripts"] = "True"
    assert eligibility_reason(record, link) == "duplicate_original_id"


def test_repeated_utterances_match_once_and_require_same_speaker_and_role():
    original = [dict(speaker_code="MOT", speaker_role="Mother", full_utterance="you go", utterance_order=str(i)) for i in [1, 3]]
    current = [dict(speaker="MOT", speaker_role="Mother", text="you go .", utterance_id=str(i)) for i in [2, 4, 6]]
    current += [dict(speaker="FAT", speaker_role="Father", text="you go .", utterance_id="8")]
    result = list(match_occurrences(original, current))
    assert [(a["utterance_order"], b["utterance_id"]) for a, b in result] == [("1", "2"), ("3", "4")]


def test_changed_utterance_boundaries_do_not_create_a_match():
    original = [dict(speaker_code="MOT", speaker_role="Mother", full_utterance="you go home", utterance_order="1")]
    current = [dict(speaker="MOT", speaker_role="Mother", text="you go .", utterance_id="1"),
               dict(speaker="MOT", speaker_role="Mother", text="home .", utterance_id="2")]
    assert list(match_occurrences(original, current)) == []


def _fixture(tmp_path, asr=False):
    from extract_pairs import extract
    source = tmp_path / "toy.cha"
    header = "@UTF8\n@Begin\n@Languages:\teng\n@Participants:\tCHI Alice Target_Child, MOT Mother\n@ID:\teng|Toy|CHI|2;00|female|||Target_Child|||\n@ID:\teng|Toy|MOT||female|||Mother|||\n"
    if asr:
        header += "@Comment:\tUnchecked ASR output\n"
    source.write_text(header + ("*MOT:\tyou go .\n%mor:\tpron|you verb|go .\n%gra:\t1|2|NSUBJ 2|0|ROOT 3|2|PUNCT\n" * 3) + "@End\n")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(dict(scope="thesis_eng_na", sources=[dict(path=str(source), corpus="Toy", collection="Eng-NA", source_url="https://example.invalid/toy", download_date=None, archive_id="toy")])) )
    extraction = tmp_path / "extraction"
    extract(manifest, extraction, "%mor", "%gra")
    original = tmp_path / "original.csv"
    rows = [dict(transcript_id="17", speaker_id="1", speaker_code="MOT", speaker_role="Mother", full_utterance="you go", utterance_order=str(i), target_child_age="24.0004", target_child_name="Alice", corpus_name="Toy") for i in [1, 3]]
    with original.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    links = tmp_path / "links"; links.mkdir()
    row = dict(_link(), collection="Eng-NA", corpus="Toy", transcript="Toy/toy.cha")
    lp = links / "transcript_links.csv"
    with lp.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(row)); w.writeheader(); w.writerow(row)
    (links / 'metadata.json').write_text(json.dumps(dict(original=str(original), original_sha256=hashlib.sha256(original.read_bytes()).hexdigest(), transcript_links_sha256=hashlib.sha256(lp.read_bytes()).hexdigest())))
    return original, extraction, links


def test_matched_tables_keep_historical_age_and_zero_duplicate_inflation(tmp_path):
    original, extraction, links = _fixture(tmp_path)
    out = tmp_path / 'matched'
    build_matched(original, [extraction], [links], out)
    with (out / 'english_ud_utterances.csv').open() as f: utterances = list(csv.DictReader(f))
    with (out / 'english_ud_subject_verb_pairs.csv').open() as f: pairs = list(csv.DictReader(f))
    assert len(utterances) == len(pairs) == 2
    assert [r['original_utterance_order'] for r in utterances] == ['1', '3']
    assert {r['target_child_age_months'] for r in utterances + pairs} == {'24.0004'}
    assert {r['original_text'] for r in utterances} == {'you go'}
    assert {r['text'] for r in utterances} == {'you go .'}


def test_raw_asr_and_changed_link_hash_cannot_enter_cohort(tmp_path):
    original, extraction, links = _fixture(tmp_path, asr=True)
    out = tmp_path / 'matched'
    with pytest.raises(ValueError, match='eligible'):
        build_matched(original, [extraction], [links], out)
    assert not out.exists()
    with (links / 'transcript_links.csv').open('a') as f: f.write('\n')
    with pytest.raises(ValueError, match='hash'):
        build_matched(original, [extraction], [links], out)


def test_explicit_later_corpus_source_replaces_earlier_copy(tmp_path):
    original, extraction, links = _fixture(tmp_path)
    out = tmp_path / 'matched'
    meta = build_matched(original, [extraction, extraction], [links, links], out)
    assert meta['n_cohort_transcripts'] == 1
    assert meta['counts']['matched_utterances'] == 2
    assert meta['rejection_counts']['superseded_corpus_source'] == 1
