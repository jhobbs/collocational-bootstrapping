"""Regenerate copula comparison figures, alpha tables and endpoint summaries."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


AGES = [f'{a}-{a+12}mo' for a in range(0, 96, 12)]
SCOPES = {'eng_na': 'Eng-NA', 'broader_english': 'broader English'}
ARMS = {'ud_strict': 'Strict UD', 'ud_copula': 'UD including copulas',
        'spacy_chat': 'spaCy on CHAT text'}
COLORS = {'ud_strict': '#1b9e77', 'ud_copula': '#377eb8', 'spacy_chat': '#d95f02'}


def load_summary(path, *, paper=False):
    frame = pd.read_csv(path)
    keys = ['age_group'] if paper else ['scope', 'arm', 'be_filter', 'age_group']
    if frame.duplicated(keys).any():
        raise ValueError(f'Duplicate summary rows: {path}')
    if not frame.status.eq('ok').all() or not np.isfinite(frame.alpha).all():
        raise ValueError(f'Unfitted or invalid alpha values: {path}')
    if paper:
        if set(frame.age_group) != set(['overall'] + AGES):
            raise ValueError(f'Incomplete paper age coverage: {path}')
    else:
        if not frame.groupby(['scope', 'age_group']).n_utterances.nunique().eq(1).all():
            raise ValueError(f'Method denominator mismatch: {path}')
        present = set(frame[keys].itertuples(index=False, name=None))
        arms = list(ARMS) + (['spacy_original'] if frame.arm.eq('spacy_original').any() else [])
        expected = {(scope, arm, condition, age) for scope in SCOPES for arm in arms
                    for condition in ['all', 'no_be'] for age in ['overall'] + AGES}
        if expected != present:
            raise ValueError(f'Incomplete method/condition/age coverage: {path}')
    return frame


def endpoint_rows(frame, dataset, *, paper=False):
    result = []
    if paper:
        groups = [(('paper_saved', 'spacy_saved', dataset), frame)]
        dataset = 'paper_saved'
    else:
        groups = frame.groupby(['scope', 'arm', 'be_filter'], sort=False)
    for (scope, arm, condition), group in groups:
        indexed = group.set_index('age_group')
        youngest, oldest = float(indexed.loc[AGES[0], 'alpha']), float(indexed.loc[AGES[-1], 'alpha'])
        result.append(dict(dataset=dataset, scope=scope, arm=arm, be_filter=condition,
                           youngest_alpha=youngest, oldest_alpha=oldest,
                           oldest_minus_youngest=oldest-youngest))
    return result


def alpha_tables(summaries):
    sections = []
    columns = [(arm, condition) for condition in ['all', 'no_be'] for arm in ARMS]
    for dataset, frame in summaries.items():
        title = 'Matched historical subset' if dataset == 'matched' else 'Larger earlier TalkBank population'
        for scope, name in SCOPES.items():
            table = frame[frame.scope == scope].pivot(index='age_group', columns=['arm', 'be_filter'], values='alpha')
            lines = [f'### {title}: {name}', '',
                     '| Age (months) | UD | UD+cop | spaCy | UD −be | UD+cop −be | spaCy −be |',
                     '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
            for age in ['overall'] + AGES:
                label = 'Overall' if age == 'overall' else age.replace('mo', '').replace('-', '–')
                lines.append('| ' + label + ' | ' + ' | '.join(f'{table.loc[age, col]:.2f}' for col in columns) + ' |')
            sections.append('\n'.join(lines))
    return '\n\n'.join(sections) + '\n'


def plot_populations(summaries, paper, paper_no_be, destination):
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), sharex=True, sharey=True)
    for i, (dataset, summary) in enumerate(summaries.items()):
        population = 'Matched historical subset' if dataset == 'matched' else 'Larger earlier TalkBank population'
        broader = summary[summary.scope == 'broader_english']
        n = int(broader[broader.age_group == 'overall'].n_utterances.iloc[0])
        for j, condition in enumerate(['all', 'no_be']):
            axis = axes[i, j]
            for arm, label in ARMS.items():
                values = broader[(broader.arm == arm) & (broader.be_filter == condition)].set_index('age_group').reindex(AGES)
                axis.plot(range(8), values.alpha, marker='o', markersize=4, color=COLORS[arm], label=label)
            reference = paper if condition == 'all' else paper_no_be
            axis.plot(range(8), reference.set_index('age_group').reindex(AGES).alpha,
                      color='#666666', linestyle='--', marker='s', markersize=3,
                      label="Paper's saved data (different population)")
            axis.set_title(f'{population}\n{n:,} utterances', fontsize=10)
            axis.set_xticks(range(8), [x.replace('mo', '') for x in AGES], rotation=35, ha='right')
            axis.grid(axis='y', alpha=.2)
            axis.spines[['top', 'right']].set_visible(False)
            if j == 0:
                axis.set_ylabel('Fitted Zipf exponent α')
            if i == 1:
                axis.set_xlabel('Child age (months)')
    fig.text(.285, .94, 'All verbs eligible', ha='center', weight='bold')
    fig.text(.755, .94, 'be excluded', ha='center', weight='bold')
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='lower center', ncol=2, frameon=False, bbox_to_anchor=(.5, .01))
    fig.suptitle('Broader English: matched and larger populations', y=.995, fontsize=14)
    fig.tight_layout(rect=(0, .1, 1, .91))
    fig.savefig(destination / 'population_comparison.png', dpi=240, bbox_inches='tight')
    fig.savefig(destination / 'population_comparison.pdf', bbox_inches='tight')
    plt.close(fig)


def run(matched, full, baseline, paper_no_be, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    sources = {'matched': Path(matched) / 'copula_summary.csv',
               'earlier_talkbank': Path(full) / 'copula_summary.csv',
               'paper_all': Path(baseline) / 'baseline_summary.csv',
               'paper_no_be': Path(paper_no_be) / 'summary.csv'}
    summaries = {name: load_summary(sources[name]) for name in ['matched', 'earlier_talkbank']}
    paper, filtered = [load_summary(sources[name], paper=True) for name in ['paper_all', 'paper_no_be']]
    if not paper.set_index('age_group').n_utterances.sort_index().equals(
            filtered.set_index('age_group').n_utterances.sort_index()):
        raise ValueError('Saved paper all/no-be denominator mismatch')
    provenance = {}
    for name, path in sources.items():
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        provenance[name] = {'path': str(path.resolve()), 'sha256': digest}
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.copula-report-', dir=output.parent))
    try:
        plot_populations(summaries, paper, filtered, stage)
        (stage / 'alpha_tables.md').write_text(alpha_tables(summaries))
        endpoints = [row for name, frame in summaries.items() for row in endpoint_rows(frame, name)]
        endpoints += endpoint_rows(paper, 'all', paper=True) + endpoint_rows(filtered, 'no_be', paper=True)
        pd.DataFrame(endpoints).to_csv(stage / 'endpoints.csv', index=False)
        (stage / 'metadata.json').write_text(json.dumps({
            'inputs': provenance, 'endpoint_bins': [AGES[0], AGES[-1]],
            'scope_of_figure': 'broader_english',
            'figure_and_table_arms': list(ARMS),
            'endpoint_arms': {name: list(frame.arm.unique()) for name, frame in summaries.items()},
            'optional_control': 'spacy_original, when present, is included in endpoints.csv; figures and tables show the three CHAT-based methods.',
            'reference': 'Saved paper data use a different population from either solid-line cohort.',
            'interpretation': 'Descriptive endpoints and fitted curves; no monotonicity or uncertainty inference.',
        }, indent=2) + '\n')
        stage.rename(output)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--matched-fits', required=True, type=Path)
    parser.add_argument('--full-fits', required=True, type=Path)
    parser.add_argument('--baseline', required=True, type=Path)
    parser.add_argument('--paper-no-be', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    run(args.matched_fits, args.full_fits, args.baseline, args.paper_no_be, args.output)
    print(f'Published {args.output}')


if __name__ == '__main__':
    main()
