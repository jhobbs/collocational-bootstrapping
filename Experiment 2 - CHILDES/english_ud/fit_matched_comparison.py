#!/usr/bin/env python3
"""Fit and compare three parsers on identical matched historical utterances."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from fit_zipf import AGE_GROUPS, fit_files


ARMS = {
    "ud_chat": "Existing Universal Dependencies annotations in selected CHAT transcripts",
    "spacy_chat": "spaCy dependency parsing of selected CHAT surface text",
    "spacy_original": "spaCy dependency parsing of matched historical original-gloss text",
}


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _metadata(directory: Path) -> dict:
    with (directory / "metadata.json").open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"metadata JSON must be an object: {directory}")
    return value


def validate_inputs(matched: Path, spacy_chat: Path, spacy_original: Path) -> dict:
    """Require all arms to describe the exact matched utterance population."""
    matched_meta = _metadata(matched)
    chat_meta, original_meta = _metadata(spacy_chat), _metadata(spacy_original)
    utterances_hash = _digest(matched / "english_ud_utterances.csv")
    expected_count = matched_meta.get("counts", {}).get("n_cds_utterances")
    if not isinstance(expected_count, int):
        raise ValueError("matched count is missing counts.n_cds_utterances")
    if "matching_provenance" not in matched_meta:
        raise ValueError("matched metadata is missing matching_provenance")
    for label, metadata, text_column in (
        ("spacy_chat", chat_meta, "text"),
        ("spacy_original", original_meta, "original_text"),
    ):
        input_record = metadata.get("input", {})
        supplied_hash = metadata.get("input_utterances_sha256", input_record.get("utterances_sha256"))
        supplied_text_column = metadata.get("source_text_column", input_record.get("text_column"))
        if supplied_hash != utterances_hash:
            raise ValueError(f"hash mismatch for {label}")
        if metadata.get("counts", {}).get("n_included_utterances") != expected_count:
            raise ValueError(f"count mismatch for {label}")
        if supplied_text_column != text_column:
            raise ValueError(f"source text column mismatch for {label}")
    return {"matched_metadata": matched_meta, "utterances_sha256": utterances_hash,
            "n_utterances": expected_count}


def _filter_csv(source: Path, destination: Path, collection: str = "Eng-NA") -> int:
    count = 0
    with source.open(newline="") as src, destination.open("w", newline="") as dst:
        reader = csv.DictReader(src)
        if not reader.fieldnames or "collection" not in reader.fieldnames:
            raise ValueError(f"CSV is missing collection: {source}")
        writer = csv.DictWriter(dst, fieldnames=reader.fieldnames)
        writer.writeheader()
        for row in reader:
            if row["collection"] == collection:
                writer.writerow(row)
                count += 1
    return count


def prepare_eng_na_inputs(matched: Path, arm_dirs: dict[str, Path], destination: Path) -> dict:
    """Stream the common utterances and each arm's pairs into an Eng-NA subset."""
    destination.mkdir(parents=True)
    utterances = destination / "english_ud_utterances.csv"
    n_utterances = _filter_csv(matched / "english_ud_utterances.csv", utterances)
    pairs = {}
    pair_counts = {}
    for arm, directory in arm_dirs.items():
        path = destination / f"{arm}_subject_verb_pairs.csv"
        pair_counts[arm] = _filter_csv(directory / "english_ud_subject_verb_pairs.csv", path)
        pairs[arm] = path
    return {"utterances": utterances, "pairs": pairs, "n_utterances": n_utterances,
            "pair_counts": pair_counts}


def _write_fit_metadata(path: Path, source: dict, scope: str, collection: str,
                        arm: str, validation: dict) -> None:
    metadata = {
        "scope": scope, "collection": collection, "annotation_scheme": ARMS[arm],
        "matching_provenance": validation["matched_metadata"]["matching_provenance"],
        "matched_utterances_sha256": validation["utterances_sha256"],
        "matched_input_count": validation["n_utterances"],
        "arm": arm, "arm_source_metadata": source,
        "comparability_note": "All three arms use the same matched utterances. The saved historical spaCy baseline is plotted as a reference from a different dataset and is not scope-comparable.",
    }
    path.write_text(json.dumps(metadata, indent=2) + "\n")


def _age_label(value: str) -> str | None:
    try:
        age = float(value)
    except (TypeError, ValueError):
        return None
    for start, end, label in AGE_GROUPS:
        if start <= age < end:
            return label
    return None


def _coverage(matched: Path, arm_dirs: dict[str, Path], output: Path) -> None:
    counts = Counter()
    with (matched / "english_ud_utterances.csv").open() as stream:
        for row in csv.DictReader(stream):
            labels = ["overall"]
            age = _age_label(row.get("target_child_age_months", ""))
            if age:
                labels.append(age)
            for label in labels:
                counts[(row["collection"], label, "matched_utterances")] += 1
    for arm, directory in arm_dirs.items():
        with (directory / "english_ud_subject_verb_pairs.csv").open() as stream:
            for row in csv.DictReader(stream):
                labels = ["overall"]
                age = _age_label(row.get("target_child_age_months", ""))
                if age:
                    labels.append(age)
                for label in labels:
                    counts[(row["collection"], label, arm + "_pairs")] += 1
    fields = ["collection", "age_group", "matched_utterances"] + [a + "_pairs" for a in ARMS]
    keys = sorted({(collection, age) for collection, age, _metric in counts})
    with output.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for collection, age in keys:
            writer.writerow({"collection": collection, "age_group": age,
                             **{field: counts[(collection, age, field)] for field in fields[2:]}})


