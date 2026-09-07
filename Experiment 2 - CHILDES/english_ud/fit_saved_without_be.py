"""Remove be from Claire's saved pair datasets and reuse the original fitting math."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile

import pandas as pd

from fit_copula_diagnostics import digest
from fit_zipf import _fit_scope, _plot_trajectory
from preserve_baseline import DATASETS


def run(baseline, output):
    baseline, output = Path(baseline).resolve(), Path(output)
    if output.exists():
        raise FileExistsError(output)
    original = pd.read_csv(baseline / 'baseline_summary.csv').set_index('age_group')
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.saved-no-be-', dir=output.parent))
    try:
        (stage / 'filtered_pairs').mkdir()
        fits = stage / 'fits'
        fits.mkdir()
        summaries, sources = [], []
        metadata = {'scope': 'paper_saved_spacy', 'collection': 'historical_English_participants',
                    'annotation_scheme': 'Saved original spaCy pairs with verb lemma be excluded'}
        for age, directory, suffix in DATASETS:
            source = baseline / directory / f'nsubj_verb_pairs_{suffix}.csv'
            pairs = pd.read_csv(source, dtype=str, keep_default_na=False)
            if len(pairs) != int(original.loc[age, 'n_pairs']):
                raise ValueError(f'Saved pair count differs from baseline summary for {age}')
            keep = pairs.verb_lemma.str.lower() != 'be'
            filtered = pairs.loc[keep].copy()
            filtered.to_csv(stage / 'filtered_pairs' / f'{age}.csv', index=False)
            row = _fit_scope(filtered, int(original.loc[age, 'n_utterances']), age, fits,
                             metadata, apply_age_thresholds=age != 'overall')
            summaries.append(row)
            sources.append({'age_group': age, 'source_pairs': str(source),
                            'source_pairs_sha256': digest(source),
                            'original_pairs': len(pairs), 'removed_be_pairs': int((~keep).sum()),
                            'remaining_pairs': len(filtered), 'n_utterances': row['n_utterances']})
        summary = pd.DataFrame(summaries)
        summary.to_csv(stage / 'summary.csv', index=False)
        _plot_trajectory(summary, fits)
        (stage / 'metadata.json').write_text(json.dumps({
            **metadata, 'source_baseline': str(baseline),
            'baseline_summary_sha256': digest(baseline / 'baseline_summary.csv'),
            'sources': sources, 'lemma_filter': "verb_lemma.lower() != 'be'",
            'fitting_policy': 'Unchanged fit_zipf._fit_scope, top 100 verbs reselected after filtering.',
            'denominator_policy': 'Original saved utterance count for each age bin, including zero remaining pairs.',
        }, indent=2) + '\n')
        stage.rename(output)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    run(args.baseline, args.output)


if __name__ == '__main__':
    main()
