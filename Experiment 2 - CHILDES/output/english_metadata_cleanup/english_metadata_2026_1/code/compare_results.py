"""Compare this corrected run with the preserved historical analysis."""
import argparse
from pathlib import Path
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('run_directory', type=Path)
parser.add_argument('historical_baseline', type=Path)
args = parser.parse_args()
root, baseline = args.run_directory, args.historical_baseline
new = pd.read_csv(root / 'output/age_groups_complete_96mos/summary_96mos.csv')
old = pd.read_csv(baseline / 'age_groups_complete_96mos/summary_96mos.csv')
fields = ['age_group', 'z', 'n_utterances', 'n_pairs']
comparison = old[fields].merge(new[fields], on='age_group', suffixes=('_historical', '_corrected'))
comparison.to_csv(root / 'comparison_with_historical.csv', index=False)
words = ['und', 'du', 'das', 'nicht', 'ich', 'wir', 'gucken', 'wörter', 'quatsch', 'natürlich',
         'machen', 'zeigen', 'mal', 'ja', 'dir', 'ist', 'auch', 'denn', 'wie', 'bisschen']
records = []
for age in ['72-84mo', '84-96mo']:
    for label, directory in [('historical', baseline), ('corrected', root / 'output')]:
        ranked = pd.read_csv(directory / f'age_groups_complete_96mos/all_verbs_ranked_{age}.csv', keep_default_na=False)
        for word in words:
            matching = ranked[(ranked.verb == 'be') & (ranked.subject == word)]
            records.append({'age_group': age, 'run': label, 'subject': word, 'frequency': int(matching.frequency.sum())})
pd.DataFrame(records).to_csv(root / 'audit/german_be_subject_comparison.csv', index=False)
fig, ax = plt.subplots(figsize=(8, 4.5))
x = range(len(comparison))
ax.plot(x, comparison.z_historical, 'o-', color='#777777', label='Historical 2021.1 · original selection', lw=1.8)
ax.plot(x, comparison.z_corrected, 'o-', color='#1479A0', label='Corrected 2026.1 · English metadata filters', lw=2)
for i, value in enumerate(comparison.z_corrected):
    ax.annotate(f'{value:.2f}', (i, value), xytext=(0, 8), textcoords='offset points', ha='center', fontsize=9, color='#126584')
ax.set_xticks(list(x), [value.replace('mo', '') for value in comparison.age_group])
ax.set_xlabel('Target child age (months)')
ax.set_ylabel('Fitted Zipf parameter α')
ax.set_ylim(1.15, 1.62)
ax.grid(axis='y', alpha=.18)
ax.spines[['top', 'right']].set_visible(False)
ax.legend(loc='lower left', frameon=False)
fig.suptitle('CHILDES: historical and corrected age-bin fits', x=.12, ha='left', fontsize=14)
fig.text(.12, .02, 'Both data release and selection changed; this comparison does not isolate German removal.', fontsize=9, color='#555555')
fig.tight_layout(rect=(0, .06, 1, .94))
fig.savefig(root / 'comparison_with_historical.png', dpi=200)
plt.close(fig)