def _comparison_plot(summary: pd.DataFrame, baseline: Path, output: Path) -> None:
    baseline_frame = pd.read_csv(baseline / "baseline_summary.csv")
    order = [label for _start, _end, label in AGE_GROUPS]
    figure, axes = plt.subplots(1, 2, figsize=(12, 4), sharey=True)
    colors = {"ud_chat": "#1b9e77", "spacy_chat": "#d95f02", "spacy_original": "#7570b3"}
    labels = {"ud_chat": "UD: CHAT annotations", "spacy_chat": "spaCy: same CHAT text",
              "spacy_original": "spaCy: original CSV text"}
    for axis, (scope, title) in zip(axes, (("matched_eng_na", "Matched Eng-NA"),
                                                  ("matched_repository_english", "Matched broader English"))):
        for arm in ARMS:
            rows = summary[(summary.scope == scope) & (summary.arm == arm)].set_index("age_group").reindex(order)
            axis.plot(range(len(order)), rows["alpha"], marker="o", label=labels[arm], color=colors[arm])
        old = baseline_frame.set_index("age_group").reindex(order)
        axis.plot(range(len(order)), old["alpha"], linestyle="--", color="#555555",
                  label="saved spaCy baseline (different historical data)")
        axis.set_title(title); axis.set_xticks(range(len(order)))
        axis.set_xticklabels([x.replace("mo", "") for x in order], rotation=45, ha="right")
        axis.set_xlabel("Child age group (months)"); axis.spines[["top", "right"]].set_visible(False)
    axes[0].set_ylabel("Zipf parameter (alpha)")
    handles, labels = axes[1].get_legend_handles_labels()
    figure.legend(handles, labels, loc="lower center", ncol=2, frameon=False)
    figure.tight_layout(rect=(0, .18, 1, 1))
    figure.savefig(output, dpi=300)
    plt.close(figure)


def _rewrite_staged_json_paths(stage: Path, destination: Path) -> None:
    """Translate only generated absolute paths from staging to their published location."""
    old, new = str(stage.resolve()), str(destination.resolve())

    def rewrite(value):
        if isinstance(value, str) and (value == old or value.startswith(old + "/")):
            return new + value[len(old):]
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        return value

    for path in stage.rglob("*.json"):
        value = json.loads(path.read_text())
        path.write_text(json.dumps(rewrite(value), indent=2, sort_keys=True) + "\n")


def run(matched: Path, spacy_chat: Path, spacy_original: Path,
        baseline: Path, output: Path) -> None:
    if output.exists():
        raise FileExistsError(output)
    validation = validate_inputs(matched, spacy_chat, spacy_original)
    arms = {"ud_chat": matched, "spacy_chat": spacy_chat, "spacy_original": spacy_original}
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="." + output.name + "-", dir=output.parent))
    try:
        subset = prepare_eng_na_inputs(matched, arms, stage / "inputs_eng_na")
        summaries = []
        source_metadata = {arm: _metadata(directory) for arm, directory in arms.items()}
        collection = validation["matched_metadata"].get("collection", "")
        for scope, utterances, pair_paths, scope_collection in (
            ("matched_repository_english", matched / "english_ud_utterances.csv",
             {a: d / "english_ud_subject_verb_pairs.csv" for a, d in arms.items()}, collection),
            ("matched_eng_na", subset["utterances"], subset["pairs"], "Eng-NA"),
        ):
            for arm in ARMS:
                fit_dir = stage / scope / arm
                meta_path = stage / f"{scope}_{arm}_metadata.json"
                _write_fit_metadata(meta_path, source_metadata[arm], scope, scope_collection, arm, validation)
                fit_files(pair_paths[arm], utterances, meta_path, fit_dir)
                frame = pd.read_csv(fit_dir / "english_ud_summary.csv")
                frame.insert(0, "arm", arm); summaries.append(frame)
        summary = pd.concat(summaries, ignore_index=True)
        summary.to_csv(stage / "matched_comparison_summary.csv", index=False)
        _coverage(matched, arms, stage / "coverage_by_collection_age.csv")
        _comparison_plot(summary, baseline, stage / "matched_alpha_comparison.png")
        (stage / "metadata.json").write_text(json.dumps({
            "scope": "matched_comparison", "n_fits": 6, "rows_per_fit": 9,
            "input_validation": {k: v for k, v in validation.items() if k != "matched_metadata"},
            "matching_provenance": validation["matched_metadata"]["matching_provenance"],
            "baseline_reference": str(baseline.resolve()),
            "baseline_note": "Reference only: the baseline uses different historical data and is not treated as scope-comparable.",
        }, indent=2) + "\n")
        _rewrite_staged_json_paths(stage, output)
        stage.rename(output)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matched", required=True, type=Path)
    parser.add_argument("--spacy-chat", required=True, type=Path)
    parser.add_argument("--spacy-original", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    run(args.matched, args.spacy_chat, args.spacy_original, args.baseline, args.output)


if __name__ == "__main__":
    main()
