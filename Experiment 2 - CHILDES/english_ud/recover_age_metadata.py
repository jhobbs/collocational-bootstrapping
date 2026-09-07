"""Enrich extracted ages from verified historical identity evidence, without reparsing UD."""
import argparse
from collections import Counter
import copy
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sys
import tempfile

import pylangacq


TARGET_FIELDS = ('target_child', 'target_child_code', 'target_child_age',
                 'target_child_age_months', 'age_status', 'target_selection')
EXTRA_FIELDS = tuple('chat_original_' + key for key in TARGET_FIELDS) + ('age_source', 'metadata_source')
CSV_NAMES = ('english_ud_utterances.csv', 'english_ud_subject_verb_pairs.csv')


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError):
        return None


def yes(value):
    return value is True or str(value).strip().lower() in {'true', '1'}


def age_bin(age):
    if age is None:
        return 'missing'
    if age >= 96:
        return 'outside_0_96'
    lower = int(age // 12) * 12
    return f'{lower}-{lower + 12}'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def name_check(current, original, code):
    def normalize(value):
        return re.sub(r'[^a-z0-9]', '', (value or '').casefold())
    a, b = normalize(current), normalize(original)
    placeholders = {'', 'chi', 'na', 'none', 'unknown', 'targetchild', normalize(code)}
    if a in placeholders or b in placeholders:
        return 'uninformative'
    return 'compatible' if a == b else 'conflicting'


def legacy_target(header):
    """Ask PyLangAcq to interpret participant headers only; no utterances/UD are supplied."""
    lines = ['@UTF8', '@Begin', '@Languages:\teng']
    if header.get('participants_tier'):
        lines.append('@Participants:\t' + header['participants_tier'])
    lines.extend('@ID:\t' + entry['raw_id'] for entry in header['ids'])
    lines.append('@End')
    chat = pylangacq.CHAT.from_strs(['\n'.join(lines) + '\n'], parallel=False, strict=True)
    people = chat.headers()[0].participants
    chi = [p for p in people if p.code == 'CHI']
    targets = [p for p in people if p.role == 'Target_Child']
    child = chi[0] if len(chi) == 1 else targets[0] if len(targets) == 1 else None
    if child is None or child.role != 'Target_Child' or not child.age:
        raise ValueError('Historical header has no uniquely selected target child with age')
    age = number(child.age.in_months())
    if age is None:
        raise ValueError('Historical target age is invalid')
    raw_age = next(entry['raw_id'].split('|')[3] for entry in header['ids']
                   if entry['raw_id'].split('|')[2] == child.code)
    return dict(target_child=child.name or child.code, target_child_code=child.code,
                target_child_age=raw_age, target_child_age_months=age, age_status='valid',
                target_selection='legacy_PID_CHI_code' if chi else 'legacy_PID_unique_target')


def legacy_rejection(record, candidate):
    header = candidate.get('legacy_header', {})
    pid = candidate.get('current_pid')
    checks = [
        (record['collection'] == header.get('collection', 'Eng-NA'), 'collection'),
        (candidate.get('transcript') == record['transcript'], 'transcript'),
        (bool(pid) and pid == candidate.get('legacy_pid') == header.get('pid'), 'PID'),
        (candidate.get('exact_pid_matches') == 1, 'nonunique_PID'),
        (bool(record.get('sha256')) and record['sha256'] == candidate.get('current_chat_sha256'), 'source_hash'),
        (not record.get('pid') or record['pid'] == pid, 'current_PID'),
        (yes(candidate.get('same_speaker_sequence')), 'speaker_sequence'),
        (yes(candidate.get('roles_preserved_for_all_current_codes')), 'speaker_roles'),
        (candidate.get('asr_comment') is False, 'ASR'),
        (candidate.get('anonymous_participants_only') is False, 'anonymous_speakers'),
        ((number(candidate.get('underscore_normalized_position_agreement')) or 0) >= .9, 'body_agreement'),
        (bool(header.get('chat_sha256')), 'legacy_hash'),
    ]
    return next(('rejected_' + reason for valid, reason in checks if not valid), '')


def decide(record, candidate, link):
    result = copy.deepcopy(record)
    for key in TARGET_FIELDS:
        result['chat_original_' + key] = record.get(key)
    before = number(record.get('target_child_age_months'))
    result['age_source'] = 'current_chat' if before is not None else 'missing'
    result['metadata_source'] = 'current_chat:' + record.get('sha256', '')
    decision = dict(collection=record['collection'], transcript=record.get('transcript', ''),
                    legacy_status='not_available', original_status='not_available',
                    chat_age_months=before, legacy_age_months=None, original_age_months=None)
    evidence = {}
    asr = any('asr' in comment.casefold() for comment in record.get('annotation_comments', []))
    if candidate and before is None:
        reason = legacy_rejection(record, candidate)
        if asr:
            reason = 'rejected_current_ASR'
        if not reason:
            try:
                target = legacy_target(candidate['legacy_header'])
            except (ValueError, KeyError, TypeError) as exc:
                reason = 'rejected_legacy_header: ' + str(exc)
        if reason:
            decision['legacy_status'] = reason
        else:
            result.update(target)
            result['chat_original_target_participants'] = record.get('target_participants', [])
            result['target_participants'] = [dict(code=target['target_child_code'],
                                                   name=target['target_child'], age=target['target_child_age'])]
            result['age_source'] = 'legacy_chat_verified_pid'
            result['metadata_source'] = 'legacy_chat:' + candidate['legacy_pid']
            decision['legacy_status'] = 'accepted'
            decision['legacy_age_months'] = target['target_child_age_months']
            evidence.update(legacy_pid=candidate['legacy_pid'],
                            legacy_member=candidate['legacy_header'].get('archive_member'),
                            legacy_chat_sha256=candidate['legacy_header']['chat_sha256'],
                            current_chat_sha256=candidate['current_chat_sha256'],
                            text_agreement=candidate['underscore_normalized_position_agreement'],
                            legacy_target=target)
    elif candidate:
        decision['legacy_status'] = 'not_needed_valid_chat_age'
    if link:
        original = number(link.get('original_age_months'))
        corroborating = number(result.get('target_child_age_months'))
        decision['original_age_months'] = original
        compatible = name_check(result.get('target_child'), link.get('original_target_child'),
                                result.get('target_child_code'))
        decision['original_name_check'] = compatible
        delta = original - corroborating if original is not None and corroborating is not None else None
        decision['original_age_delta_months'] = delta
        votes = number(link.get('unique_matching_utterances')) or 0
        runner = number(link.get('runner_up_matches')) or 0
        total = number(link.get('total_matching_utterances')) or 0
        checks = [
            (link.get('status') == 'high_confidence_text_match' and votes >= 5
             and votes >= 5 * runner and total > 0 and votes / total >= .8, 'text_identity'),
            (not yes(link.get('original_id_used_by_multiple_current_transcripts')), 'duplicate_original_ID'),
            (not asr, 'current_ASR'),
            (corroborating is not None, 'uncorroborated_age'),
            (original is not None, 'missing_original_age'),
            (delta is not None and abs(delta) <= .1 + 1e-12, 'age_conflict'),
            (compatible != 'conflicting', 'target_name_conflict'),
        ]
        reason = next(('rejected_' + why for ok, why in checks if not ok), '')
        if reason:
            decision['original_status'] = reason
        else:
            result['target_child_age_months'] = original
            result['age_source'] = 'original_childes_db_corroborated'
            result['metadata_source'] += ';original_childes_db:' + str(link.get('original_transcript_id', ''))
            decision['original_status'] = 'accepted'
            evidence.update(original_transcript_id=link.get('original_transcript_id'),
                            original_target_child=link.get('original_target_child'),
                            original_age_months=original, corroborating_age_months=corroborating,
                            original_age_delta_months=delta, name_check=compatible,
                            original_unique_text_matches=votes)
    result['age_evidence'] = evidence
    decision['final_age_months'] = number(result.get('target_child_age_months'))
    decision['age_source'] = result['age_source']
    return result, decision


def enrich(extraction, legacy_candidates, output, original_links=None, collection=None):
    extraction, legacy_candidates, output = map(Path, (extraction, legacy_candidates, output))
    if output.exists():
        raise FileExistsError(f'Output already exists: {output}')
    if collection not in (None, 'Eng-NA'):
        raise ValueError('Only the approved --collection Eng-NA scope is supported')
    csv.field_size_limit(sys.maxsize)
    source_meta = json.loads((extraction / 'metadata.json').read_text())
    if 'age_enrichment' in source_meta:
        raise ValueError('Input is already age-enriched; use the original extraction')
    legacy = json.loads(legacy_candidates.read_text())
    candidates = legacy.get('transcripts', {})
    links = {}
    links_path = None
    link_provenance = None
    if original_links:
        links_path = Path(original_links) / 'transcript_links.csv'
        provenance_path = Path(original_links) / 'metadata.json'
        link_provenance = json.loads(provenance_path.read_text())
        if link_provenance.get('transcript_links_sha256') != digest(links_path):
            raise ValueError('Original transcript link CSV does not match its provenance hash')
        original_source = Path(link_provenance['original'])
        if link_provenance.get('original_sha256') != digest(original_source):
            raise ValueError('Original utterance CSV does not match its provenance hash')
        link_provenance = dict(link_provenance, metadata_path=str(provenance_path.resolve()),
                               metadata_sha256=digest(provenance_path))
        with links_path.open(newline='') as stream:
            for row in csv.DictReader(stream):
                key = (row['collection'], row['transcript'])
                if key in links:
                    raise ValueError(f'Duplicate original link transcript: {key}')
                links[key] = row
        original_ids = Counter(row.get('original_transcript_id') for row in links.values()
                               if row.get('status') == 'high_confidence_text_match' and row.get('original_transcript_id'))
        for row in links.values():
            if original_ids[row.get('original_transcript_id')] > 1:
                row['original_id_used_by_multiple_current_transcripts'] = True
    metadata = copy.deepcopy(source_meta)
    records, file_map, decisions = [], {}, []
    counts = Counter()
    change_counts = Counter({key: 0 for key in (
        'files_recovered', 'utterances_recovered', 'pairs_recovered', 'cds_utterances_recovered',
        'files_shifted_age_bin', 'utterances_shifted_age_bin', 'pairs_shifted_age_bin',
        'cds_utterances_shifted_age_bin', 'files_adjusted', 'utterances_adjusted', 'pairs_adjusted')})
    source_counts = {kind: Counter() for kind in ('files', 'utterances', 'pairs')}
    transitions = Counter()
    for record in metadata['files']:
        if collection and record['collection'] != collection:
            continue
        counts['n_files'] += 1
        if record.get('strict_validation_error'):
            counts['n_files_relaxed_validation'] += 1
        if record.get('status') == 'parse_error':
            counts['n_files_parse_error'] += 1
            records.append(record)
            continue
        key = (record['collection'], record['transcript'])
        if key in file_map:
            raise ValueError(f'Duplicate transcript metadata: {key}')
        candidate = candidates.get(record['transcript']) if record['collection'] == 'Eng-NA' else None
        updated, decision = decide(record, candidate, links.get(key))
        records.append(updated)
        decisions.append(decision)
        file_map[key] = updated
        before = number(record.get('target_child_age_months'))
        after = number(updated.get('target_child_age_months'))
        counts['n_files_missing_age'] += after is None
        source_counts['files'][updated['age_source']] += 1
        change_counts['files_recovered'] += before is None and after is not None
        change_counts['files_adjusted'] += before is not None and after != before
        change_counts['files_shifted_age_bin'] += before is not None and age_bin(before) != age_bin(after)
        transitions[(age_bin(before), age_bin(after))] += 1
    if not records:
        raise ValueError('No files remain after collection selection')
    roles = Counter()
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.recover-', dir=output.parent))
    try:
        for filename in CSV_NAMES:
            utterances = filename == CSV_NAMES[0]
            kind = 'utterances' if utterances else 'pairs'
            with (extraction / filename).open(newline='') as inp, (stage / filename).open('w', newline='') as out:
                reader = csv.DictReader(inp)
                if not reader.fieldnames:
                    raise ValueError(f'CSV has no header: {filename}')
                writer = csv.DictWriter(out, fieldnames=list(reader.fieldnames) + [k for k in EXTRA_FIELDS if k not in reader.fieldnames])
                writer.writeheader()
                for row in reader:
                    if collection and row['collection'] != collection:
                        continue
                    key = (row['collection'], row['transcript'])
                    if key not in file_map:
                        raise ValueError(f'CSV transcript has no parsed file metadata: {key}')
                    record = file_map[key]
                    before = number(row.get('target_child_age_months'))
                    after = number(record.get('target_child_age_months'))
                    if before != number(record.get('chat_original_target_child_age_months')):
                        raise ValueError(f'CSV transcript age disagrees with file metadata: {key}')
                    for field in TARGET_FIELDS:
                        row['chat_original_' + field] = row.get(field, '')
                        row[field] = record.get(field, '')
                    row['age_source'] = record['age_source']
                    row['metadata_source'] = record['metadata_source']
                    writer.writerow(row)
                    counts['n_' + kind] += 1
                    source_counts[kind][record['age_source']] += 1
                    recovered = before is None and after is not None
                    shifted = before is not None and age_bin(before) != age_bin(after)
                    change_counts[kind + '_recovered'] += recovered
                    change_counts[kind + '_shifted_age_bin'] += shifted
                    change_counts[kind + '_adjusted'] += before is not None and after != before
                    if utterances:
                        included = yes(row.get('included_in_cds'))
                        roles[(row.get('speaker_role', ''), included)] += 1
                        counts['n_cds_utterances'] += included
                        counts['n_cds_utterances_missing_age'] += included and after is None
                        status = row.get('annotation_status')
                        counts['n_annotated_utterances'] += status == 'annotated'
                        counts['n_cds_utterances_missing_tiers'] += included and status != 'annotated'
                        counts['n_utterances_misaligned'] += status == 'misaligned_tokens'
                        counts['n_cds_utterances_misaligned'] += included and status == 'misaligned_tokens'
                        counts['n_utterances_invalid_dependencies'] += status == 'invalid_dependencies'
                        counts['n_cds_utterances_invalid_dependencies'] += included and status == 'invalid_dependencies'
                        change_counts['cds_utterances_recovered'] += included and recovered
                        change_counts['cds_utterances_shifted_age_bin'] += included and shifted
                    else:
                        counts['n_pairs_missing_age'] += after is None
        with (stage / 'speaker_roles.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['speaker_role', 'utterance_count', 'included_in_cds'])
            writer.writeheader()
            writer.writerows(dict(speaker_role=role, utterance_count=n, included_in_cds=include)
                             for (role, include), n in sorted(roles.items()))
        metadata['files'] = records
        metadata['source_extraction_counts'] = source_meta.get('counts', {})
        metadata['source_extraction_scope'] = source_meta['scope']
        if collection:
            metadata['scope'] = 'thesis_eng_na'
            metadata['collection'] = collection
            metadata['sources'] = [s for s in source_meta['sources'] if s['collection'] == collection]
            metadata['source_extraction_diagnostics'] = {
                'scope': source_meta['scope'],
                **{key: metadata.pop(key) for key in ('diagnostics', 'dependency_counts', 'pos_counts') if key in metadata},
                'note': 'Token-level annotation totals cannot be recomputed from pair/utterance CSVs and refer to the source extraction.'}
            selected = {(r['archive_id'], r['member']) for r in records}
            metadata['parse_errors'] = [e for e in source_meta.get('parse_errors', [])
                                        if (e.get('archive_id'), e.get('member')) in selected]
            metadata['complete_parse'] = not metadata['parse_errors']
        else:
            if 'n_changeable_headers' in source_meta.get('counts', {}):
                counts['n_changeable_headers'] = source_meta['counts']['n_changeable_headers']
        metadata['counts'] = dict(counts)
        metadata['age_note'] = ('Original extracted CHAT fields are preserved under chat_original_*. '
                                'Verified historical PID ages use PyLangAcq header ages. Original childes-db ages '
                                'are used only with high-confidence unique text identity, independent CHAT/legacy '
                                'age agreement within 0.1 months, and no known target-name conflict.')
        metadata['age_enrichment'] = dict(
            created_at=datetime.now(timezone.utc).isoformat(), collection_filter=collection,
            input_extraction=str(extraction.resolve()),
            legacy_candidates=str(legacy_candidates.resolve()), legacy_candidates_sha256=digest(legacy_candidates),
            legacy_source_url=legacy.get('summary', {}).get('source_url'),
            original_links=str(links_path.resolve()) if links_path else None,
            original_links_sha256=digest(links_path) if links_path else None,
            original_link_provenance=link_provenance,
            counts=dict(change_counts), age_source_counts={k: dict(v) for k, v in source_counts.items()},
            file_age_bin_transitions=[dict(before=a, after=b, n_files=n) for (a, b), n in sorted(transitions.items())],
            decisions=decisions,
            legacy_rule='Unique exact full PID + current source SHA256 + preserved roles and speaker sequence + no ASR/anonymous speakers + >=90% normalized body agreement',
            original_rule='Unique high-confidence text link, >=5 distinct matches, >=5x runner-up and >=80% votes; corroborating valid CHAT or verified legacy age within 0.1 months; no known target-name conflict; no ASR',
            name_note='Blank, CHI, NA, unknown, and target-code placeholders are uninformative, not evidence of a different child.')
        (stage / 'metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
        (stage / 'age_enrichment_report.json').write_text(json.dumps(metadata['age_enrichment'], indent=2) + '\n')
        stage.rename(output)
        return metadata
    except BaseException:
        shutil.rmtree(stage)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extraction', required=True, type=Path)
    parser.add_argument('--legacy-candidates', required=True, type=Path)
    parser.add_argument('--original-links', type=Path)
    parser.add_argument('--collection', choices=['Eng-NA'])
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    metadata = enrich(args.extraction, args.legacy_candidates, args.output, args.original_links, args.collection)
    print(json.dumps(metadata['age_enrichment']['counts'], indent=2))


if __name__ == '__main__':
    main()
