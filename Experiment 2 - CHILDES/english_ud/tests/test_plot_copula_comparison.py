import json

import pandas as pd
import pytest


@pytest.fixture
def summaries(tmp_path):
    folders = [tmp_path / name for name in ['matched', 'full', 'baseline', 'paper_no_be']]
    for folder in folders:
        folder.mkdir()
    ages = ['overall'] + [f'{a}-{a+12}mo' for a in range(0, 96, 12)]
    for folder, n in zip(folders[:2], [160, 240]):
        rows = []
        for scope in ['eng_na', 'broader_english']:
            for arm in ['ud_strict', 'ud_copula', 'spacy_chat']:
                for condition in ['all', 'no_be']:
                    for i, age in enumerate(ages):
                        rows.append(dict(scope=scope, arm=arm, be_filter=condition,
                                         age_group=age, alpha=1.1+i*.1, status='ok',
                                         n_utterances=n if age=='overall' else n//8))
        pd.DataFrame(rows).to_csv(folder / 'copula_summary.csv', index=False)
    paper = pd.DataFrame(dict(age_group=ages, alpha=[1.5,1.8,1.7,1.6,1.5,1.4,1.3,1.2,1.1],
                              n_utterances=[320]+[40]*8, status='ok'))
    paper.to_csv(folders[2] / 'baseline_summary.csv', index=False)
    paper.to_csv(folders[3] / 'summary.csv', index=False)
    return folders


def test_reusable_report_derives_endpoints_and_preserves_inputs(summaries, tmp_path):
    from plot_copula_comparison import run
    matched, full, baseline, paper_no_be = summaries
    before = (matched/'copula_summary.csv').read_bytes()
    output = tmp_path/'report'
    run(matched, full, baseline, paper_no_be, output)
    endpoints = pd.read_csv(output/'endpoints.csv')
    row = endpoints[(endpoints.dataset=='matched') & (endpoints.scope=='broader_english') &
                    (endpoints.arm=='ud_strict') & (endpoints.be_filter=='all')].iloc[0]
    assert row.youngest_alpha == pytest.approx(1.2)
    assert row.oldest_alpha == pytest.approx(1.9)
    assert row.oldest_minus_youngest == pytest.approx(.7)
    assert (output/'population_comparison.png').stat().st_size > 1000
    assert (output/'population_comparison.pdf').read_bytes().startswith(b'%PDF')
    assert (output/'alpha_tables.md').is_file()
    assert len(json.loads((output/'metadata.json').read_text())['inputs']) == 4
    assert (matched/'copula_summary.csv').read_bytes() == before
    with pytest.raises(FileExistsError):
        run(matched, full, baseline, paper_no_be, output)


def test_report_rejects_unequal_method_denominators(summaries, tmp_path):
    from plot_copula_comparison import run
    matched, full, baseline, paper_no_be = summaries
    path = matched/'copula_summary.csv'
    frame = pd.read_csv(path)
    frame.loc[1,'n_utterances'] += 1
    frame.to_csv(path,index=False)
    with pytest.raises(ValueError, match='denominator'):
        run(matched, full, baseline, paper_no_be, tmp_path/'report')
    assert not (tmp_path/'report').exists()


def test_report_rejects_paper_filter_population_change(summaries, tmp_path):
    from plot_copula_comparison import run
    matched, full, baseline, paper_no_be = summaries
    path = paper_no_be/'summary.csv'
    frame = pd.read_csv(path)
    frame.loc[1,'n_utterances'] += 1
    frame.to_csv(path,index=False)
    with pytest.raises(ValueError, match='paper.*denominator'):
        run(matched, full, baseline, paper_no_be, tmp_path/'report')


def test_report_rejects_unknown_analysis_arm(summaries, tmp_path):
    from plot_copula_comparison import run
    matched, full, baseline, paper_no_be = summaries
    path = matched/'copula_summary.csv'
    frame = pd.read_csv(path)
    extra = frame[frame.arm=='spacy_chat'].copy()
    extra['arm'] = 'unknown_parser'
    pd.concat([frame, extra]).to_csv(path, index=False)
    with pytest.raises(ValueError, match='coverage'):
        run(matched, full, baseline, paper_no_be, tmp_path/'report')
