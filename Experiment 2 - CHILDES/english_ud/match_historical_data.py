"""Intersect independently corroborated historical/current transcripts and utterances.

Candidate transcript identity uses the existing long-utterance linker; actual
utterance matching preserves lexical boundaries, speaker code, role and counts.
"""
import argparse
from collections import Counter, defaultdict, deque
from contextlib import ExitStack
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unicodedata
import zipfile

from extract_pairs import PAIR_FIELDS, UTTERANCE_FIELDS, CDS_ROLES
from link_original_transcripts import corpus_key
from recover_age_metadata import name_check, number, yes


EXTRA = ['original_transcript_id', 'original_utterance_order', 'original_speaker_id',
         'original_text', 'match_method']
WORDS = re.compile(r"[^\W_]+(?:['\-][^\W_]+)*", re.UNICODE)
# The original pandas loader drops these literal CSV NA strings as well as blanks.
NA_TEXT = frozenset(['', '#N/A', '#N/A N/A', '#NA', '-1.#IND', '-1.#QNAN',
                     '-NaN', '-nan', '1.#IND', '1.#QNAN', '<NA>', 'N/A',
                     'NA', 'NULL', 'NaN', 'None', 'n/a', 'nan', 'null'])


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def canonical_words(text):
    text = unicodedata.normalize('NFC', text).replace('’', "'").replace('‘', "'").casefold()
    text = re.sub(r"(?<=\w)\s+('(?:s|m|d|ll|re|ve)\b)", r'\1', text)
    text = re.sub(r"\b(\w+)\s+n['’]t\b", r"\1n't", text)
    return tuple(WORDS.findall(text))


def eligibility_reason(record, link):
    if record.get('status') != 'parsed':
        return 'unparsed'
    if link.get('status') != 'high_confidence_text_match':
        return 'weak_text_identity'
    votes = number(link.get('unique_matching_utterances')) or 0
    runner = number(link.get('runner_up_matches')) or 0
    total = number(link.get('total_matching_utterances')) or 0
    if votes < 5 or votes < 5 * runner or not total or votes / total < .8:
        return 'weak_text_identity'
    if yes(link.get('original_id_used_by_multiple_current_transcripts')):
        return 'duplicate_original_id'
    age, old_age = number(record.get('target_child_age_months')), number(link.get('original_age_months'))
    if age is None:
        return 'missing_chat_age'
    if old_age is None:
        return 'missing_original_age'
    if old_age > 96:
        return 'outside_age_range'
    if abs(age - old_age) > .1 + 1e-12:
        return 'age_conflict'
    if name_check(record.get('target_child'), link.get('original_target_child'), record.get('target_child_code')) == 'conflicting':
        return 'target_name_conflict'
    if any('asr' in c.casefold() for c in record.get('annotation_comments', [])):
        return 'asr'
    return ''


def utterance_key(speaker, role, text):
    return speaker, role, canonical_words(text)


def match_occurrences(original, current):
    index = defaultdict(deque)
    for row in original:
        key = utterance_key(row['speaker_code'], row['speaker_role'], row['full_utterance'])
        if key[2]:
            index[key].append(row)
    for row in current:
        key = utterance_key(row['speaker'], row['speaker_role'], row['text'])
        if index.get(key):
            yield index[key].popleft(), row


def age_labels(age):
    if age is None or age > 96:
        return []
    return ['overall'] + ([f'{int(age // 12) * 12}-{int(age // 12) * 12 + 12}mo'] if age < 96 else [])


def _bump(coverage, age, field, count=1):
    for label in age_labels(age):
        coverage[label][field] += count


def _write_csv(path, rows, fields):
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


