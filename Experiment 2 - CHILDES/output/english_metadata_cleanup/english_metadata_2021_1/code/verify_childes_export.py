"""Audit every exported row against saved metadata and independent SQL counts."""

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path


def read_rows(path):
    with Path(path).open(newline='') as stream:
        return list(csv.DictReader(stream))


def verify_export(root):
    root = Path(root)
    speakers = {row['id']: row for row in read_rows(root / 'audit/selected_speakers.csv')}
    transcripts = {row['transcript_id']: row for row in read_rows(root / 'audit/transcripts.csv')}
    expected = {(row['collection_name'], row['corpus_name'], row['corpus_id']): int(row['n_utterances'])
                for row in read_rows(root / 'audit/independent_selection_counts.csv')}
    versions = {row['db_version'] for row in read_rows(root / 'audit/selection_policy.csv')}
    observed, collections, violations = Counter(), Counter(), Counter()
    previous_key = None
    ids = set()
    count = 0
    source = root / 'data/childes_utterances.csv'
    with source.open(newline='') as stream:
        for row in csv.DictReader(stream):
            count += 1
            observed[(row['collection_name'], row['corpus_name'], row['corpus_id'])] += 1
            collections[row['collection_name']] += 1
            for field in ['language', 'participant_language', 'transcript_language']:
                if row[field] != 'eng':
                    violations[field] += 1
            if row['collection_name'] not in {'Eng-NA', 'Eng-UK'}:
                violations['collection'] += 1
            if row['db_version'] not in versions:
                violations['db_version'] += 1
            speaker = speakers.get(row['speaker_id'], {})
            transcript = transcripts.get(row['transcript_id'], {})
            for label, metadata in [('speaker', speaker), ('transcript', transcript)]:
                if metadata.get('language') != 'eng' or any(
                        metadata.get(field) != row[field] for field in ['corpus_id', 'collection_id', 'collection_name']):
                    violations[label + '_metadata'] += 1
            key = (int(row['transcript_id']), int(row['utterance_order']))
            if previous_key is not None and key <= previous_key:
                violations['duplicate_or_unordered_key'] += 1
            previous_key = key
            if row['utterance_id'] in ids:
                violations['duplicate_utterance_id'] += 1
            ids.add(row['utterance_id'])
    counts_match = dict(observed) == expected
    digest = hashlib.sha256()
    with source.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    report = {
        'status': 'passed' if count and not violations and counts_match else 'failed',
        'n_utterances': count, 'independent_server_count': sum(expected.values()),
        'all_corpus_counts_match': counts_match, 'collection_counts': dict(collections),
        'metadata_violations': dict(violations), 'csv_sha256': digest.hexdigest(),
        'note': 'Validates metadata selection; does not classify the language of speech.',
    }
    (root / 'audit/export_verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    if report['status'] != 'passed':
        raise ValueError('Export verification failed')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_directory', type=Path)
    verify_export(parser.parse_args().run_directory)
