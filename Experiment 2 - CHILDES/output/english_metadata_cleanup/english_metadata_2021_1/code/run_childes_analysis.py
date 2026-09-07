"""Run both original analyses after one ordered, parallel spaCy extraction."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

import pandas as pd

CODE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CODE_DIR / 'english_ud'))
from parse_sharded_spacy import parse_sharded
import analyze_complete_dataset_96mos as complete
import analyze_age_groups_96mos as ages


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def prepare_parser_input(source, output):
    """Use the original loader, retaining CSV row indices through its filters."""
    source, output = Path(source).resolve(), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    frame = complete.load_data(str(source), max_age=96)
    prepared = pd.DataFrame({
        'csv_row_index': frame.index,
        'included_in_cds': True,
        'collection': frame['collection_name'].to_numpy(),
        'corpus': frame['corpus_name'].to_numpy(),
        'transcript': frame['transcript_id'].to_numpy(),
        'utterance_id': frame['utterance_id'].to_numpy(),
        'target_child_age_months': frame['target_child_age'].to_numpy(),
        'text': frame['gloss'].to_numpy(),
    })
    prepared.to_csv(output / 'utterances.csv', index=False)
    metadata = {
        'scope': 'english_metadata',
        'db_versions': sorted(frame['db_version'].astype(str).unique().tolist()) if 'db_version' in frame else [],
        'collection': sorted(frame['collection_name'].unique().tolist()),
        'counts': {'n_cds_utterances': len(frame)},
        'selection_provenance': {
            'source_csv': str(source), 'source_sha256': sha256(source),
            'age_and_text_filter': 'unchanged analyze_complete_dataset_96mos.load_data(max_age=96)',
            'row_identity': 'zero-based data row index in source CSV; preserved through filtering',
        },
    }
    (output / 'metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    return frame


class CachedPairs:
    """Supply the original analyses with exactly the pairs for their input rows."""

    def __init__(self, path, valid_indices):
        self.pairs = pd.read_csv(path, usecols=['csv_row_index', 'subject_lemma', 'verb_lemma'],
                                 keep_default_na=False)
        self.valid_indices = pd.Index(valid_indices)
        if not self.pairs['csv_row_index'].isin(self.valid_indices).all():
            raise ValueError('Parsed pairs refer to utterances outside the analysis population')
        if not self.pairs['csv_row_index'].is_monotonic_increasing:
            raise ValueError('Parsed pairs are not in original utterance order')

    def __call__(self, utterances, nlp):
        if not utterances.index.isin(self.valid_indices).all():
            raise ValueError('Analysis requested utterances outside the parsed population')
        selected = self.pairs[self.pairs['csv_row_index'].isin(utterances.index)]
        print(f'Using {len(selected):,} freshly parsed pairs for {len(utterances):,} utterances', flush=True)
        return list(selected[['subject_lemma', 'verb_lemma']].itertuples(index=False, name=None))


def run_analysis(run_directory, shards=16):
    root = Path(run_directory).resolve()
    if (root / 'audit/extraction_status.txt').read_text().strip() != 'complete':
        raise ValueError('Extraction has not completed')
    source = root / 'data/childes_utterances.csv'
    verification = json.loads((root / 'audit/export_verification.json').read_text())
    if verification.get('status') != 'passed' or verification.get('csv_sha256') != sha256(source):
        raise ValueError('Export verification failed or refers to a different CSV')
    if (root / 'output').exists():
        raise FileExistsError('Analysis output already exists')
    # Retain every executable source needed for this run beside its data.
    sources = ['run_childes_analysis.py', 'analyze_complete_dataset_96mos.py',
               'analyze_age_groups_96mos.py', 'english_ud/parse_matched_spacy.py',
               'english_ud/parse_sharded_spacy.py']
    for name in sources:
        destination = root / 'code' / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if (CODE_DIR / name).resolve() != destination.resolve():
            shutil.copy2(CODE_DIR / name, destination)
    manifest = {'started_at': datetime.now(timezone.utc).isoformat(),
                'shards': shards, 'code_sha256': {name: sha256(CODE_DIR / name) for name in sources}}
    manifest_path = root / 'audit/analysis_run.json'
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    frame = prepare_parser_input(source, root / 'parser_input')
    parse_sharded(root / 'parser_input/utterances.csv', root / 'parser_input/metadata.json',
                  root / 'parsed', shards=shards)
    cached = CachedPairs(root / 'parsed/english_ud_subject_verb_pairs.csv', frame.index)
    del frame
    previous_directory = Path.cwd()
    original_complete, original_ages = complete.extract_subject_verb_pairs, ages.extract_subject_verb_pairs
    try:
        os.chdir(root)
        complete.extract_subject_verb_pairs = cached
        ages.extract_subject_verb_pairs = cached
        complete.main()
        ages.main()
    finally:
        complete.extract_subject_verb_pairs = original_complete
        ages.extract_subject_verb_pairs = original_ages
        os.chdir(previous_directory)
    manifest['completed_at'] = datetime.now(timezone.utc).isoformat()
    manifest['status'] = 'complete'
    manifest['source_sha256'] = sha256(source)
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_directory', type=Path)
    parser.add_argument('--shards', type=int, default=16)
    arguments = parser.parse_args()
    run_analysis(arguments.run_directory, arguments.shards)
