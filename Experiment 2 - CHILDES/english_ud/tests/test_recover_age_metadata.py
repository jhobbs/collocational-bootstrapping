"""Identity and provenance checks for streaming historical age enrichment."""
import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def write_csv(path, rows):
    with path.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def read_csv(path):
    return list(csv.DictReader(path.open()))


def fixture(tmp_path, age=None, collection='Eng-NA', name=''):
    source = tmp_path / 'source'
    source.mkdir()
    record = dict(collection=collection, corpus='Sample', transcript='Sample/a.cha',
                  archive_id=f'{collection}/Sample.zip', member='a.cha', sha256='current-hash',
                  target_child=name, target_child_code='CHI' if age is not None else '',
                  target_child_age='0;11.29' if age is not None else '',
                  target_child_age_months=age,
                  age_status='valid' if age is not None else 'missing_target',
                  target_selection='CHI_code' if age is not None else 'none',
                  status='parsed', annotation_comments=[])
    metadata = dict(scope='repository_english', collection=collection, files=[record],
                    sources=[dict(collection=collection, archive_id=record['archive_id'], path='archive.zip')],
                    counts={'n_files': 1, 'n_pairs': 1, 'n_changeable_headers': 9},
                    diagnostics={'nsubj_nonverbal_head': 20}, pos_counts={'VERB': 50},
                    dependency_counts={'NSUBJ': 30}, parse_errors=[], complete_parse=True,
                    annotation_scheme='Universal Dependencies', morphology_tier='%mor', grammar_tier='%gra')
    (source / 'metadata.json').write_text(json.dumps(metadata))
    base = {k: record[k] for k in ('collection', 'corpus', 'transcript', 'target_child', 'target_child_code',
                                 'target_child_age', 'target_child_age_months', 'age_status', 'target_selection')}
    base.update(language='eng', speaker='MOT', speaker_role='Mother', utterance_id=1)
    write_csv(source / 'english_ud_utterances.csv', [{**base, 'included_in_cds': True,
        'annotation_status': 'annotated', 'n_pairs': 1, 'text': 'you go', 'chat_main_tier': 'you go .'}])
    write_csv(source / 'english_ud_subject_verb_pairs.csv', [{**base, 'subject_lemma': 'you', 'verb_lemma': 'go'}])
    candidate = dict(transcript='Sample/a.cha', current_pid='11312/c-00001-1', legacy_pid='11312/c-00001-1',
                     exact_pid_matches=1, current_chat_sha256='current-hash', same_speaker_sequence=True,
                     roles_preserved_for_all_current_codes=True, asr_comment=False,
                     anonymous_participants_only=False, underscore_normalized_position_agreement=0.96,
                     legacy_header=dict(collection='Eng-NA', pid='11312/c-00001-1', chat_sha256='old-hash',
                        archive_member='Eng-NA/Sample/a.cha', participants_tier='CHI Alice Target_Child, MOT Mother',
                        ids=[{'raw_id': 'eng|Sample|CHI|1;00.00|female|||Target_Child|||'},
                             {'raw_id': 'eng|Sample|MOT||female|||Mother|||'}]))
    legacy = tmp_path / 'legacy.json'
    legacy.write_text(json.dumps({'summary': {'source_url': 'https://example.invalid/legacy.zip'},
                                 'transcripts': {'Sample/a.cha': candidate}}))
    return source, legacy, candidate


def links(tmp_path, original_age, name='Alice', duplicate=False):
    import hashlib
    p = tmp_path / 'links'
    p.mkdir()
    write_csv(p / 'transcript_links.csv', [dict(collection='Eng-NA', corpus='Sample', transcript='Sample/a.cha',
        original_transcript_id=123, status='high_confidence_text_match', unique_matching_utterances=10,
        runner_up_matches=0, total_matching_utterances=10, original_target_child=name,
        original_age_months=original_age, original_id_used_by_multiple_current_transcripts=duplicate)])
    source = tmp_path / 'original.csv'
    source.write_text('synthetic original source\n')
    (p / 'metadata.json').write_text(json.dumps({
        'original': str(source), 'original_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'transcript_links_sha256': hashlib.sha256((p / 'transcript_links.csv').read_bytes()).hexdigest(),
        'rule': 'synthetic test rule'}))
    return p


