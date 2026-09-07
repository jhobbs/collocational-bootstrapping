import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


ENGLISH_UD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGLISH_UD_DIR))

from fit_zipf import (  # noqa: E402
    calculate_rank_proportions,
    find_optimal_z,
    get_top_verbs,
    predict_zipf,
)


def test_get_top_verbs_filters_non_ascii_and_preserves_first_seen_ties():
    pairs = [
        ("s1", "later"),
        ("s2", "first"),
        ("s3", "later"),
        ("s4", "first"),
        ("s5", "can't"),
        ("s6", "re-do"),
        ("s7", "naïve"),
        ("s8", "two words"),
    ]

    assert get_top_verbs(pairs, n=10) == ["later", "first", "can't", "re-do"]


def test_rank_proportions_preserve_subject_ties_and_average_available_verbs():
    pairs = [
        ("b", "run"),
        ("a", "run"),
        ("b", "run"),
        ("a", "run"),
        ("c", "run"),
        ("x", "jump"),
        ("x", "jump"),
        ("x", "jump"),
        ("y", "jump"),
    ]

    averages, ranked = calculate_rank_proportions(pairs, ["run", "jump"])

    assert ranked[["verb", "subject", "frequency", "rank", "total"]].to_dict(
        "records"
    ) == [
        {"verb": "run", "subject": "b", "frequency": 2, "rank": 1, "total": 5},
        {"verb": "run", "subject": "a", "frequency": 2, "rank": 2, "total": 5},
        {"verb": "run", "subject": "c", "frequency": 1, "rank": 3, "total": 5},
        {"verb": "jump", "subject": "x", "frequency": 3, "rank": 1, "total": 4},
        {"verb": "jump", "subject": "y", "frequency": 1, "rank": 2, "total": 4},
    ]
    np.testing.assert_allclose(
        averages["average_proportion"], [0.575, 0.325, 0.2], rtol=0, atol=1e-15
    )
    assert averages["num_verbs"].tolist() == [2, 2, 1]
    assert np.isnan(averages.loc[2, "sd_proportion"])


def test_predict_zipf_normalizes_over_supplied_ranks():
    predicted = predict_zipf(np.array([1, 2, 4]), 1.0)

    np.testing.assert_allclose(predicted, [4 / 7, 2 / 7, 1 / 7])
    assert predicted.sum() == pytest.approx(1.0)


def test_find_optimal_z_renormalizes_actual_curve_and_uses_full_grid():
    rank_averages = pd.DataFrame(
        {"rank": [1, 2], "average_proportion": [1.5, 0.5]}
    )

    alpha, mse, search = find_optimal_z(rank_averages)

    assert alpha == pytest.approx(1.58)
    assert mse == pytest.approx(4.1667843752183973e-07)
    assert len(search) == 291
    assert search.iloc[0]["z"] == pytest.approx(0.1)
    assert search.iloc[-1]["z"] == pytest.approx(3.0)


def test_find_optimal_z_keeps_first_minimum_on_ties():
    rank_averages = pd.DataFrame({"rank": [1], "average_proportion": [7.0]})

    alpha, mse, _ = find_optimal_z(rank_averages)

    assert alpha == 0.1
    assert mse == 0.0


def _write_cli_fixture(tmp_path):
    pairs = []
    for verb_index in range(10):
        for subject_index in range(10):
            pairs.append(
                {
                    "subject_lemma": f"s{subject_index}",
                    "verb_lemma": f"verb{chr(97 + verb_index)}",
                    "target_child_age_months": 1,
                }
            )
    pairs.extend(
        {
            "subject_lemma": f"young{i}",
            "verb_lemma": "talk",
            "target_child_age_months": 13,
        }
        for i in range(99)
    )
    pairs.extend(
        {
            "subject_lemma": f"middle{i % 5}",
            "verb_lemma": "say",
            "target_child_age_months": 25,
        }
        for i in range(100)
    )
    pairs.extend(
        [
            {
                "subject_lemma": "boundary",
                "verb_lemma": "exist",
                "target_child_age_months": 96,
            },
            {
                "subject_lemma": "nan",
                "verb_lemma": "exist",
                "target_child_age_months": 1,
            },
            {
                "subject_lemma": "negative",
                "verb_lemma": "exist",
                "target_child_age_months": -1,
            },
            {
                "subject_lemma": "old",
                "verb_lemma": "exist",
                "target_child_age_months": 97,
            },
            {
                "subject_lemma": "unknown",
                "verb_lemma": "exist",
                "target_child_age_months": np.nan,
            },
        ]
    )
    utterances = pd.DataFrame(
        {
            "target_child_age_months": [1, 13, 25, 37, 96, 97, np.nan, -1],
            "included_in_cds": [True, True, True, False, True, True, True, True],
            "corpus": ["demo"] * 8,
            "transcript": [f"t{i}" for i in range(8)],
        }
    )
    pairs_path = tmp_path / "pairs.csv"
    utterances_path = tmp_path / "utterances.csv"
    metadata_path = tmp_path / "metadata.json"
    pd.DataFrame(pairs).to_csv(pairs_path, index=False)
    utterances.to_csv(utterances_path, index=False)
    metadata_path.write_text(
        json.dumps(
            {
                "scope": "Eng-NA",
                "collection": "childes-db-test",
                "annotation_scheme": "UD",
                "note": "retained in copied metadata",
            }
        )
    )
    return pairs_path, utterances_path, metadata_path


