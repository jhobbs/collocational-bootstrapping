"""Compare strict UD, UD with copulas, and spaCy with and without be."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import ExitStack
import csv
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

from fit_matched_comparison import _rewrite_staged_json_paths
from fit_zipf import AGE_GROUPS, fit_files


ARM_LABELS = {'ud_strict': 'UD: strict rule', 'ud_copula': 'UD: includes copulas',
              'spacy_chat': 'spaCy: CHAT text', 'spacy_original': 'spaCy: original CSV text'}
COLORS = {'ud_strict': '#1b9e77', 'ud_copula': '#377eb8',
          'spacy_chat': '#d95f02', 'spacy_original': '#984ea3'}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_metadata(directory):
    return json.loads((Path(directory) / 'metadata.json').read_text())


def validate_inputs(population, augmented, spacy, spacy_original=None):
    utterances = Path(population) / 'english_ud_utterances.csv'
    source = read_metadata(population)
    expected = source['counts']['n_cds_utterances']
    sha = digest(utterances)
    strict_sha = digest(Path(population) / 'english_ud_subject_verb_pairs.csv')
    if read_metadata(augmented)['input'].get('strict_pairs_sha256') != strict_sha:
        raise ValueError('Augmented UD source strict pair hash differs')
    checks = [('ud_copula', augmented, 'n_cds_utterances', None),
              ('spacy_chat', spacy, 'n_included_utterances', 'text')]
    if spacy_original is not None:
        checks.append(('spacy_original', spacy_original, 'n_included_utterances', 'original_text'))
    parser = None
    for arm, directory, count_field, text_column in checks:
        metadata = read_metadata(directory)
        if metadata['input']['utterances_sha256'] != sha:
            raise ValueError(f'{arm} input utterance hash differs')
        if metadata['counts'][count_field] != expected:
            raise ValueError(f'{arm} input utterance count differs')
        if text_column:
            if metadata['input']['text_column'] != text_column:
                raise ValueError(f'{arm} source text column differs')
            if parser is not None and metadata.get('parser') != parser:
                raise ValueError('spaCy parser versions/settings differ between text arms')
            parser = metadata.get('parser')
    return {'utterances_sha256': sha, 'strict_pairs_sha256': strict_sha, 'n_utterances': expected,
            'population_metadata_sha256': digest(Path(population) / 'metadata.json')}


def prepare_inputs(population, arm_dirs, destination):
    """Keep a common denominator and stream each exact pair filter in source order."""
    destination = Path(destination)
    destination.mkdir(parents=True)
    source_utterances = Path(population) / 'english_ud_utterances.csv'
    eng_utterances = destination / 'eng_na_utterances.csv'
    n_all = n_eng = 0
    with source_utterances.open(newline='') as src, eng_utterances.open('w', newline='') as dst:
        reader = csv.DictReader(src)
        writer = csv.DictWriter(dst, fieldnames=reader.fieldnames)
        writer.writeheader()
        for row in reader:
            n_all += 1
            if row['collection'] == 'Eng-NA':
                writer.writerow(row)
                n_eng += 1
    result = {
        'broader_english': {'utterances': source_utterances.resolve(), 'n_utterances': n_all,
                            'pairs': {}, 'pair_counts': {}},
        'eng_na': {'utterances': eng_utterances.resolve(), 'n_utterances': n_eng,
                   'pairs': {}, 'pair_counts': {}},
    }
    for arm, directory in arm_dirs.items():
        source_pairs = Path(directory) / 'english_ud_subject_verb_pairs.csv'
        destinations = {
            ('broader_english', 'no_be'): destination / f'{arm}_no_be.csv',
            ('eng_na', 'all'): destination / f'{arm}_eng_na_all.csv',
            ('eng_na', 'no_be'): destination / f'{arm}_eng_na_no_be.csv',
        }
        counts = {key: 0 for key in destinations}
        n_pairs = 0
        with ExitStack() as stack:
            source = stack.enter_context(source_pairs.open(newline=''))
            reader = csv.DictReader(source)
            writers = {}
            for key, path in destinations.items():
                stream = stack.enter_context(path.open('w', newline=''))
                writers[key] = csv.DictWriter(stream, fieldnames=reader.fieldnames)
                writers[key].writeheader()
            for row in reader:
                n_pairs += 1
                no_be = row['verb_lemma'].lower() != 'be'
                selected = ([('broader_english', 'no_be')] if no_be else [])
                if row['collection'] == 'Eng-NA':
                    selected.append(('eng_na', 'all'))
                    if no_be:
                        selected.append(('eng_na', 'no_be'))
                for key in selected:
                    writers[key].writerow(row)
                    counts[key] += 1
        result['broader_english']['pairs'][(arm, 'all')] = source_pairs.resolve()
        result['broader_english']['pair_counts'][(arm, 'all')] = n_pairs
        for (scope, condition), path in destinations.items():
            result[scope]['pairs'][(arm, condition)] = path.resolve()
            result[scope]['pair_counts'][(arm, condition)] = counts[(scope, condition)]
        print(f'Prepared {arm}: {n_pairs:,} pairs', flush=True)
    return result


def _fit_job(job):
    fit_files(job['pairs'], job['utterances'], job['metadata'], job['output'])
    return job


def plot_summary(summary, condition, dataset, output):
    order = [label for _, _, label in AGE_GROUPS]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6), sharey=True)
    for axis, scope, title in zip(axes, ['eng_na', 'broader_english'], ['Eng-NA', 'Broader English']):
        subset = summary[(summary.scope == scope) & (summary.be_filter == condition)]
        for arm in ARM_LABELS:
            if arm not in set(subset.arm):
                continue
            frame = subset[subset.arm == arm].set_index('age_group').reindex(order)
            axis.plot(range(8), frame.alpha, marker='o', label=ARM_LABELS[arm], color=COLORS[arm])
        axis.set_xticks(range(8), [label.replace('mo', '') for label in order], rotation=40, ha='right')
        axis.set_title(title)
        axis.set_xlabel('Child age (months)')
        axis.spines[['top', 'right']].set_visible(False)
    axes[0].set_ylabel('Fitted Zipf exponent α')
    handles, labels = axes[1].get_legend_handles_labels()
    fig.legend(handles, labels, loc='lower center', ncol=2, frameon=False)
    caption = 'All verbs eligible' if condition == 'all' else 'be excluded; top 100 verbs reselected'
    fig.suptitle(f'{dataset}: {caption}')
    fig.tight_layout(rect=(0, .16, 1, .95))
    fig.savefig(output, dpi=240)
    plt.close(fig)


def run(population, augmented, spacy, output, *, dataset, workers=4, spacy_original=None):
    population, augmented, spacy, output = map(Path, [population, augmented, spacy, output])
    if output.exists():
        raise FileExistsError(output)
    if workers < 1:
        raise ValueError('workers must be positive')
    validation = validate_inputs(population, augmented, spacy, spacy_original)
    arms = {'ud_strict': population, 'ud_copula': augmented, 'spacy_chat': spacy}
    if spacy_original is not None:
        arms['spacy_original'] = Path(spacy_original)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.copula-diagnostics-', dir=output.parent)).resolve()
    try:
        prepared = prepare_inputs(population, arms, stage / 'inputs')
        if prepared['broader_english']['n_utterances'] != validation['n_utterances']:
            raise ValueError('Population CSV row count differs from metadata')
        provenance = {}
        for arm, directory in arms.items():
            pairs = directory / 'english_ud_subject_verb_pairs.csv'
            provenance[arm] = {'pairs': str(pairs.resolve()), 'pairs_sha256': digest(pairs),
                               'metadata': str((directory / 'metadata.json').resolve()),
                               'metadata_sha256': digest(directory / 'metadata.json')}
            metadata = read_metadata(directory)
            expected_pairs = metadata.get('counts', {}).get('n_pairs')
            if expected_pairs is not None and expected_pairs != prepared['broader_english']['pair_counts'][(arm, 'all')]:
                raise ValueError(f'{arm} pair count differs from metadata')
        jobs = []
        for scope, inputs in prepared.items():
            for (arm, condition), pairs in inputs['pairs'].items():
                metadata_path = stage / 'inputs' / f'{scope}_{arm}_{condition}.json'
                metadata_path.write_text(json.dumps({
                    'scope': scope, 'dataset': dataset, 'arm': arm, 'be_filter': condition,
                    'collection': 'Eng-NA' if scope == 'eng_na' else read_metadata(population)['collection'],
                    'annotation_scheme': ARM_LABELS[arm], 'population_validation': validation,
                    'source': provenance[arm], 'n_utterances': inputs['n_utterances'],
                    'n_pairs': inputs['pair_counts'][(arm, condition)],
                    'verb_selection': 'Original top-100 selection after applying the be filter; no fixed-vocabulary constraint.',
                }, indent=2) + '\n')
                jobs.append({'pairs': pairs, 'utterances': inputs['utterances'],
                             'metadata': metadata_path, 'output': stage / 'fits' / scope / arm / condition,
                             'scope': scope, 'arm': arm, 'be_filter': condition})
        if workers == 1:
            for job in jobs:
                _fit_job(job)
        else:
            with ProcessPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(_fit_job, job): job for job in jobs}
                for number, future in enumerate(as_completed(futures), 1):
                    job = future.result()
                    print(f"Completed fit {number}/{len(jobs)}: {job['scope']} {job['arm']} {job['be_filter']}", flush=True)
        summaries = []
        for job in jobs:
            frame = pd.read_csv(job['output'] / 'english_ud_summary.csv')
            frame.insert(0, 'dataset', dataset)
            frame.insert(1, 'arm', job['arm'])
            frame.insert(2, 'be_filter', job['be_filter'])
            summaries.append(frame)
        summary = pd.concat(summaries, ignore_index=True)
        if not (summary.groupby(['scope', 'age_group']).n_utterances.nunique() == 1).all():
            raise ValueError('Analysis arm denominators differ')
        summary.to_csv(stage / 'copula_summary.csv', index=False)
        plot_summary(summary, 'all', dataset, stage / 'copula_all_verbs.png')
        plot_summary(summary, 'no_be', dataset, stage / 'copula_without_be.png')
        (stage / 'metadata.json').write_text(json.dumps({
            'dataset': dataset, 'n_fits': len(jobs), 'population_validation': validation,
            'input_provenance': provenance, 'workers': workers,
            'conditions': ['all', 'no_be'], 'arms': list(arms),
            'denominator_policy': 'Identical population and ages within each corpus scope, including zero-pair utterances.',
            'top_verb_policy': 'Original top 100 eligible verbs reselected separately for each arm, age and be filter.',
        }, indent=2) + '\n')
        _rewrite_staged_json_paths(stage, output)
        stage.rename(output)
        print(f'Published {output}', flush=True)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['population', 'augmented', 'spacy', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--spacy-original', type=Path)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    run(args.population, args.augmented, args.spacy, args.output, dataset=args.dataset,
        workers=args.workers, spacy_original=args.spacy_original)


if __name__ == '__main__':
    main()
