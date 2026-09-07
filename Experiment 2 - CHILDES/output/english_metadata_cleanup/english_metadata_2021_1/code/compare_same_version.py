"""Compare the historical baseline, German removal, and full metadata cleanup."""
import argparse
from pathlib import Path
import pickle

import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--corrected-2021', type=Path, required=True)
parser.add_argument('--corrected-2026', type=Path, required=True)
parser.add_argument('--german-control', type=Path, required=True)
parser.add_argument('--historical-baseline', type=Path, required=True)
args = parser.parse_args()
roots = {
    'historical_saved': args.historical_baseline,
    'original_refit_2021': args.german_control / 'cohorts/original/output',
    'without_german_collection_2021': args.german_control / 'cohorts/without_german_collection/output',
    'corrected_2021': args.corrected_2021 / 'output',
    'corrected_2026': args.corrected_2026 / 'output',
}
records = []
for name, root in roots.items():
    with (root / 'complete_dataset_96mos/summary_96mos.pkl').open('rb') as stream:
        overall = pickle.load(stream)
    records.append({'run': name, 'age_group': 'overall', 'alpha': overall['optimal_z'],
                    'n_utterances': overall['n_utterances'], 'n_pairs': overall['n_pairs']})
    age = pd.read_csv(root / 'age_groups_complete_96mos/summary_96mos.csv')
    for row in age.itertuples():
        records.append({'run': name, 'age_group': row.age_group, 'alpha': row.z,
                        'n_utterances': row.n_utterances, 'n_pairs': row.n_pairs})
results = pd.DataFrame(records)
results.to_csv(args.corrected_2021 / 'same_version_comparison.csv', index=False)
order = ['overall'] + [f'{lo}-{lo + 12}mo' for lo in range(0, 96, 12)]
wide = results.pivot(index='age_group', columns='run', values='alpha').reindex(order)
wide.to_csv(args.corrected_2021 / 'alpha_comparison.csv')
print(wide.to_string(float_format=lambda value: f'{value:.2f}'))

fig, ax = plt.subplots(figsize=(9, 5))
styles = {
    'original_refit_2021': ('Original 2021.1', '#777777', '-'),
    'without_german_collection_2021': ('2021.1: remove German collection only', '#AA5599', '-'),
    'corrected_2021': ('2021.1: full metadata cleanup', '#1479A0', '-'),
    'corrected_2026': ('2026.1: full metadata cleanup', '#348C57', '--'),
}
for name, (label, color, linestyle) in styles.items():
    ax.plot(range(8), wide.loc[order[1:], name], marker='o', color=color,
            linestyle=linestyle, label=label, lw=1.8, markersize=5)
ax.set_xticks(range(8), [age.replace('mo', '') for age in order[1:]])
ax.set_xlabel('Target child age (months)')
ax.set_ylabel('Fitted Zipf parameter α')
ax.set_ylim(1.15, max(1.65, float(wide.max().max()) + .08))
ax.grid(axis='y', alpha=.18)
ax.spines[['top', 'right']].set_visible(False)
ax.legend(loc='lower left', frameon=False, fontsize=9)
fig.suptitle('CHILDES: separate German removal from broader cleanup', x=.12, ha='left', fontsize=14)
fig.text(.12, .02, 'The three 2021.1 curves use the same database release, parser, age rules, and fitting method.', fontsize=9, color='#555555')
fig.tight_layout(rect=(0, .06, 1, .94))
fig.savefig(args.corrected_2021 / 'same_version_comparison.png', dpi=200)
plt.close(fig)

words = ['und', 'du', 'das', 'nicht', 'ich', 'wir', 'gucken', 'wörter', 'quatsch', 'natürlich',
         'machen', 'zeigen', 'mal', 'ja', 'dir', 'ist', 'auch', 'denn', 'wie', 'bisschen']
terms = []
for name, root in roots.items():
    for age in ['72-84mo', '84-96mo']:
        ranked = pd.read_csv(root / f'age_groups_complete_96mos/all_verbs_ranked_{age}.csv', keep_default_na=False)
        for word in words:
            matching = ranked[(ranked.verb == 'be') & (ranked.subject == word)]
            terms.append({'run': name, 'age_group': age, 'subject': word,
                          'frequency': int(matching.frequency.sum())})
pd.DataFrame(terms).to_csv(args.corrected_2021 / 'audit/german_be_subject_comparison.csv', index=False)
