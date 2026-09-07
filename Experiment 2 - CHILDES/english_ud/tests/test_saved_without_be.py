import csv
import json

import pandas as pd

from fit_saved_without_be import run
from preserve_baseline import DATASETS


def test_saved_pair_filter_keeps_historical_denominators(tmp_path):
    baseline = tmp_path / 'baseline'
    baseline.mkdir()
    summaries = []
    for age, directory, suffix in DATASETS:
        folder = baseline / directory
        folder.mkdir(exist_ok=True)
        rows = [{'subject_lemma': 'subject' + chr(97 + i % 12),
                 'verb_lemma': 'be' if i % 13 == 0 else 'verb' + chr(97 + i % 13)}
                for i in range(156)]
        pd.DataFrame(rows).to_csv(folder / f'nsubj_verb_pairs_{suffix}.csv', index=False)
        summaries.append({'age_group': age, 'n_utterances': 200, 'n_pairs': 156, 'alpha': 1.4})
    pd.DataFrame(summaries).to_csv(baseline / 'baseline_summary.csv', index=False)
    output = tmp_path / 'no_be'
    run(baseline, output)
    summary = pd.read_csv(output / 'summary.csv')
    assert len(summary) == 9
    assert set(summary.n_utterances) == {200}
    assert set(summary.n_pairs) == {144}
    for path in (output / 'filtered_pairs').glob('*.csv'):
        assert 'be' not in set(pd.read_csv(path).verb_lemma)
    metadata = json.loads((output / 'metadata.json').read_text())
    assert set(record['removed_be_pairs'] for record in metadata['sources']) == {12}
