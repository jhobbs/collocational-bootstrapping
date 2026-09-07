"""Compare saved spaCy and English UD Zipf analysis artifacts."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


AGE_GROUPS = [
    "overall", "0-12mo", "12-24mo", "24-36mo", "36-48mo",
    "48-60mo", "60-72mo", "72-84mo", "84-96mo",
]
SUMMARY_COLUMNS = [
    "age_group", "alpha", "mse", "n_utterances", "n_pairs",
    "n_unique_subjects", "n_unique_verbs", "scope", "collection",
    "annotation_scheme",
]
CURVE_COLUMNS = [
    "rank", "actual_frequency", "predicted_frequency", "squared_error", "num_verbs",
]
PAIR_REQUIRED = [
    "collection", "corpus", "transcript", "utterance_id", "subject_surface",
    "verb_surface", "subject_lemma", "verb_lemma", "verb_pos",
]
PAIR_CONTEXT = ["collection", "corpus", "transcript", "utterance_id"]
SCOPE_NOTE = (
    "The saved baseline scope is repository_english / "
    "mixed_english_unknown_manifest, and no corpus/transcript manifest is "
    "available; exact scope equivalence cannot be asserted even when the UD "
    "input supplies a repository scope."
)


def _require_columns(frame: pd.DataFrame, required: Iterable[str], label: str) -> None:
    missing = sorted(set(required) - set(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {', '.join(missing)}")


def _ensure_empty_output(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise FileExistsError(f"Output directory is not empty: {path}")
    path.mkdir(parents=True, exist_ok=True)


def _artifact_path(root: Path, source: str, kind: str, age_group: str) -> Path:
    if source == "spacy":
        if age_group == "overall":
            return root / "complete_dataset_96mos" / f"{kind}_96mos.csv"
        return root / "age_groups_complete_96mos" / f"{kind}_{age_group}.csv"
    return root / f"english_ud_{kind}_{age_group}.csv"


def _read_summary(path: Path, label: str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    _require_columns(frame, SUMMARY_COLUMNS, label)
    if frame["age_group"].duplicated().any():
        duplicates = frame.loc[frame["age_group"].duplicated(), "age_group"].tolist()
        raise ValueError(f"{label} has duplicate age groups: {duplicates}")
    return frame


def _prefixed_summary(frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
    keep = SUMMARY_COLUMNS + (["status"] if "status" in frame.columns else [])
    return frame[keep].rename(
        columns={name: f"{prefix}_{name}" for name in keep if name != "age_group"}
    )


def _read_ranked(path: Path, label: str) -> pd.DataFrame:
    frame = pd.read_csv(path, keep_default_na=False)
    _require_columns(frame, ["verb", "subject", "frequency", "rank"], label)
    return frame


def _ordered_verbs(ranked: pd.DataFrame, limit: int = 100) -> list[str]:
    """Recover top-verb order encoded by contiguous blocks in fitter output."""
    return ranked.loc[~ranked["verb"].duplicated(), "verb"].astype(str).head(limit).tolist()


def _scope_label(summary: pd.DataFrame, prefix: str) -> str:
    scopes = summary[f"{prefix}_scope"].dropna().astype(str).unique().tolist()
    collections = summary[f"{prefix}_collection"].dropna().astype(str).unique().tolist()
    return f"{prefix}: scope={','.join(scopes) or 'unknown'}; collection={','.join(collections) or 'unknown'}"


def _source_labels(frame: pd.DataFrame) -> dict[str, list[str]]:
    return {
        "scopes": frame["scope"].dropna().astype(str).unique().tolist(),
        "collections": frame["collection"].dropna().astype(str).unique().tolist(),
        "annotation_schemes": frame["annotation_scheme"].dropna().astype(str).unique().tolist(),
    }


def _plot_age_trajectory(summary: pd.DataFrame, output: Path) -> None:
    midpoint = {group: 6 + index * 12 for index, group in enumerate(AGE_GROUPS[1:])}
    fig, axis = plt.subplots(figsize=(10, 6))
    age = summary[summary["age_group"].isin(midpoint)].copy()
    age["age_midpoint_months"] = age["age_group"].map(midpoint)
    for prefix, name in [("spacy", "spaCy baseline"), ("ud", "English UD")]:
        values = age.dropna(subset=[f"{prefix}_alpha"])
        axis.plot(values["age_midpoint_months"], values[f"{prefix}_alpha"], marker="o", label=name)
    axis.set(xlabel="Age-bin midpoint (months)", ylabel="Fitted alpha")
    axis.set_xticks(list(midpoint.values()))
    axis.grid(alpha=0.25)
    if axis.lines:
        axis.legend()
    axis.set_title(
        "Zipf alpha by age\n" + _scope_label(summary, "spacy") + "\n" + _scope_label(summary, "ud"),
        fontsize=9,
    )
    fig.tight_layout()
    fig.savefig(output, dpi=180)
    plt.close(fig)


def _plot_curve_overlay(curves: pd.DataFrame, summary: pd.DataFrame, output: Path) -> None:
    fig, axes = plt.subplots(3, 3, figsize=(15, 13), squeeze=False)
    for age_group, axis in zip(AGE_GROUPS, axes.flat):
        group = curves[curves["age_group"] == age_group]
        plotted = False
        for prefix, color, name in [("spacy", "tab:blue", "spaCy"), ("ud", "tab:orange", "UD")]:
            actual = group.dropna(subset=[f"{prefix}_actual_frequency"])
            predicted = group.dropna(subset=[f"{prefix}_predicted_frequency"])
            if not actual.empty:
                axis.plot(actual["rank"], actual[f"{prefix}_actual_frequency"], color=color, label=f"{name} empirical")
                plotted = True
            if not predicted.empty:
                axis.plot(predicted["rank"], predicted[f"{prefix}_predicted_frequency"], color=color, linestyle="--", label=f"{name} predicted")
                plotted = True
        axis.set(title=age_group, xlabel="Subject rank", ylabel="Frequency")
        axis.grid(alpha=0.2)
        if plotted:
            axis.legend(fontsize=7)
        else:
            axis.text(0.5, 0.5, "curve unavailable", ha="center", va="center")
    fig.suptitle(
        "Empirical and predicted rank-frequency curves\n"
        + _scope_label(summary, "spacy") + "\n" + _scope_label(summary, "ud"),
        fontsize=10,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(output, dpi=180)
    plt.close(fig)


def _canonical(value):
    return None if pd.isna(value) else value


def _tuple_key(row: pd.Series, columns: list[str]) -> tuple:
    return tuple(_canonical(row[column]) for column in columns)


def compare_aligned_pairs(spacy_pairs: pd.DataFrame, ud_pairs: pd.DataFrame) -> pd.DataFrame:
    """Classify multiplicities in genuinely transcript-aligned pair tables."""
    _require_columns(spacy_pairs, PAIR_REQUIRED, "spaCy pair table")
    _require_columns(ud_pairs, PAIR_REQUIRED, "UD pair table")
    for label, frame in [("spaCy", spacy_pairs), ("UD", ud_pairs)]:
        if frame[PAIR_CONTEXT].isna().any().any():
            raise ValueError(f"{label} pair table has null alignment keys")

    declared_spaces = [set(frame["index_space"]) if "index_space" in frame else set()
                       for frame in [spacy_pairs, ud_pairs]]
    shared_space = (declared_spaces[0] == declared_spaces[1]
                    and len(declared_spaces[0]) == 1
                    and all(isinstance(value, str) and value.strip() for value in declared_spaces[0]))
    use_indices = shared_space and all(
        column in spacy_pairs.columns and column in ud_pairs.columns
        and all(pd.to_numeric(frame[column], errors="coerce").gt(0).all()
                for frame in [spacy_pairs, ud_pairs])
        for column in ["subject_index", "verb_index"]
    )
    optional = ["subject_index", "verb_index"] if use_indices else []
    fields = ["subject_surface", "verb_surface", "subject_lemma", "verb_lemma", "verb_pos"]
    full_columns = PAIR_CONTEXT + optional + fields
    dep_columns = PAIR_CONTEXT + optional if use_indices else PAIR_CONTEXT + ["subject_surface", "verb_surface"]
    spacy_counts = Counter(_tuple_key(row, full_columns) for _, row in spacy_pairs.iterrows())
    ud_counts = Counter(_tuple_key(row, full_columns) for _, row in ud_pairs.iterrows())
    indexes = {name: index for index, name in enumerate(full_columns)}
    records: list[dict] = []

    def dependency(key: tuple) -> tuple:
        return tuple(key[indexes[name]] for name in dep_columns)

    # Clitics or retracing fragments can share one source-word coordinate.
    # Preserve exact overlap but do not invent a lemma/POS alignment between
    # distinct pairs when either parser has multiple analyses at that position.
    ambiguous_dependencies = set()
    for counts in [spacy_counts, ud_counts]:
        distinct = Counter(dependency(key) for key in counts)
        ambiguous_dependencies.update(dep for dep, n in distinct.items() if n > 1)

    def add(classification: str, count: int, spacy_key=None, ud_key=None) -> None:
        if count <= 0:
            return
        reference = spacy_key if spacy_key is not None else ud_key
        record = {name: reference[indexes[name]] for name in PAIR_CONTEXT}
        record.update(classification=classification, multiplicity=count,
                      alignment_basis="token_indices" if use_indices else "surface_forms",
                      alignment_index_space=next(iter(declared_spaces[0])) if use_indices else None,
                      dependency_alignment_ambiguous=dependency(reference) in ambiguous_dependencies)
        for prefix, key in [("spacy", spacy_key), ("ud", ud_key)]:
            for name in fields + optional:
                record[f"{prefix}_{name}"] = key[indexes[name]] if key is not None else None
        records.append(record)

    for key in list(spacy_counts):
        common = min(spacy_counts[key], ud_counts[key])
        add("both", common, key, key)
        spacy_counts[key] -= common
        ud_counts[key] -= common

    spacy_groups, ud_groups = defaultdict(list), defaultdict(list)
    for key, count in spacy_counts.items():
        if count:
            spacy_groups[dependency(key)].append(key)
    for key, count in ud_counts.items():
        if count:
            ud_groups[dependency(key)].append(key)
    for dep in spacy_groups.keys() & ud_groups.keys():
        if dep in ambiguous_dependencies:
            continue
        for left in spacy_groups[dep]:
            for right in ud_groups[dep]:
                available = min(spacy_counts[left], ud_counts[right])
                if not available:
                    continue
                left_pos, right_pos = left[indexes["verb_pos"]], right[indexes["verb_pos"]]
                left_lemmas = (left[indexes["subject_lemma"]], left[indexes["verb_lemma"]])
                right_lemmas = (right[indexes["subject_lemma"]], right[indexes["verb_lemma"]])
                if left_pos != right_pos and "AUX" in {left_pos, right_pos}:
                    classification = "same_dependency_different_verb_aux_treatment"
                elif left_lemmas != right_lemmas:
                    classification = "same_dependency_different_lemma"
                else:
                    continue
                add(classification, available, left, right)
                spacy_counts[left] -= available
                ud_counts[right] -= available
    for key, count in spacy_counts.items():
        add("spacy_only", count, spacy_key=key)
    for key, count in ud_counts.items():
        add("ud_only", count, ud_key=key)
    columns = PAIR_CONTEXT + ["classification", "multiplicity", "alignment_basis", "alignment_index_space", "dependency_alignment_ambiguous"]
    columns += [f"{prefix}_{name}" for prefix in ["spacy", "ud"] for name in fields + optional]
    return pd.DataFrame(records, columns=columns)


def compare_outputs(baseline_dir, ud_dir, output_dir, spacy_pairs_path=None, ud_pairs_path=None) -> pd.DataFrame:
    """Write comparison tables, plots, and provenance metadata."""
    baseline_dir, ud_dir, output_dir = map(Path, (baseline_dir, ud_dir, output_dir))
    _ensure_empty_output(output_dir)
    baseline_summary = _read_summary(baseline_dir / "baseline_summary.csv", "baseline summary")
    ud_summary = _read_summary(ud_dir / "english_ud_summary.csv", "UD summary")
    summary = pd.DataFrame({"age_group": AGE_GROUPS})
    summary = summary.merge(_prefixed_summary(baseline_summary, "spacy"), how="left")
    summary = summary.merge(_prefixed_summary(ud_summary, "ud"), how="left")
    summary["alpha_delta"] = summary["ud_alpha"] - summary["spacy_alpha"]
    summary["scope_comparable"] = False
    summary["scope_note"] = SCOPE_NOTE

    diagnostics, top_rows, subject_frames, curve_frames = [], [], [], []
    for age_group in AGE_GROUPS:
        if age_group not in set(baseline_summary["age_group"]):
            diagnostics.append(f"{age_group}: spaCy summary row missing")
        if age_group not in set(ud_summary["age_group"]):
            diagnostics.append(f"{age_group}: UD summary row missing")
    overlap = {}
    for age_group in AGE_GROUPS:
        ranked, verbs = {}, {}
        for source, root in [("spacy", baseline_dir), ("ud", ud_dir)]:
            path = _artifact_path(root, source, "all_verbs_ranked", age_group)
            if not path.exists():
                name = "UD" if source == "ud" else "spaCy"
                diagnostics.append(f"{age_group}: {name} ranked artifact missing: {path}")
                ranked[source] = verbs[source] = None
                continue
            ranked[source] = _read_ranked(path, f"{source} ranked table ({age_group})")
            verbs[source] = _ordered_verbs(ranked[source])
            top_rows.extend(
                {"age_group": age_group, "source": source, "position": i, "verb": verb}
                for i, verb in enumerate(verbs[source], 1)
            )
        if verbs["spacy"] is None or verbs["ud"] is None:
            overlap[age_group] = (np.nan, np.nan, np.nan)
        else:
            denominator = min(100, len(verbs["spacy"]), len(verbs["ud"]))
            count = len(set(verbs["spacy"]) & set(verbs["ud"]))
            overlap[age_group] = (count, denominator, count / denominator if denominator else np.nan)

        if ranked["spacy"] is not None:
            baseline_top = verbs["spacy"][:10]
            left = (ranked["spacy"].loc[ranked["spacy"]["verb"].astype(str).isin(baseline_top)]
                    .groupby(["verb", "subject"], as_index=False)["frequency"].sum()
                    .rename(columns={"frequency": "spacy_frequency"}))
            if ranked["ud"] is None:
                counts = left.assign(ud_frequency=np.nan)
            else:
                right = (ranked["ud"].loc[ranked["ud"]["verb"].astype(str).isin(baseline_top)]
                         .groupby(["verb", "subject"], as_index=False)["frequency"].sum()
                         .rename(columns={"frequency": "ud_frequency"}))
                counts = left.merge(right, on=["verb", "subject"], how="outer")
                counts[["spacy_frequency", "ud_frequency"]] = counts[["spacy_frequency", "ud_frequency"]].fillna(0)
            order = {verb: i for i, verb in enumerate(baseline_top, 1)}
            counts.insert(0, "baseline_top_position", counts["verb"].map(order))
            counts.insert(0, "age_group", age_group)
            counts["spacy_table_available"] = True
            counts["ud_table_available"] = ranked["ud"] is not None
            subject_frames.append(counts)

        curves = {}
        for source, root in [("spacy", baseline_dir), ("ud", ud_dir)]:
            path = _artifact_path(root, source, "actual_vs_predicted", age_group)
            if not path.exists():
                name = "UD" if source == "ud" else "spaCy"
                diagnostics.append(f"{age_group}: {name} curve artifact missing: {path}")
                curves[source] = None
                continue
            curve = pd.read_csv(path)
            _require_columns(curve, CURVE_COLUMNS, f"{source} curve table ({age_group})")
            curves[source] = curve[CURVE_COLUMNS].rename(
                columns={name: f"{source}_{name}" for name in CURVE_COLUMNS[1:]}
            )
        if curves["spacy"] is not None or curves["ud"] is not None:
            if curves["spacy"] is None:
                aligned = curves["ud"].assign(**{f"spacy_{c}": np.nan for c in CURVE_COLUMNS[1:]})
            elif curves["ud"] is None:
                aligned = curves["spacy"].assign(**{f"ud_{c}": np.nan for c in CURVE_COLUMNS[1:]})
            else:
                aligned = curves["spacy"].merge(curves["ud"], on="rank", how="outer")
            aligned.insert(0, "age_group", age_group)
            curve_frames.append(aligned)

    overlap_frame = pd.DataFrame.from_dict(overlap, orient="index", columns=[
        "top_100_overlap_count", "top_100_overlap_denominator", "top_100_overlap_fraction",
    ]).rename_axis("age_group").reset_index()
    summary = summary.merge(overlap_frame, on="age_group", how="left")
    summary.to_csv(output_dir / "english_ud_vs_spacy.csv", index=False)
    pd.DataFrame(top_rows, columns=["age_group", "source", "position", "verb"]).to_csv(
        output_dir / "top_verbs_comparison.csv", index=False
    )
    subject_columns = ["age_group", "baseline_top_position", "verb", "subject", "spacy_frequency",
                       "ud_frequency", "spacy_table_available", "ud_table_available"]
    subjects = pd.concat(subject_frames, ignore_index=True) if subject_frames else pd.DataFrame(columns=subject_columns)
    subjects[subject_columns].to_csv(output_dir / "baseline_top10_subject_counts.csv", index=False)
    empty_curve_columns = ["age_group", "rank"] + [
        f"{source}_{name}" for source in ["spacy", "ud"] for name in CURVE_COLUMNS[1:]
    ]
    aligned_curves = pd.concat(curve_frames, ignore_index=True) if curve_frames else pd.DataFrame(columns=empty_curve_columns)
    aligned_curves.to_csv(output_dir / "aligned_curves.csv", index=False)
    _plot_age_trajectory(summary, output_dir / "alpha_age_trajectory.png")
    _plot_curve_overlay(aligned_curves, summary, output_dir / "curve_overlay.png")

    pair_metadata = {
        "requested": bool(spacy_pairs_path or ud_pairs_path),
        "limitation": "Historical aggregate pair files cannot be transcript-aligned; both aligned tables must be supplied explicitly.",
    }
    if bool(spacy_pairs_path) != bool(ud_pairs_path):
        raise ValueError("Both --spacy-pairs and --ud-pairs are required together")
    if spacy_pairs_path and ud_pairs_path:
        spacy_pairs, ud_pairs = (pd.read_csv(spacy_pairs_path, keep_default_na=False),
                                pd.read_csv(ud_pairs_path, keep_default_na=False))
        pair_diagnostics = compare_aligned_pairs(spacy_pairs, ud_pairs)
        pair_diagnostics.to_csv(output_dir / "same_transcript_pair_diagnostics.csv", index=False)
        pair_diagnostics.groupby("classification", as_index=False)["multiplicity"].sum().to_csv(
            output_dir / "same_transcript_pair_summary.csv", index=False
        )
        pair_metadata.update(spacy_pairs=str(Path(spacy_pairs_path).resolve()),
                             ud_pairs=str(Path(ud_pairs_path).resolve()),
                             total_spacy_pairs=len(spacy_pairs), total_ud_pairs=len(ud_pairs),
                             spacy_collections=sorted(spacy_pairs["collection"].unique().tolist()),
                             ud_collections=sorted(ud_pairs["collection"].unique().tolist()),
                             alignment_index_spaces=sorted(pair_diagnostics["alignment_index_space"].dropna().unique().tolist()),
                             scope_note="These collections describe the supplied parser sample; its scope is independent of the aggregate fits above.")

    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "inputs": {"baseline": str(baseline_dir.resolve()), "ud": str(ud_dir.resolve())},
        "source_labels": {
            "spacy": _source_labels(baseline_summary),
            "ud": _source_labels(ud_summary),
        },
        "scope_comparable": False,
        "scope_note": SCOPE_NOTE,
        "top_100_overlap_denominator": "min(100, number of ordered verbs available in each ranked table)",
        "diagnostics": diagnostics,
        "same_transcript_pair_diagnostic": pair_metadata,
        "limitations": [SCOPE_NOTE, "Alpha and MSE differences combine annotation and corpus-scope differences.",
                        "Missing summary bins or artifacts are represented as null and described in diagnostics."],
    }
    (output_dir / "comparison_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--ud", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--spacy-pairs", type=Path)
    parser.add_argument("--ud-pairs", type=Path)
    return parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    compare_outputs(args.baseline, args.ud, args.output, args.spacy_pairs, args.ud_pairs)


if __name__ == "__main__":
    main()
