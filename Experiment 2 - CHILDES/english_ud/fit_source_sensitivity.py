#!/usr/bin/env python3
"""Fit matched Eng-NA sensitivity arms after excluding Hall and McCune."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

import pandas as pd

from fit_matched_comparison import ARMS, validate_inputs
from fit_zipf import fit_files


EXCLUDED_CORPORA = frozenset({"Hall", "McCune"})


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def filter_rows(source: Path, destination: Path) -> int:
    count = 0
    with source.open(newline="") as src, destination.open("w", newline="") as dst:
        reader = csv.DictReader(src)
        required = {"collection", "corpus"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"CSV lacks collection/corpus identity: {source}")
        writer = csv.DictWriter(dst, fieldnames=reader.fieldnames)
        writer.writeheader()
        for row in reader:
            if row["collection"] == "Eng-NA" and row["corpus"] not in EXCLUDED_CORPORA:
                writer.writerow(row)
                count += 1
    return count


def rewrite_stage_paths(stage: Path, output: Path) -> None:
    old, new = str(stage.resolve()), str(output.resolve())

    def rewrite(value):
        if isinstance(value, str) and (value == old or value.startswith(old + "/")):
            return new + value[len(old):]
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        return value

    for path in stage.rglob("*.json"):
        path.write_text(json.dumps(rewrite(json.loads(path.read_text())), indent=2, sort_keys=True) + "\n")


def run(matched: Path, spacy_chat: Path, spacy_original: Path, output: Path) -> None:
    if output.exists():
        raise FileExistsError(output)
    validation = validate_inputs(matched, spacy_chat, spacy_original)
    arms = {"ud_chat": matched, "spacy_chat": spacy_chat, "spacy_original": spacy_original}
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="." + output.name + "-", dir=output.parent))
    try:
        inputs = stage / "filtered_inputs"
        inputs = inputs.resolve(); inputs.mkdir()
        utterances = inputs / "english_ud_utterances.csv"
        n_utterances = filter_rows(matched / "english_ud_utterances.csv", utterances)
        summaries, pair_counts, input_provenance = [], {}, {}
        for arm, directory in arms.items():
            source_pairs = directory / "english_ud_subject_verb_pairs.csv"
            pairs = inputs / f"{arm}_subject_verb_pairs.csv"
            pair_counts[arm] = filter_rows(source_pairs, pairs)
            source_meta = directory / "metadata.json"
            input_provenance[arm] = {
                "source_pairs": str(source_pairs.resolve()), "source_pairs_sha256": digest(source_pairs),
                "source_metadata": str(source_meta.resolve()), "source_metadata_sha256": digest(source_meta),
            }
            metadata_path = inputs / f"{arm}_metadata.json"
            metadata_path.write_text(json.dumps({
                "scope": "matched_eng_na_excluding_hall_mccune", "collection": "Eng-NA",
                "annotation_scheme": ARMS[arm], "arm": arm,
                "excluded_corpora": sorted(EXCLUDED_CORPORA),
                "matching_provenance": validation["matched_metadata"]["matching_provenance"],
                "full_matched_utterances": str((matched / "english_ud_utterances.csv").resolve()),
                "full_matched_utterances_sha256": validation["utterances_sha256"],
                "filtered_utterances": n_utterances, "filtered_pairs": pair_counts[arm],
                "source_input": input_provenance[arm],
            }, indent=2) + "\n")
            fit_dir = stage / arm
            fit_files(pairs, utterances, metadata_path, fit_dir)
            summary = pd.read_csv(fit_dir / "english_ud_summary.csv")
            summary.insert(0, "arm", arm)
            summaries.append(summary)
        combined = pd.concat(summaries, ignore_index=True)
        combined.to_csv(stage / "sensitivity_summary.csv", index=False)
        alpha = combined.pivot(index="age_group", columns="arm", values="alpha")
        order = ["overall", "0-12mo", "12-24mo", "24-36mo", "36-48mo",
                 "48-60mo", "60-72mo", "72-84mo", "84-96mo"]
        alpha.reindex(order).reset_index().to_csv(stage / "sensitivity_alpha_table.csv", index=False)
        (stage / "metadata.json").write_text(json.dumps({
            "scope": "matched_eng_na_excluding_hall_mccune", "collection": "Eng-NA",
            "excluded_corpora": sorted(EXCLUDED_CORPORA), "n_utterances": n_utterances,
            "pair_counts": pair_counts, "full_input_provenance": input_provenance,
            "matching_provenance": validation["matched_metadata"]["matching_provenance"],
            "full_matched_utterances_sha256": validation["utterances_sha256"],
            "filter_rule": "collection == 'Eng-NA' and corpus not in {'Hall', 'McCune'}; full CSV rows retained",
        }, indent=2) + "\n")
        rewrite_stage_paths(stage, output)
        stage.rename(output)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matched", required=True, type=Path)
    parser.add_argument("--spacy-chat", required=True, type=Path)
    parser.add_argument("--spacy-original", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    run(args.matched, args.spacy_chat, args.spacy_original, args.output)


if __name__ == "__main__":
    main()