def test_safe_legacy_recovery_updates_both_csvs_and_file_metadata_with_raw_provenance(tmp_path):
    from recover_age_metadata import enrich
    source, legacy, _ = fixture(tmp_path)
    output = tmp_path / 'out'
    enrich(source, legacy, output)
    for filename in ('english_ud_utterances.csv', 'english_ud_subject_verb_pairs.csv'):
        row = read_csv(output / filename)[0]
        assert float(row['target_child_age_months']) == 12
        assert row['target_child'] == 'Alice'
        assert row['target_child_code'] == 'CHI'
        assert row['chat_original_target_child_age_months'] == ''
        assert row['chat_original_age_status'] == 'missing_target'
        assert row['age_source'] == 'legacy_chat_verified_pid'
        assert row['speaker_role'] == 'Mother'
    meta = json.loads((output / 'metadata.json').read_text())
    assert meta['files'][0]['target_child_age_months'] == 12
    assert meta['counts']['n_pairs_missing_age'] == 0
    assert meta['age_enrichment']['counts']['files_recovered'] == 1
    assert meta['age_enrichment']['counts']['utterances_recovered'] == 1
    assert meta['age_enrichment']['counts']['pairs_recovered'] == 1
    assert meta['files'][0]['age_evidence']['legacy_pid'] == '11312/c-00001-1'


@pytest.mark.parametrize('change', [
    {'legacy_pid': '11312/a-00001-1'}, {'exact_pid_matches': 2}, {'asr_comment': True},
    {'anonymous_participants_only': True}, {'same_speaker_sequence': False},
    {'roles_preserved_for_all_current_codes': False}, {'underscore_normalized_position_agreement': .89},
    {'current_chat_sha256': 'different-file'},
])
def test_rejects_unverified_legacy_identity_without_guessing_age(tmp_path, change):
    from recover_age_metadata import enrich
    source, legacy, candidate = fixture(tmp_path)
    candidate.update(change)
    legacy.write_text(json.dumps({'transcripts': {'Sample/a.cha': candidate}}))
    enrich(source, legacy, tmp_path / 'out')
    row = read_csv(tmp_path / 'out/english_ud_subject_verb_pairs.csv')[0]
    assert row['target_child_age_months'] == ''
    assert row['age_source'] == 'missing'
    assert json.loads((tmp_path / 'out/metadata.json').read_text())['age_enrichment']['decisions'][0]['legacy_status'].startswith('rejected')


def test_original_rounding_adjustment_preserves_chat_age_and_records_shifted_bin(tmp_path):
    from recover_age_metadata import enrich
    source, legacy, _ = fixture(tmp_path, age=11.999, name='Alice')
    original = links(tmp_path, 12.0002)
    enrich(source, legacy, tmp_path / 'out', original_links=original)
    row = read_csv(tmp_path / 'out/english_ud_subject_verb_pairs.csv')[0]
    assert float(row['target_child_age_months']) == 12.0002
    assert float(row['chat_original_target_child_age_months']) == 11.999
    assert row['age_source'] == 'original_childes_db_corroborated'
    m = json.loads((tmp_path / 'out/metadata.json').read_text())
    assert m['age_enrichment']['counts']['pairs_shifted_age_bin'] == 1
    assert m['age_enrichment']['original_link_provenance']['original'].endswith('original.csv')


@pytest.mark.parametrize('original_age,name,duplicate', [(15, 'Alice', False), (12, 'Bob', False), (12, 'Alice', True)])
def test_original_age_conflict_name_conflict_or_duplicate_link_not_overwritten(tmp_path, original_age, name, duplicate):
    from recover_age_metadata import enrich
    source, legacy, _ = fixture(tmp_path, age=11.999, name='Alice')
    enrich(source, legacy, tmp_path / 'out', original_links=links(tmp_path, original_age, name, duplicate))
    row = read_csv(tmp_path / 'out/english_ud_subject_verb_pairs.csv')[0]
    assert float(row['target_child_age_months']) == 11.999
    assert row['age_source'] == 'current_chat'
    meta = json.loads((tmp_path / 'out/metadata.json').read_text())
    assert meta['age_enrichment']['decisions'][0]['original_status'].startswith('rejected')


