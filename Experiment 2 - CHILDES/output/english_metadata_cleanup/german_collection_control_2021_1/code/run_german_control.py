"""Hold the historical population/parser fixed and remove only German collection rows."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys

import pandas as pd

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--repository', type=Path, required=True)
parser.add_argument('--original-csv', type=Path, required=True)
parser.add_argument('--transcripts', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--shards', type=int, default=16)
args = parser.parse_args()
root = args.output.resolve()
root.mkdir(parents=True, exist_ok=True)
if (root / 'parser_input').exists() or (root / 'cohorts').exists():
    raise FileExistsError('Refusing to overwrite a control run')
code = root / 'code'
code.mkdir(exist_ok=True)
names = ['analyze_complete_dataset_96mos.py', 'analyze_age_groups_96mos.py',
         'run_childes_analysis.py', 'english_ud/parse_matched_spacy.py',
         'english_ud/parse_sharded_spacy.py']
for name in names:
    destination = code / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.repository / name, destination)
sys.path.insert(0, str(code))
from run_childes_analysis import CachedPairs, sha256
from parse_sharded_spacy import parse_sharded
import analyze_complete_dataset_96mos as complete
import analyze_age_groups_96mos as ages

audit = root / 'audit'
audit.mkdir(exist_ok=True)
manifest = {
    'started_at': datetime.now(timezone.utc).isoformat(),
    'database_release': '2021.1',
    'original_csv': str(args.original_csv.resolve()),
    'original_sha256': sha256(args.original_csv),
    'transcript_metadata': str(args.transcripts.resolve()),
    'transcript_metadata_sha256': sha256(args.transcripts),
    'code_sha256': {name: sha256(code / name) for name in names},
    'control_definition': "original analysis population with collection_name != 'German'; all other rows retained",
    'shards': args.shards,
}
(audit / 'run.json').write_text(json.dumps(manifest, indent=2) + '\n')
frame = complete.load_data(str(args.original_csv.resolve()), max_age=96)
transcripts = pd.read_csv(args.transcripts, usecols=['transcript_id', 'collection_name', 'language'], keep_default_na=False)
if transcripts.transcript_id.duplicated().any():
    raise ValueError('Duplicate transcript metadata IDs')
metadata = transcripts.set_index('transcript_id')
frame['collection_name'] = frame.transcript_id.map(metadata.collection_name)
frame['transcript_language'] = frame.transcript_id.map(metadata.language)
if frame.collection_name.isna().any() or frame.transcript_language.isna().any():
    raise ValueError('Original utterances lack transcript metadata')
frame['csv_row_index'] = frame.index
german = frame.collection_name.eq('German')
expected_raw = 4_739_189
if len(frame) != expected_raw:
    raise ValueError(f'Historical analysis population changed: {len(frame)} != {expected_raw}')

prepared = root / 'parser_input'
prepared.mkdir()
pd.DataFrame({
    'csv_row_index': frame.index, 'included_in_cds': True,
    'collection': frame.collection_name.to_numpy(), 'corpus': frame.corpus_name.to_numpy(),
    'transcript': frame.transcript_id.to_numpy(),
    'utterance_id': frame.index,  # Explicit synthetic identity: source CSV row index.
    'target_child_age_months': frame.target_child_age.to_numpy(),
    'text': frame.gloss.to_numpy(),
}).to_csv(prepared / 'utterances.csv', index=False)
input_metadata = {
    'scope': 'original_population_2021_1', 'collection': sorted(frame.collection_name.unique().tolist()),
    'counts': {'n_cds_utterances': len(frame)},
    'selection_provenance': {'source_sha256': manifest['original_sha256'],
                             'utterance_id_definition': 'zero-based source CSV data row index'},
}
(prepared / 'metadata.json').write_text(json.dumps(input_metadata, indent=2) + '\n')
frame[['csv_row_index', 'transcript_id', 'corpus_name', 'collection_name',
       'transcript_language', 'target_child_age']].to_csv(audit / 'cohort_membership.csv', index=False)
selection = {'original_utterances': len(frame), 'german_collection_utterances': int(german.sum()),
             'without_german_utterances': int((~german).sum()), 'removed_by_age': {}}
for lo, hi, label in ages.AGE_GROUPS:
    mask = frame.target_child_age.ge(lo) & frame.target_child_age.lt(hi)
    selection['removed_by_age'][label] = int((mask & german).sum())
(audit / 'selection.json').write_text(json.dumps(selection, indent=2) + '\n')
print(json.dumps(selection, indent=2), flush=True)

parse_sharded(prepared / 'utterances.csv', prepared / 'metadata.json', root / 'parsed', shards=args.shards)
cached = CachedPairs(root / 'parsed/english_ud_subject_verb_pairs.csv', frame.index)
original_complete_loader, original_age_loader = complete.load_data, ages.load_data
original_complete_extract, original_age_extract = complete.extract_subject_verb_pairs, ages.extract_subject_verb_pairs
previous = Path.cwd()
try:
    complete.extract_subject_verb_pairs = cached
    ages.extract_subject_verb_pairs = cached
    for name, mask in [('original', pd.Series(True, index=frame.index)), ('without_german_collection', ~german)]:
        selected = frame.loc[mask]
        def load_selected(data_path='data/childes_utterances.csv', max_age=96):
            if max_age != 96:
                raise ValueError('Control run fixes the original age limit at 96')
            return selected
        complete.load_data = load_selected
        ages.load_data = load_selected
        cohort_root = root / 'cohorts' / name
        cohort_root.mkdir(parents=True)
        os.chdir(cohort_root)
        print(f'FITTING COHORT {name}: {len(selected):,} utterances', flush=True)
        complete.main()
        ages.main()
finally:
    complete.load_data, ages.load_data = original_complete_loader, original_age_loader
    complete.extract_subject_verb_pairs, ages.extract_subject_verb_pairs = original_complete_extract, original_age_extract
    os.chdir(previous)
manifest['status'] = 'complete'
manifest['completed_at'] = datetime.now(timezone.utc).isoformat()
manifest['selection'] = selection
manifest['n_original_pairs'] = len(cached.pairs)
(audit / 'run.json').write_text(json.dumps(manifest, indent=2) + '\n')
