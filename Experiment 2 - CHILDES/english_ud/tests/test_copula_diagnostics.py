import csv
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from fit_copula_diagnostics import prepare_inputs, run, validate_inputs


def write_csv(path, rows):
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def inputs(tmp_path):
    population, augmented, spacy = [tmp_path / name for name in ['population', 'augmented', 'spacy']]
    for path in [population, augmented, spacy]:
        path.mkdir()
    utterances, pairs = [], []
    for collection, age in [('Eng-NA', 6), ('Eng-UK', 90)]:
        for i in range(156):
            utterance = dict(collection=collection, corpus='Example', transcript=collection + '/a.cha',
                             utterance_id=str(i), target_child_age_months=str(age),
                             included_in_cds='True', text='she is happy', n_pairs='1')
            utterances.append(utterance)
            pairs.append({**utterance, 'subject_lemma': 'subject' + chr(97 + i % 12),
                          'verb_lemma': 'be' if i % 13 == 0 else 'verb' + chr(97 + i % 13)})
        utterances.append({**utterance, 'utterance_id': 'zero', 'n_pairs': '0', 'text': 'hello'})
    write_csv(population / 'english_ud_utterances.csv', utterances)
    write_csv(population / 'english_ud_subject_verb_pairs.csv', pairs)
    write_csv(spacy / 'english_ud_subject_verb_pairs.csv', pairs)
    additions = [{**pairs[1], 'verb_lemma': 'be'}, {**pairs[2], 'verb_lemma': 'seem'}]
    write_csv(augmented / 'english_ud_subject_verb_pairs.csv', pairs + additions)
    digest = hashlib.sha256((population / 'english_ud_utterances.csv').read_bytes()).hexdigest()
    metadata = {'scope': 'fixture', 'collection': 'Eng-NA;Eng-UK', 'annotation_scheme': 'UD',
                'counts': {'n_cds_utterances': len(utterances)}}
    (population / 'metadata.json').write_text(json.dumps(metadata))
    (spacy / 'metadata.json').write_text(json.dumps({'annotation_scheme': 'spaCy',
        'input': {'utterances_sha256': digest, 'text_column': 'text'},
        'counts': {'n_included_utterances': len(utterances), 'n_pairs': len(pairs)}}))
    pair_digest = hashlib.sha256((population / 'english_ud_subject_verb_pairs.csv').read_bytes()).hexdigest()
    (augmented / 'metadata.json').write_text(json.dumps({'input': {'utterances_sha256': digest,
        'strict_pairs_sha256': pair_digest},
        'counts': {'n_cds_utterances': len(utterances), 'n_pairs': len(pairs) + len(additions)}}))
    return population, augmented, spacy


def test_no_be_filter_preserves_population_and_non_be_copulas(inputs, tmp_path):
    population, augmented, spacy = inputs
    prepared = prepare_inputs(population, {'ud_strict': population, 'ud_copula': augmented,
                                         'spacy_chat': spacy}, tmp_path / 'prepared')
    eng = prepared['eng_na']
    utterances = pd.read_csv(eng['utterances'])
    assert len(utterances) == 157  # Includes the zero-pair utterance.
    assert set(utterances.collection) == {'Eng-NA'}
    strict = pd.read_csv(eng['pairs'][('ud_strict', 'no_be')])
    copula = pd.read_csv(eng['pairs'][('ud_copula', 'no_be')])
    assert 'be' not in set(copula.verb_lemma)
    assert len(copula) == len(strict) + 1
    assert 'seem' in set(copula.verb_lemma)
    assert set(copula.target_child_age_months) == {6}


def test_source_hash_mismatch_rejected(inputs):
    population, augmented, spacy = inputs
    with (population / 'english_ud_utterances.csv').open('a') as stream:
        stream.write('\n')
    with pytest.raises(ValueError, match='hash'):
        validate_inputs(population, augmented, spacy)


def test_augmented_source_strict_pair_hash_must_match(inputs):
    population, augmented, spacy = inputs
    path = population / 'english_ud_subject_verb_pairs.csv'
    path.write_text(path.read_text().replace('subjecta', 'changeda'))
    with pytest.raises(ValueError, match='strict.*hash'):
        validate_inputs(population, augmented, spacy)


def test_completed_diagnostic_uses_same_denominators_and_valid_paths(inputs, tmp_path):
    population, augmented, spacy = inputs
    output = tmp_path / 'new-parent' / 'result'
    run(population, augmented, spacy, output, dataset='fixture', workers=1)
    summary = pd.read_csv(output / 'copula_summary.csv')
    assert len(summary) == 108  # Three arms, two filters, two scopes, nine ages.
    assert (summary.groupby(['scope', 'age_group']).n_utterances.nunique() == 1).all()
    overall = summary[(summary.scope == 'eng_na') & (summary.age_group == 'overall')]
    assert set(overall.n_utterances) == {157}
    for path in output.rglob('fit_metadata.json'):
        for value in json.loads(path.read_text())['inputs'].values():
            assert Path(value).is_file()
    assert (output / 'copula_all_verbs.png').is_file()
    assert (output / 'copula_without_be.png').is_file()
    with pytest.raises(FileExistsError):
        run(population, augmented, spacy, output, dataset='fixture', workers=1)