def test_original_link_alone_cannot_recover_unidentified_age(tmp_path):
    from recover_age_metadata import enrich
    source, legacy, _ = fixture(tmp_path)
    legacy.write_text(json.dumps({'transcripts': {}}))
    enrich(source, legacy, tmp_path / 'out', original_links=links(tmp_path, 12))
    assert read_csv(tmp_path / 'out/english_ud_subject_verb_pairs.csv')[0]['target_child_age_months'] == ''


def test_collection_filter_recomputes_counts_roles_and_does_not_keep_global_diagnostics(tmp_path):
    from recover_age_metadata import enrich
    source, legacy, _ = fixture(tmp_path)
    metadata = json.loads((source / 'metadata.json').read_text())
    uk = {**metadata['files'][0], 'collection': 'Eng-UK', 'archive_id': 'Eng-UK/Sample.zip'}
    metadata['files'].append(uk)
    metadata['sources'].append({'collection': 'Eng-UK', 'archive_id': uk['archive_id']})
    (source / 'metadata.json').write_text(json.dumps(metadata))
    for filename in ('english_ud_utterances.csv', 'english_ud_subject_verb_pairs.csv'):
        row = read_csv(source / filename)[0]
        write_csv(source / filename, [row, {**row, 'collection': 'Eng-UK', 'speaker_role': 'Father'}])
    enrich(source, legacy, tmp_path / 'out', collection='Eng-NA')
    m = json.loads((tmp_path / 'out/metadata.json').read_text())
    assert m['scope'] == 'thesis_eng_na'
    assert m['collection'] == 'Eng-NA'
    assert len(m['files']) == len(m['sources']) == 1
    assert m['counts']['n_files'] == m['counts']['n_pairs'] == m['counts']['n_cds_utterances'] == 1
    assert m.get('diagnostics') != {'nsubj_nonverbal_head': 20}
    assert read_csv(tmp_path / 'out/speaker_roles.csv') == [{'speaker_role': 'Mother', 'utterance_count': '1', 'included_in_cds': 'True'}]


def test_refuses_existing_output_and_removes_stage_on_invalid_row_identity(tmp_path):
    from recover_age_metadata import enrich
    source, legacy, _ = fixture(tmp_path)
    out = tmp_path / 'out'
    out.mkdir()
    with pytest.raises(FileExistsError):
        enrich(source, legacy, out)
    out.rmdir()
    row = read_csv(source / 'english_ud_subject_verb_pairs.csv')[0]
    write_csv(source / 'english_ud_subject_verb_pairs.csv', [{**row, 'transcript': 'unknown.cha'}])
    with pytest.raises(ValueError, match='transcript'):
        enrich(source, legacy, out)
    assert not out.exists()
    assert not list(tmp_path.glob('.recover-*'))


def test_verified_legacy_identity_can_corroborate_original_day_based_age(tmp_path):
    from recover_age_metadata import enrich
    source, legacy, _ = fixture(tmp_path)
    enrich(source, legacy, tmp_path / 'out', original_links=links(tmp_path, 12.0002))
    row = read_csv(tmp_path / 'out/english_ud_subject_verb_pairs.csv')[0]
    assert float(row['target_child_age_months']) == 12.0002
    assert row['target_child_age'] == '1;00.00'
    assert row['target_child'] == 'Alice'
    assert row['chat_original_target_child_code'] == ''
    meta = json.loads((tmp_path / 'out/metadata.json').read_text())
    assert meta['files'][0]['target_participants'] == [{'code': 'CHI', 'name': 'Alice', 'age': '1;00.00'}]
    assert meta['files'][0]['age_evidence']['legacy_pid'] == '11312/c-00001-1'
    assert meta['files'][0]['age_evidence']['original_transcript_id'] == '123'


def test_original_link_does_not_adjust_asr_even_when_a_chat_age_is_present(tmp_path):
    from recover_age_metadata import enrich
    source, legacy, _ = fixture(tmp_path, age=12, name='Alice')
    m = json.loads((source / 'metadata.json').read_text())
    m['files'][0]['annotation_comments'] = ['Batchalign ASR unchecked']
    (source / 'metadata.json').write_text(json.dumps(m))
    enrich(source, legacy, tmp_path / 'out', original_links=links(tmp_path, 12.0002))
    row = read_csv(tmp_path / 'out/english_ud_subject_verb_pairs.csv')[0]
    assert float(row['target_child_age_months']) == 12
    assert row['age_source'] == 'current_chat'
