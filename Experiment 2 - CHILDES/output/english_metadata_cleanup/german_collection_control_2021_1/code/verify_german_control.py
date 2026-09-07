"""Verify the German-collection exclusion against independent historical metadata."""
import argparse
import json
from pathlib import Path
import pickle

import numpy as np
import pandas as pd

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('control', type=Path)
parser.add_argument('historical_baseline', type=Path)
parser.add_argument('independent_age_counts', type=Path)
args = parser.parse_args()
root = args.control
members = pd.read_csv(root / 'audit/cohort_membership.csv', keep_default_na=False)
if members.csv_row_index.duplicated().any():
    raise ValueError('Duplicate source rows in original cohort')
members = members.set_index('csv_row_index')
pairs = pd.read_csv(root / 'parsed/english_ud_subject_verb_pairs.csv',
                    usecols=['csv_row_index', 'subject_lemma', 'verb_lemma'], keep_default_na=False)
if not pairs.csv_row_index.is_monotonic_increasing or not pairs.csv_row_index.isin(members.index).all():
    raise ValueError('Parsed pair identity/order mismatch')
independent = pd.read_csv(args.independent_age_counts)
expected_german = independent[independent.collection_name.eq('German')].groupby('age_bin_start').n_utterances.sum()
actual_german = members[members.collection_name.eq('German')]
if len(actual_german) != int(expected_german.sum()):
    raise ValueError('German collection count disagrees with independent database query')
for lo in range(0, 96, 12):
    observed = int((actual_german.target_child_age.ge(lo) & actual_german.target_child_age.lt(lo + 12)).sum())
    if observed != int(expected_german.get(lo, 0)):
        raise ValueError(f'German collection age count mismatch: {lo}')

pair_columns = ['subject_lemma', 'verb_lemma']
pair_metadata = members.loc[pairs.csv_row_index].reset_index(drop=True)
checks = []
for name, utterance_mask in [('original', pd.Series(True, index=members.index)),
                              ('without_german_collection', members.collection_name.ne('German'))]:
    selected_ids = members.index[utterance_mask]
    selected_pairs = pairs.csv_row_index.isin(selected_ids)
    output = root / 'cohorts' / name / 'output'
    with (output / 'complete_dataset_96mos/summary_96mos.pkl').open('rb') as stream:
        overall = pickle.load(stream)
    if overall['n_utterances'] != int(utterance_mask.sum()) or overall['n_pairs'] != int(selected_pairs.sum()):
        raise ValueError(f'Cohort summary mismatch: {name}')
    saved_pairs = pd.read_csv(output / 'complete_dataset_96mos/nsubj_verb_pairs_96mos.csv', keep_default_na=False)
    if not saved_pairs.equals(pairs.loc[selected_pairs, pair_columns].reset_index(drop=True)):
        raise ValueError(f'Overall pair output differs from exact selected rows: {name}')
    summary = pd.read_csv(output / 'age_groups_complete_96mos/summary_96mos.csv')
    if len(summary) != 8:
        raise ValueError(f'Missing age fits: {name}')
    fit_cases = [('overall', output / 'complete_dataset_96mos', '96mos', overall['optimal_z'])]
    for row in summary.itertuples():
        age_mask = members.target_child_age.ge(row.age_min_mo) & members.target_child_age.lt(row.age_max_mo)
        pair_age_mask = pair_metadata.target_child_age.ge(row.age_min_mo) & pair_metadata.target_child_age.lt(row.age_max_mo)
        expected_pairs = pairs.loc[selected_pairs & pair_age_mask, pair_columns].reset_index(drop=True)
        if row.n_utterances != int((utterance_mask & age_mask).sum()) or row.n_pairs != len(expected_pairs):
            raise ValueError(f'Age population mismatch: {name}/{row.age_group}')
        saved = pd.read_csv(output / f'age_groups_complete_96mos/nsubj_verb_pairs_{row.age_group}.csv', keep_default_na=False)
        if not saved.equals(expected_pairs):
            raise ValueError(f'Age pairs differ from selected rows: {name}/{row.age_group}')
        fit_cases.append((row.age_group, output / 'age_groups_complete_96mos', row.age_group, row.z))
    for label, directory, suffix, alpha in fit_cases:
        search = pd.read_csv(directory / f'mse_search_{suffix}.csv')
        if not np.isclose(search.loc[search.mse.idxmin(), 'z'], alpha):
            raise ValueError(f'Fit minimum mismatch: {name}/{label}')
    checks.append({'cohort': name, 'n_utterances': overall['n_utterances'], 'n_pairs': overall['n_pairs'],
                   'overall_alpha': float(overall['optimal_z']), 'verified_fits': len(fit_cases)})

historical = pd.read_csv(args.historical_baseline / 'complete_dataset_96mos/nsubj_verb_pairs_96mos.csv', keep_default_na=False)
matches_historical = historical.equals(pairs[pair_columns].reset_index(drop=True))
report = {'status': 'passed', 'cohorts': checks, 'only_exclusion': "collection_name == 'German'",
          'n_german_collection_utterances': len(actual_german),
          'removed_counts_match_independent_database_query': True,
          'all_cohort_and_age_pair_outputs_match_exactly': True,
          'fresh_original_pairs_match_historical_saved_pairs_exactly': matches_historical}
(root / 'audit/control_verification.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
