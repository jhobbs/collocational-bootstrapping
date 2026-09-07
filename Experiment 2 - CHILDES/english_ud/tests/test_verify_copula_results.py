import csv
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verify_copula_results import VerificationError, verify_results


AGE_BINS = [(start, start + 12, f"{start}-{start + 12}mo") for start in range(0, 96, 12)]
AGES = ["overall", *(label for _low, _high, label in AGE_BINS)]
SETTINGS = {"z_min": 0.1, "z_max": 0.2, "z_step": 0.1, "top_verbs": 100}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_csv(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_curve(folder, age, no_be):
    folder.mkdir(parents=True, exist_ok=True)
    if no_be:
        ranked = [
            {"verb": "go", "subject": "i", "frequency": 3, "rank": 1,
             "total": 4, "proportion": 0.75},
            {"verb": "go", "subject": "you", "frequency": 1, "rank": 2,
             "total": 4, "proportion": 0.25},
        ]
        averages = [
            {"rank": 1, "average_proportion": 0.75, "num_verbs": 1,
             "sd_proportion": ""},
            {"rank": 2, "average_proportion": 0.25, "num_verbs": 1,
             "sd_proportion": ""},
        ]
        top = [{"rank": 1, "verb": "go", "frequency": 4}]
        actual = [0.75, 0.25]
    else:
        ranked = [
            {"verb": "be", "subject": "i", "frequency": 2, "rank": 1,
             "total": 2, "proportion": 1.0},
            {"verb": "go", "subject": "i", "frequency": 3, "rank": 1,
             "total": 4, "proportion": 0.75},
            {"verb": "go", "subject": "you", "frequency": 1, "rank": 2,
             "total": 4, "proportion": 0.25},
        ]
        averages = [
            {"rank": 1, "average_proportion": 0.875, "num_verbs": 2,
             "sd_proportion": 0.1767766952966369},
            {"rank": 2, "average_proportion": 0.25, "num_verbs": 1,
             "sd_proportion": ""},
        ]
        top = [
            {"rank": 1, "verb": "go", "frequency": 4},
            {"rank": 2, "verb": "be", "frequency": 2},
        ]
        actual = [7 / 9, 2 / 9]
    predictions = {
        0.1: [0.5173217448321853, 0.48267825516781476],
        0.2: [0.5346019613807635, 0.46539803861923645],
    }
    search = []
    for z, predicted in predictions.items():
        mse = sum((left - right) ** 2 for left, right in zip(actual, predicted)) / 2
        search.append({"z": z, "mse": mse})
    best = min(search, key=lambda row: row["mse"])
    predicted = predictions[best["z"]]
    comparison = [
        {"rank": rank, "actual_frequency": left, "predicted_frequency": right,
         "squared_error": (left - right) ** 2, "num_verbs": averages[rank - 1]["num_verbs"]}
        for rank, (left, right) in enumerate(zip(actual, predicted), 1)
    ]
    pd.DataFrame(ranked).to_csv(folder / f"english_ud_all_verbs_ranked_{age}.csv", index=False)
    pd.DataFrame(averages).to_csv(folder / f"english_ud_rank_averages_{age}.csv", index=False)
    pd.DataFrame(search).to_csv(folder / f"english_ud_mse_search_{age}.csv", index=False)
    pd.DataFrame(comparison).to_csv(folder / f"english_ud_actual_vs_predicted_{age}.csv", index=False)
    pd.DataFrame(top).to_csv(folder / f"english_ud_top_verbs_{age}.csv", index=False)
    return best["z"], best["mse"], 4 if no_be else 6


def make_fit_bundle(root, dataset):
    root.mkdir()
    pair_sources = {}
    provenance = {}
    for arm in ("ud_strict", "ud_copula"):
        source = root / f"{arm}_source.csv"
        write_csv(source, [
            {"collection": "Eng-NA", "verb_lemma": "be"},
            {"collection": "Eng-NA", "verb_lemma": "go"},
        ])
        metadata = root / f"{arm}_source.json"
        metadata.write_text(json.dumps({"arm": arm}) + "\n")
        pair_sources[arm] = source
        provenance[arm] = {
            "pairs": str(source.resolve()), "pairs_sha256": digest(source),
            "metadata": str(metadata.resolve()), "metadata_sha256": digest(metadata),
        }
    rows = []
    for arm in pair_sources:
        for condition in ("all", "no_be"):
            pair_input = root / f"{arm}_{condition}_input.csv"
            input_rows = []
            for low, _high, _label in AGE_BINS:
                if condition == "all":
                    input_rows += [{"collection": "Eng-NA", "subject_lemma": "i",
                                    "verb_lemma": "be", "target_child_age_months": low + 1}] * 2
                input_rows += [{"collection": "Eng-NA", "subject_lemma": "i",
                                "verb_lemma": "go", "target_child_age_months": low + 1}] * 4
            write_csv(pair_input, input_rows)
            folder = root / "fits" / "broader_english" / arm / condition
            fit_rows = []
            for age in AGES:
                alpha, mse, per_bin_pairs = write_curve(folder, age, condition == "no_be")
                n_pairs = len(input_rows) if age == "overall" else per_bin_pairs
                fit_rows.append({
                    "dataset": dataset, "arm": arm, "be_filter": condition,
                    "scope": "broader_english", "age_group": age, "alpha": alpha,
                    "mse": mse, "n_utterances": 80 if age == "overall" else 10,
                    "n_pairs": n_pairs,
                    "status": "ok",
                })
            rows.extend(fit_rows)
            utterances = root / "utterances.csv"
            if not utterances.exists():
                utterance_rows = []
                for low, _high, _label in AGE_BINS:
                    utterance_rows += [{"included_in_cds": "True",
                                       "target_child_age_months": low + 1,
                                       "collection": "Eng-NA"}] * 10
                write_csv(utterances, utterance_rows)
            fit_metadata = {
                "inputs": {"pairs": str(pair_input.resolve()),
                           "utterances": str(utterances.resolve()),
                           "metadata": str((root / f"{arm}_source.json").resolve())},
                "fitting_settings": SETTINGS,
                "exclusion_counts": {
                    "pairs_missing_or_nonfinite_age": 0,
                    "pairs_age_over_96": 0,
                    "pairs_missing_lemma": 0,
                    "pairs_outside_age_bins_but_in_overall": 0,
                    "utterances_total": 80,
                    "utterances_not_cds": 0,
                    "utterances_missing_or_nonfinite_age": 0,
                    "utterances_age_over_96": 0,
                    "utterances_outside_age_bins_but_in_overall": 0,
                },
            }
            (folder / "fit_metadata.json").write_text(json.dumps(fit_metadata) + "\n")
    pd.DataFrame(rows).to_csv(root / "copula_summary.csv", index=False)
    (root / "metadata.json").write_text(json.dumps({
        "dataset": dataset, "arms": list(pair_sources), "conditions": ["all", "no_be"],
        "n_fits": 4, "input_provenance": provenance,
    }) + "\n")


def make_paper_bundle(root):
    root.mkdir()
    baseline = root / "baseline"
    baseline.mkdir()
    baseline_summary = baseline / "baseline_summary.csv"
    write_csv(baseline_summary, [{"age_group": "overall", "n_pairs": 6}])
    source = baseline / "pairs.csv"
    source_rows = []
    filtered_rows = []
    for low, _high, _label in AGE_BINS:
        source_rows += [{"verb_lemma": "be"}] * 2 + [{"verb_lemma": "go"}] * 4
        filtered_rows += [{"verb_lemma": "go"}] * 4
    write_csv(source, source_rows)
    filtered = root / "filtered_pairs"
    filtered.mkdir()
    fits = root / "fits"
    summary_rows = []
    sources = []
    for age in AGES:
        age_rows = filtered_rows if age == "overall" else [{"verb_lemma": "go"}] * 4
        original = len(source_rows) if age == "overall" else 6
        removed = 16 if age == "overall" else 2
        write_csv(filtered / f"{age}.csv", age_rows)
        alpha, mse, per_bin_pairs = write_curve(fits, age, True)
        remaining = len(filtered_rows) if age == "overall" else per_bin_pairs
        summary_rows.append({"age_group": age, "alpha": alpha, "mse": mse,
                             "n_utterances": 80 if age == "overall" else 10,
                             "n_pairs": remaining, "status": "ok"})
        age_source = source if age == "overall" else baseline / f"{age}.csv"
        if age != "overall":
            write_csv(age_source, [{"verb_lemma": "be"}] * 2 + [{"verb_lemma": "go"}] * 4)
        sources.append({"age_group": age, "source_pairs": str(age_source.resolve()),
                        "source_pairs_sha256": digest(age_source), "original_pairs": original,
                        "removed_be_pairs": removed, "remaining_pairs": remaining,
                        "n_utterances": 80 if age == "overall" else 10})
    pd.DataFrame(summary_rows).to_csv(root / "summary.csv", index=False)
    (root / "metadata.json").write_text(json.dumps({
        "source_baseline": str(baseline.resolve()),
        "baseline_summary_sha256": digest(baseline_summary),
        "sources": sources,
        "fitting_settings": SETTINGS,
    }) + "\n")


def fixture_tree(tmp_path):
    matched, full, paper = (tmp_path / name for name in ("matched", "full", "paper"))
    make_fit_bundle(matched, "matched")
    make_fit_bundle(full, "earlier_talkbank")
    make_paper_bundle(paper)
    return matched, full, paper


def test_tampered_mse_grid_is_rejected_atomically(tmp_path):
    matched, full, paper = fixture_tree(tmp_path)
    search = matched / "fits/broader_english/ud_strict/all/english_ud_mse_search_overall.csv"
    frame = pd.read_csv(search)
    frame.loc[0, "mse"] += 0.01
    frame.to_csv(search, index=False)
    output = tmp_path / "verification"

    with pytest.raises(VerificationError, match="MSE"):
        verify_results(matched, full, paper, output)

    assert not output.exists()


def test_tampered_no_be_input_is_rejected_atomically(tmp_path):
    matched, full, paper = fixture_tree(tmp_path)
    filtered = full / "ud_strict_no_be_input.csv"
    frame = pd.read_csv(filtered, keep_default_na=False)
    frame.loc[0, "verb_lemma"] = "be"
    frame.to_csv(filtered, index=False)
    output = tmp_path / "verification"

    with pytest.raises(VerificationError, match="contains be"):
        verify_results(matched, full, paper, output)

    assert not output.exists()
