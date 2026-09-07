#!/usr/bin/env python3
"""Fit the repository's original Zipf model to pre-extracted CHILDES pairs."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


TOP_VERBS = 100
AGE_GROUPS = [
    (0, 12, "0-12mo"),
    (12, 24, "12-24mo"),
    (24, 36, "24-36mo"),
    (36, 48, "36-48mo"),
    (48, 60, "48-60mo"),
    (60, 72, "60-72mo"),
    (72, 84, "72-84mo"),
    (84, 96, "84-96mo"),
]
SUMMARY_COLUMNS = [
    "age_group",
    "alpha",
    "mse",
    "n_utterances",
    "n_pairs",
    "n_unique_subjects",
    "n_unique_verbs",
    "status",
    "scope",
    "collection",
    "annotation_scheme",
]
_ASCII_VERB = re.compile(r"^[a-zA-Z'-]+$")


def get_top_verbs(pairs: Iterable[tuple[str, str]], n: int = TOP_VERBS) -> list[str]:
    """Return the most frequent ASCII verbs, retaining first-seen order on ties."""
    verb_counts: dict[str, int] = defaultdict(int)
    for _subject, verb in pairs:
        verb_counts[verb] += 1
    filtered = {
        verb: count
        for verb, count in verb_counts.items()
        if isinstance(verb, str) and _ASCII_VERB.match(verb)
    }
    sorted_verbs = sorted(filtered.items(), key=lambda item: item[1], reverse=True)
    return [verb for verb, _count in sorted_verbs[:n]]


def calculate_rank_proportions(
    pairs: Iterable[tuple[str, str]], top_verbs: Sequence[str]
) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """Calculate subject proportions and their mean at each within-verb rank."""
    verb_subject_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    top_verb_set = set(top_verbs)
    for subject, verb in pairs:
        if verb in top_verb_set:
            verb_subject_counts[verb][subject] += 1

    ranked_rows = []
    for verb in top_verbs:
        subjects = verb_subject_counts[verb]
        if not subjects:
            continue
        total = sum(subjects.values())
        sorted_subjects = sorted(subjects.items(), key=lambda item: item[1], reverse=True)
        for rank, (subject, frequency) in enumerate(sorted_subjects, start=1):
            ranked_rows.append(
                {
                    "verb": verb,
                    "subject": subject,
                    "frequency": frequency,
                    "rank": rank,
                    "total": total,
                    "proportion": frequency / total,
                }
            )

    if not ranked_rows:
        return None, None

    combined = pd.DataFrame(ranked_rows)
    averages = (
        combined.groupby("rank")
        .agg(
            average_proportion=("proportion", "mean"),
            num_verbs=("proportion", "count"),
            sd_proportion=("proportion", "std"),
        )
        .reset_index()
    )
    return averages, combined


def predict_zipf(ranks: np.ndarray, z: float) -> np.ndarray:
    """Return a Zipf distribution normalized over the supplied ranks."""
    predicted = 1.0 / np.power(ranks, z)
    return predicted / predicted.sum()


def find_optimal_z(
    rank_averages: pd.DataFrame | None,
    z_min: float = 0.1,
    z_max: float = 3.0,
    z_step: float = 0.01,
) -> tuple[float | None, float | None, pd.DataFrame | None]:
    """Search the original inclusive grid and retain its first MSE minimum."""
    if rank_averages is None or len(rank_averages) == 0:
        return None, None, None

    ranks = rank_averages["rank"].values
    actual = rank_averages["average_proportion"].values
    actual_normalized = actual / actual.sum()

    best_z = z_min
    best_mse = float("inf")
    mse_rows = []
    for z in np.arange(z_min, z_max + z_step, z_step):
        predicted = predict_zipf(ranks, z)
        mse = np.mean((actual_normalized - predicted) ** 2)
        mse_rows.append({"z": z, "mse": mse})
        if mse < best_mse:
            best_mse = mse
            best_z = z
    return best_z, best_mse, pd.DataFrame(mse_rows)


def _require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {', '.join(missing)}")


def _parse_cds(value: object) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "t", "yes", "y", "1"}:
            return True
        if normalized in {"false", "f", "no", "n", "0"}:
            return False
    raise ValueError(f"Invalid included_in_cds value: {value!r}")


def _pair_tuples(frame: pd.DataFrame) -> list[tuple[str, str]]:
    return list(frame[["subject_lemma", "verb_lemma"]].itertuples(index=False, name=None))


def _top_verbs_table(pairs: list[tuple[str, str]], top_verbs: list[str]) -> pd.DataFrame:
    counts: dict[str, int] = defaultdict(int)
    for _subject, verb in pairs:
        counts[verb] += 1
    return pd.DataFrame(
        [
            {"rank": rank, "verb": verb, "frequency": counts[verb]}
            for rank, verb in enumerate(top_verbs, start=1)
        ]
    )


def _fit_scope(
    pairs_frame: pd.DataFrame,
    n_utterances: int,
    label: str,
    output: Path,
    metadata_fields: dict[str, object],
    apply_age_thresholds: bool,
) -> dict[str, object]:
    pairs = _pair_tuples(pairs_frame)
    row: dict[str, object] = {
        "age_group": label,
        "alpha": np.nan,
        "mse": np.nan,
        "n_utterances": n_utterances,
        "n_pairs": len(pairs),
        "n_unique_subjects": pairs_frame["subject_lemma"].nunique(dropna=True),
        "n_unique_verbs": pairs_frame["verb_lemma"].nunique(dropna=True),
        "status": "ok",
        **metadata_fields,
    }

    if n_utterances == 0:
        row["status"] = "no_data"
        return row
    if not pairs:
        row["status"] = "insufficient_pairs"
        return row
    if apply_age_thresholds and len(pairs) < 100:
        row["status"] = "insufficient_pairs"
        return row

    top_verbs = get_top_verbs(pairs, TOP_VERBS)
    if (apply_age_thresholds and len(top_verbs) < 10) or not top_verbs:
        row["status"] = "insufficient_verbs"
        return row

    rank_averages, ranked = calculate_rank_proportions(pairs, top_verbs)
    if rank_averages is None or ranked is None:
        row["status"] = "insufficient_ranks"
        return row
    if apply_age_thresholds and len(rank_averages) < 5:
        row["status"] = "insufficient_ranks"
        return row

    alpha, mse, mse_search = find_optimal_z(rank_averages)
    assert alpha is not None and mse is not None and mse_search is not None
    actual = rank_averages["average_proportion"].values
    actual_normalized = actual / actual.sum()
    predicted = predict_zipf(rank_averages["rank"].values, alpha)
    comparison = pd.DataFrame(
        {
            "rank": rank_averages["rank"].values,
            "actual_frequency": actual_normalized,
            "predicted_frequency": predicted,
            "squared_error": (actual_normalized - predicted) ** 2,
            "num_verbs": rank_averages["num_verbs"].values,
        }
    )

    rank_averages.to_csv(output / f"english_ud_rank_averages_{label}.csv", index=False)
    ranked.to_csv(output / f"english_ud_all_verbs_ranked_{label}.csv", index=False)
    mse_search.to_csv(output / f"english_ud_mse_search_{label}.csv", index=False)
    comparison.to_csv(
        output / f"english_ud_actual_vs_predicted_{label}.csv", index=False
    )
    _top_verbs_table(pairs, top_verbs).to_csv(
        output / f"english_ud_top_verbs_{label}.csv", index=False
    )
    row["alpha"] = alpha
    row["mse"] = mse
    return row


def _plot_trajectory(summary: pd.DataFrame, output: Path) -> None:
    ages = summary[summary["age_group"] != "overall"].copy()
    figure, axis = plt.subplots(figsize=(6, 3.5))
    valid = ages[ages["status"] == "ok"]
    if len(valid):
        positions = [ages.index.get_loc(index) for index in valid.index]
        axis.plot(positions, valid["alpha"], marker="o", color="#3182bd")
    axis.set_xticks(np.arange(len(ages)))
    axis.set_xticklabels([label.replace("mo", "") for label in ages["age_group"]])
    axis.set_xlabel("Child age group (months)")
    axis.set_ylabel("Zipf parameter (alpha)")
    axis.spines[["top", "right"]].set_visible(False)
    figure.tight_layout()
    figure.savefig(output / "english_ud_alpha_trajectory.png", dpi=300)
    plt.close(figure)


def _summarize_utterances(path: Path) -> tuple[dict[str, int], dict[str, int]]:
    counts = {"overall": 0, **{label: 0 for _start, _end, label in AGE_GROUPS}}
    exclusions = {
        "utterances_total": 0,
        "utterances_not_cds": 0,
        "utterances_missing_or_nonfinite_age": 0,
        "utterances_age_over_96": 0,
        "utterances_outside_age_bins_but_in_overall": 0,
    }
    columns = ["target_child_age_months", "included_in_cds", "corpus", "transcript"]
    try:
        chunks = pd.read_csv(
            path,
            usecols=columns,
            keep_default_na=False,
            dtype=str,
            chunksize=250_000,
        )
    except ValueError as error:
        raise ValueError(f"utterance CSV does not have the required columns: {error}") from error
    for chunk in chunks:
        exclusions["utterances_total"] += len(chunk)
        ages = pd.to_numeric(chunk["target_child_age_months"], errors="coerce")
        finite = np.isfinite(ages)
        cds = chunk["included_in_cds"].map(_parse_cds)
        exclusions["utterances_not_cds"] += int((~cds).sum())
        exclusions["utterances_missing_or_nonfinite_age"] += int((cds & ~finite).sum())
        exclusions["utterances_age_over_96"] += int((cds & finite & (ages > 96)).sum())
        overall = cds & finite & (ages <= 96)
        exclusions["utterances_outside_age_bins_but_in_overall"] += int(
            (overall & ((ages < 0) | (ages >= 96))).sum()
        )
        counts["overall"] += int(overall.sum())
        for age_min, age_max, label in AGE_GROUPS:
            counts[label] += int(
                (cds & finite & (ages >= age_min) & (ages < age_max)).sum()
            )
    return counts, exclusions


def fit_files(pairs_path: Path, utterances_path: Path, metadata_path: Path, output: Path) -> None:
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Output directory exists and is nonempty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    pairs = pd.read_csv(
        pairs_path,
        usecols=["subject_lemma", "verb_lemma", "target_child_age_months"],
        keep_default_na=False,
        dtype=str,
    )
    _require_columns(
        pairs,
        {"subject_lemma", "verb_lemma", "target_child_age_months"},
        "pair CSV",
    )
    with metadata_path.open(encoding="utf-8") as handle:
        metadata = json.load(handle)
    if not isinstance(metadata, dict):
        raise ValueError("metadata JSON must contain an object")
    required_metadata = {"scope", "collection", "annotation_scheme"}
    missing_metadata = sorted(required_metadata - set(metadata))
    if missing_metadata:
        raise ValueError(
            "metadata is missing required fields: " + ", ".join(missing_metadata)
        )
    metadata_fields = {key: metadata[key] for key in sorted(required_metadata)}

    pairs = pairs.copy()
    pair_ages = pd.to_numeric(pairs["target_child_age_months"], errors="coerce")
    pair_finite = np.isfinite(pair_ages)
    valid_lemmas = (pairs["subject_lemma"] != "") & (pairs["verb_lemma"] != "")

    pairs["target_child_age_months"] = pair_ages
    utterance_counts, utterance_exclusions = _summarize_utterances(utterances_path)
    overall_pairs = pairs[pair_finite & (pair_ages <= 96) & valid_lemmas].copy()

    rows = [
        _fit_scope(
            overall_pairs,
            utterance_counts["overall"],
            "overall",
            output,
            metadata_fields,
            apply_age_thresholds=False,
        )
    ]
    for age_min, age_max, label in AGE_GROUPS:
        pair_group = pairs[
            pair_finite
            & valid_lemmas
            & (pair_ages >= age_min)
            & (pair_ages < age_max)
        ].copy()
        rows.append(
            _fit_scope(
                pair_group,
                utterance_counts[label],
                label,
                output,
                metadata_fields,
                apply_age_thresholds=True,
            )
        )

    summary = pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
    summary.to_csv(output / "english_ud_summary.csv", index=False)
    summary.iloc[1:].to_csv(output / "english_ud_alpha_by_age.csv", index=False)
    _plot_trajectory(summary, output)

    fit_metadata = dict(metadata)
    fit_metadata["fitting_settings"] = {
        "top_verbs": TOP_VERBS,
        "ascii_verb_pattern": _ASCII_VERB.pattern,
        "age_filter_overall": "finite age <= 96",
        "age_bins": "[a,b) for 0 through 96 months",
        "minimum_pairs_per_age_bin": 100,
        "minimum_top_verbs_per_age_bin": 10,
        "minimum_ranks_per_age_bin": 5,
        "z_min": 0.1,
        "z_max": 3.0,
        "z_step": 0.01,
        "tie_order": "stable first-seen order",
        "rank_average": "mean over verbs present at each rank",
        "rank_curve_renormalized_before_mse": True,
    }
    fit_metadata["exclusion_counts"] = {
        "pairs_missing_or_nonfinite_age": int((~pair_finite).sum()),
        "pairs_age_over_96": int((pair_finite & (pair_ages > 96)).sum()),
        "pairs_missing_lemma": int((~valid_lemmas).sum()),
        "pairs_outside_age_bins_but_in_overall": int(
            (pair_finite & (pair_ages <= 96) & ((pair_ages < 0) | (pair_ages >= 96))).sum()
        ),
        **utterance_exclusions,
    }
    fit_metadata["inputs"] = {
        "pairs": str(pairs_path.resolve()),
        "utterances": str(utterances_path.resolve()),
        "metadata": str(metadata_path.resolve()),
    }
    with (output / "fit_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(fit_metadata, handle, indent=2, sort_keys=True)
        handle.write("\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", required=True, type=Path)
    parser.add_argument("--utterances", required=True, type=Path)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    fit_files(args.pairs, args.utterances, args.metadata, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
