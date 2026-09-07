"""Check completed fits, age counts, pair counts, and original-parser agreement."""

import argparse
import json
from pathlib import Path
import pickle

import numpy as np
import pandas as pd
import spacy

import analyze_complete_dataset_96mos as original
from run_childes_analysis import CachedPairs


def verify_analysis(root):
    root = Path(root)
    utterances = pd.read_csv(root / 'parser_input/utterances.csv',
                             usecols=['csv_row_index', 'target_child_age_months', 'text'],
                             keep_default_na=False)
    age_counts = {}
    samples = [utterances.head(100), utterances.tail(100), utterances.sample(min(500, len(utterances)), random_state=42)]
    for lower in range(0, 96, 12):
        group = utterances[(utterances.target_child_age_months >= lower) & (utterances.target_child_age_months < lower + 12)]
        age_counts[f'{lower}-{lower + 12}mo'] = len(group)
        if len(group):
            samples.append(group.sample(min(64, len(group)), random_state=42))
    sample = pd.concat(samples).drop_duplicates('csv_row_index').sort_values('csv_row_index')
    sample = sample.set_index('csv_row_index').rename(columns={'text': 'gloss'})
    sample.to_csv(root / 'audit/parser_spotcheck_utterances.csv')
    expected = original.extract_subject_verb_pairs(sample, spacy.load('en_core_web_sm'))
    cached = CachedPairs(root / 'parsed/english_ud_subject_verb_pairs.csv', utterances.csv_row_index)
    actual = cached(sample, None)
    if actual != expected:
        raise ValueError('Parallel parser disagrees with original parser on sampled utterances')
    with (root / 'output/complete_dataset_96mos/summary_96mos.pkl').open('rb') as stream:
        overall = pickle.load(stream)
    age_summary = pd.read_csv(root / 'output/age_groups_complete_96mos/summary_96mos.csv')
    if len(age_summary) != 8 or overall['n_utterances'] != len(utterances) or overall['n_pairs'] != len(cached.pairs):
        raise ValueError('Analysis summary population or pair count mismatch')
    paired_ages = cached.pairs.merge(utterances[['csv_row_index', 'target_child_age_months']], on='csv_row_index', validate='many_to_one')
    for row in age_summary.itertuples():
        lower, upper = row.age_min_mo, row.age_max_mo
        n_pairs = int(((paired_ages.target_child_age_months >= lower) & (paired_ages.target_child_age_months < upper)).sum())
        if row.n_utterances != age_counts[row.age_group] or row.n_pairs != n_pairs:
            raise ValueError(f'Age-bin counts mismatch: {row.age_group}')
    fit_checks = []
    cases = [('overall', root / 'output/complete_dataset_96mos', '96mos', overall['optimal_z'])]
    cases += [(row.age_group, root / 'output/age_groups_complete_96mos', row.age_group, row.z)
              for row in age_summary.itertuples()]
    for label, directory, suffix, alpha in cases:
        search = pd.read_csv(directory / f'mse_search_{suffix}.csv')
        best = search.loc[search.mse.idxmin(), 'z']
        comparison = pd.read_csv(directory / f'actual_vs_predicted_{suffix}.csv')
        if not np.isclose(best, alpha) or not np.isclose(comparison.actual_frequency.sum(), 1) or not np.isclose(comparison.predicted_frequency.sum(), 1):
            raise ValueError(f'Fit verification failed: {label}')
        fit_checks.append(label)
    report = {
        'status': 'passed', 'n_utterances': len(utterances), 'n_pairs': len(cached.pairs),
        'age_counts': age_counts, 'all_age_pair_counts_match': True,
        'spotcheck_utterances': len(sample), 'spotcheck_pairs': len(expected),
        'spotcheck_matches_original_parser_exactly': True, 'verified_fits': fit_checks,
    }
    (root / 'audit/analysis_verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_directory', type=Path)
    verify_analysis(parser.parse_args().run_directory)