def test_cli_emits_complete_summary_statuses_and_metadata(tmp_path):
    pairs_path, utterances_path, metadata_path = _write_cli_fixture(tmp_path)
    output = tmp_path / "results"

    result = subprocess.run(
        [
            sys.executable,
            str(ENGLISH_UD_DIR / "fit_zipf.py"),
            "--pairs",
            str(pairs_path),
            "--utterances",
            str(utterances_path),
            "--metadata",
            str(metadata_path),
            "--output",
            str(output),
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    summary = pd.read_csv(output / "english_ud_summary.csv")
    assert summary["age_group"].tolist() == [
        "overall",
        "0-12mo",
        "12-24mo",
        "24-36mo",
        "36-48mo",
        "48-60mo",
        "60-72mo",
        "72-84mo",
        "84-96mo",
    ]
    by_label = summary.set_index("age_group")
    assert by_label.loc["overall", "n_utterances"] == 5
    assert by_label.loc["overall", "n_pairs"] == 302
    ranked = pd.read_csv(
        output / "english_ud_all_verbs_ranked_overall.csv", keep_default_na=False
    )
    assert "nan" in set(ranked["subject"])
    assert by_label.loc["overall", "status"] == "ok"
    assert by_label.loc["0-12mo", "status"] == "ok"
    assert by_label.loc["12-24mo", "status"] == "insufficient_pairs"
    assert by_label.loc["24-36mo", "status"] == "insufficient_verbs"
    assert by_label.loc["36-48mo", "status"] == "no_data"
    assert by_label.loc["84-96mo", "n_pairs"] == 0
    assert set(summary["scope"]) == {"Eng-NA"}
    assert set(summary["collection"]) == {"childes-db-test"}
    assert set(summary["annotation_scheme"]) == {"UD"}

    alpha_by_age = pd.read_csv(output / "english_ud_alpha_by_age.csv")
    assert alpha_by_age["age_group"].tolist() == summary["age_group"].tolist()[1:]
    assert (output / "english_ud_alpha_trajectory.png").is_file()
    for label in ("overall", "0-12mo"):
        for stem in (
            "rank_averages",
            "all_verbs_ranked",
            "mse_search",
            "actual_vs_predicted",
            "top_verbs",
        ):
            assert (output / f"english_ud_{stem}_{label}.csv").is_file()

    fit_metadata = json.loads((output / "fit_metadata.json").read_text())
    assert fit_metadata["note"] == "retained in copied metadata"
    assert fit_metadata["fitting_settings"]["age_filter_overall"] == "finite age <= 96"
    assert fit_metadata["exclusion_counts"]["pairs_age_over_96"] == 1
    assert fit_metadata["exclusion_counts"]["pairs_missing_or_nonfinite_age"] == 1
    assert fit_metadata["exclusion_counts"]["utterances_not_cds"] == 1


def test_cli_refuses_nonempty_output_directory(tmp_path):
    pairs_path, utterances_path, metadata_path = _write_cli_fixture(tmp_path)
    output = tmp_path / "results"
    output.mkdir()
    (output / "keep.txt").write_text("do not replace")

    result = subprocess.run(
        [
            sys.executable,
            str(ENGLISH_UD_DIR / "fit_zipf.py"),
            "--pairs",
            str(pairs_path),
            "--utterances",
            str(utterances_path),
            "--metadata",
            str(metadata_path),
            "--output",
            str(output),
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert (output / "keep.txt").read_text() == "do not replace"