def _raw_asr(record, source, archives):
    path = Path(source['path'])
    if path.suffix.lower() == '.zip':
        if str(path) not in archives:
            archives[str(path)] = zipfile.ZipFile(path)
        raw = archives[str(path)].read(record['member'])
    else:
        raw = (path / record['member'] if path.is_dir() else path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != record['sha256']:
        raise ValueError(f"Source member hash mismatch: {record['transcript']}")
    # Check all comment tiers, including continuations, from authoritative bytes.
    text = re.sub(r'\n[ \t]+', ' ', raw.decode('utf-8-sig'))
    return any('asr' in line.casefold() for line in text.splitlines() if line.startswith('@Comment:'))


def build_matched(original, extractions, link_dirs, output):
    original, output = Path(original), Path(output)
    extractions, link_dirs = list(map(Path, extractions)), list(map(Path, link_dirs))
    if output.exists():
        raise FileExistsError(output)
    if len(extractions) != len(link_dirs) or not extractions:
        raise ValueError('Each extraction needs its corresponding transcript links')
    csv.field_size_limit(sys.maxsize)
    original_hash = digest(original)
    candidates, rejections, provenance, sources = [], [], [], {}
    corpus_priority = {}
    for source_index, (extraction, links) in enumerate(zip(extractions, link_dirs)):
        meta_path, link_path = extraction / 'metadata.json', links / 'transcript_links.csv'
        meta = json.loads(meta_path.read_text()); link_meta = json.loads((links / 'metadata.json').read_text())
        if link_meta['original_sha256'] != original_hash or link_meta['transcript_links_sha256'] != digest(link_path):
            raise ValueError('Original source/link hash mismatch')
        with link_path.open() as f:
            link_rows = list(csv.DictReader(f))
        by_file = {(r['collection'], r['transcript']): r for r in link_rows}
        if len(by_file) != len(link_rows):
            raise ValueError('Duplicate transcript identities in link table')
        for source in meta['sources']:
            key = (source_index, source['archive_id'])
            sources[key] = source
        provenance.append(dict(extraction=str(extraction.resolve()), metadata_sha256=digest(meta_path),
                               links=str(link_path.resolve()), links_sha256=digest(link_path)))
        for file in meta['files']:
            if file.get('corpus'):
                corpus_priority[(file['collection'], file['corpus'])] = source_index
            link = by_file.get((file['collection'], file.get('transcript')), {})
            reason = eligibility_reason(file, link)
            item = dict(file=file, link=link, source_index=source_index)
            if reason:
                rejections.append(dict(collection=file['collection'], transcript=file.get('transcript', file['member']), reason=reason))
            else:
                candidates.append(item)
    preferred = []
    for item in candidates:
        file = item['file']
        if item['source_index'] != corpus_priority[(file['collection'], file['corpus'])]:
            rejections.append(dict(collection=file['collection'], transcript=file['transcript'], reason='superseded_corpus_source'))
        else:
            preferred.append(item)
    candidates = preferred
    old_counts = Counter(c['link']['original_transcript_id'] for c in candidates)
    selected, archives = {}, {}
    try:
        for item in candidates:
            f, l, si = item['file'], item['link'], item['source_index']
            reason = ('duplicate_across_sources' if old_counts[l['original_transcript_id']] > 1 else
                      'raw_asr' if _raw_asr(f, sources[(si, f['archive_id'])], archives) else '')
            if reason:
                rejections.append(dict(collection=f['collection'], transcript=f['transcript'], reason=reason))
                continue
            key = (f['collection'], f['transcript'])
            if key in selected:
                raise ValueError(f'Duplicate selected transcript: {key}')
            selected[key] = item
    finally:
        for archive in archives.values():
            archive.close()
    if not selected:
        raise ValueError('No eligible independently corroborated transcripts')
    print(f'{len(selected):,} eligible transcripts; indexing original utterances', flush=True)
    by_original = {v['link']['original_transcript_id']: (k, v) for k, v in selected.items()}
    indexes = defaultdict(lambda: defaultdict(deque))
    stats = {k: Counter() for k in selected}
    coverage = defaultdict(Counter)
    for key, item in selected.items():
        _bump(coverage, number(item['link']['original_age_months']), 'cohort_transcripts')
    with original.open() as f:
        for n, row in enumerate(csv.DictReader(f), 1):
            age = number(row['target_child_age'])
            usable = row['full_utterance'] not in NA_TEXT and age is not None and age <= 96
            if usable:
                _bump(coverage, age, 'all_original_utterances')
            if row['transcript_id'] not in by_original:
                continue
            identity, item = by_original[row['transcript_id']]
            if number(row['target_child_age']) != number(item['link']['original_age_months']) or corpus_key(row['corpus_name']) != corpus_key(item['file']['corpus']):
                raise ValueError(f'Historical transcript metadata conflict: {row["transcript_id"]}')
            stats[identity]['original_rows'] += 1
            if not usable:
                continue
            stats[identity]['original_usable_utterances'] += 1
            _bump(coverage, age, 'cohort_original_utterances')
            key = utterance_key(row['speaker_code'], row['speaker_role'], row['full_utterance'])
            if key[2]:
                # Only fields needed for correspondence are held in memory.
                indexes[identity][key].append((row['utterance_order'], row['speaker_id'], row['full_utterance']))
    if any(not s['original_rows'] for s in stats.values()):
        raise ValueError('A selected transcript is absent from the original CSV')
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.' + output.name + '-', dir=output.parent))
    matched = {}
    counts = Counter()
    try:
        with (stage / 'english_ud_utterances.csv').open('w', newline='') as stream, (stage / 'cohort_ud_utterances.csv').open('w', newline='') as cohort_stream:
            fields = UTTERANCE_FIELDS + EXTRA
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore'); writer.writeheader()
            cw = csv.DictWriter(cohort_stream, fieldnames=UTTERANCE_FIELDS + ['original_transcript_id'], extrasaction='ignore'); cw.writeheader()
            for si, extraction in enumerate(extractions):
                with (extraction / 'english_ud_utterances.csv').open() as f:
                    for row in csv.DictReader(f):
                        identity = (row['collection'], row['transcript'])
                        item = selected.get(identity)
                        if not item or item['source_index'] != si or not yes(row['included_in_cds']):
                            continue
                        age_string = item['link']['original_age_months']; age = number(age_string)
                        row['target_child_age_months'] = age_string
                        row['original_transcript_id'] = item['link']['original_transcript_id']
                        stats[identity]['current_cds_utterances'] += 1
                        _bump(coverage, age, 'cohort_current_cds_utterances')
                        cw.writerow(row)
                        if row['annotation_status'] != 'annotated' or not row['text'].strip():
                            stats[identity]['current_unusable_annotations'] += 1
                            continue
                        stats[identity]['current_annotated_utterances'] += 1
                        _bump(coverage, age, 'cohort_current_annotated_utterances')
                        key = utterance_key(row['speaker'], row['speaker_role'], row['text'])
                        queue = indexes[identity].get(key)
                        if not queue:
                            stats[identity]['unmatched_current_utterances'] += 1
                            continue
                        order, speaker_id, original_text = queue.popleft()
                        if not queue:
                            del indexes[identity][key]
                        row.update(original_utterance_order=order, original_speaker_id=speaker_id,
                                   original_text=original_text,
                                   match_method='same_speaker_role_word_sequence_occurrence')
                        uid = identity + (row['utterance_id'],)
                        if uid in matched:
                            raise ValueError(f'Duplicate current utterance ID: {uid}')
                        matched[uid] = (age_string, row['original_transcript_id'], order, speaker_id, original_text)
                        writer.writerow(row)
                        counts['matched_utterances'] += 1
                        counts['expected_matched_ud_pairs'] += int(row['n_pairs'])
                        stats[identity]['matched_utterances'] += 1
                        _bump(coverage, age, 'matched_utterances')
                        if counts['matched_utterances'] % 250000 == 0:
                            print(f"Matched {counts['matched_utterances']:,} utterances", flush=True)
        del indexes
        with (stage / 'english_ud_subject_verb_pairs.csv').open('w', newline='') as stream, (stage / 'cohort_ud_pairs.csv').open('w', newline='') as cohort_stream:
            writer = csv.DictWriter(stream, fieldnames=PAIR_FIELDS + EXTRA, extrasaction='ignore'); writer.writeheader()
            cw = csv.DictWriter(cohort_stream, fieldnames=PAIR_FIELDS + ['original_transcript_id'], extrasaction='ignore'); cw.writeheader()
            for si, extraction in enumerate(extractions):
                with (extraction / 'english_ud_subject_verb_pairs.csv').open() as f:
                    for row in csv.DictReader(f):
                        identity = (row['collection'], row['transcript'])
                        item = selected.get(identity)
                        if not item or item['source_index'] != si:
                            continue
                        row['target_child_age_months'] = item['link']['original_age_months']
                        row['original_transcript_id'] = item['link']['original_transcript_id']
                        cw.writerow(row); counts['cohort_ud_pairs'] += 1
                        entry = matched.get(identity + (row['utterance_id'],))
                        if entry is None:
                            continue
                        age, tid, order, speaker_id, text = entry
                        row.update(target_child_age_months=age, original_transcript_id=tid,
                                   original_utterance_order=order, original_speaker_id=speaker_id,
                                   original_text=text, match_method='same_speaker_role_word_sequence_occurrence')
                        writer.writerow(row); counts['matched_ud_pairs'] += 1
                        _bump(coverage, number(age), 'matched_ud_pairs')
        if counts['matched_ud_pairs'] != counts['expected_matched_ud_pairs']:
            raise ValueError('Matched pair count differs from utterance annotations')
        if not counts['matched_utterances']:
            raise ValueError('No utterances matched')
        counts['n_cds_utterances'] = counts['matched_utterances']
        cohort_rows, file_records = [], []
        for identity, item in selected.items():
            f, link = item['file'], item['link']
            age = number(link['original_age_months'])
            if stats[identity]['matched_utterances']:
                _bump(coverage, age, 'transcripts_with_matched_utterances')
            cohort_rows.append(dict(collection=identity[0], corpus=f['corpus'], transcript=identity[1],
                                    original_transcript_id=link['original_transcript_id'], age_months=age,
                                    source_extraction=str(extractions[item['source_index']].resolve()), **stats[identity]))
            file_records.append(dict(f, target_child_age_months=age,
                                     original_transcript_id=link['original_transcript_id']))
        stat_fields = sorted({k for r in cohort_rows for k in r})
        _write_csv(stage / 'transcript_cohort.csv', cohort_rows, stat_fields)
        _write_csv(stage / 'transcript_rejections.csv', rejections, ['collection', 'transcript', 'reason'])
        labels = ['overall'] + [f'{i}-{i+12}mo' for i in range(0, 96, 12)]
        coverage_rows = [dict(age_group=l, **coverage[l]) for l in labels]
        _write_csv(stage / 'coverage_by_age.csv', coverage_rows, ['age_group'] + sorted({k for r in coverage_rows for k in r} - {'age_group'}))
        meta = dict(scope='matched_repository_english', collection=';'.join(sorted({k[0] for k in selected})),
                    annotation_scheme='Universal Dependencies', morphology_tier='%mor', grammar_tier='%gra',
                    created_at=datetime.now(timezone.utc).isoformat(), sources=list(sources.values()), files=file_records,
                    counts=dict(counts), n_cohort_transcripts=len(selected), rejection_counts=dict(Counter(r['reason'] for r in rejections)),
                    matching_provenance=dict(original=str(original.resolve()), original_sha256=original_hash,
                        inputs=provenance, raw_source_members_verified=True,
                        source_precedence='Later --extraction arguments replace earlier versions of the same collection/corpus. Ambiguous original IDs remaining within selected sources are excluded.',
                        transcript_rule='Unique strong multi-utterance text link; independent CHAT/verified legacy age within 0.1 months; compatible known target name; no raw ASR; original age <=96.',
                        utterance_rule='Same speaker code and role; identical casefolded lexical token sequence, allowing punctuation and known clitic-spacing differences; consume each historical occurrence at most once.',
                        repeated_utterances='Occurrence order within speaker/role/text key; no Cartesian join. This does not assert globally monotonic sequence alignment.',
                        parser_texts='UD reads original annotations; spaCy must parse text for same-surface control, and original_text separately for historical-gloss control.',
                        selection_note='This is a verified intersection subset, not the complete original corpus. Coverage is reported by age.'))
        (stage / 'metadata.json').write_text(json.dumps(meta, indent=2) + '\n')
        artifact_manifest = {p.name: dict(bytes=p.stat().st_size, sha256=digest(p))
                             for p in sorted(stage.iterdir()) if p.is_file()}
        (stage / 'artifact_manifest.json').write_text(json.dumps(artifact_manifest, indent=2) + '\n')
        stage.rename(output)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    print(json.dumps(dict(counts), indent=2), flush=True)
    return meta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original', type=Path, required=True)
    parser.add_argument('--extraction', type=Path, action='append', required=True)
    parser.add_argument('--links', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build_matched(args.original, args.extraction, args.links, args.output)


if __name__ == '__main__':
    main()
