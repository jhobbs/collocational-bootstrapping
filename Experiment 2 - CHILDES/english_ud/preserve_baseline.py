#!/usr/bin/env python3
"""Preserve and numerically verify the repository's original spaCy baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from fit_zipf import calculate_rank_proportions, find_optimal_z, get_top_verbs


DATASETS = [("overall", "complete_dataset_96mos", "96mos")] + [
    (label, "age_groups_complete_96mos", label)
    for label in (
        "0-12mo",
        "12-24mo",
        "24-36mo",
        "36-48mo",
        "48-60mo",
        "60-72mo",
        "72-84mo",
        "84-96mo",
    )
]
RAW_UTTERANCE_SOURCE = Path(
    "/home/jason/imagining-syntax/childes_full_utterances_20251211_120050.csv"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _max_float_difference(left: pd.Series, right: pd.Series) -> float:
    left_values = pd.to_numeric(left, errors="coerce").to_numpy(dtype=float)
    right_values = pd.to_numeric(right, errors="coerce").to_numpy(dtype=float)
    if left_values.shape != right_values.shape:
        return float("inf")
    if len(left_values) == 0:
        return 0.0
    differences = np.abs(left_values - right_values)
    both_nan = np.isnan(left_values) & np.isnan(right_values)
    differences[both_nan] = 0.0
    if np.isnan(differences).any():
        return float("inf")
    return float(differences.max())


def _verify_dataset(root: Path, label: str, directory: str, suffix: str) -> dict:
    dataset_dir = root / directory
    pair_path = dataset_dir / f"nsubj_verb_pairs_{suffix}.csv"
    pairs = pd.read_csv(
        pair_path,
        usecols=["subject_lemma", "verb_lemma"],
        dtype={"subject_lemma": str, "verb_lemma": str},
        keep_default_na=False,
    )
    pair_iterator = pairs[["subject_lemma", "verb_lemma"]].itertuples(
        index=False, name=None
    )
    top_verbs = get_top_verbs(pair_iterator)
    rank_averages, ranked = calculate_rank_proportions(
        pairs[["subject_lemma", "verb_lemma"]].itertuples(index=False, name=None),
        top_verbs,
    )
    if rank_averages is None or ranked is None:
        raise RuntimeError(f"No rank data computed for {label}")
    alpha, mse, search = find_optimal_z(rank_averages)
    assert alpha is not None and mse is not None and search is not None

    stored_ranked = pd.read_csv(
        dataset_dir / f"all_verbs_ranked_{suffix}.csv",
        dtype={"verb": str, "subject": str},
        keep_default_na=False,
    )
    stored_averages = pd.read_csv(dataset_dir / f"rank_averages_{suffix}.csv")
    stored_search = pd.read_csv(dataset_dir / f"mse_search_{suffix}.csv")
    errors: list[str] = []

    if len(ranked) != len(stored_ranked):
        errors.append("ranked row count differs")
    else:
        for column in ("verb", "subject"):
            if ranked[column].tolist() != stored_ranked[column].tolist():
                errors.append(f"ranked {column} order differs")
        for column in ("frequency", "rank", "total", "proportion"):
            if _max_float_difference(ranked[column], stored_ranked[column]) > 1e-14:
                errors.append(f"ranked {column} differs")

    rank_average_max_differences = {}
    if len(rank_averages) != len(stored_averages):
        errors.append("rank-average row count differs")
    else:
        for column in ("rank", "average_proportion", "num_verbs", "sd_proportion"):
            difference = _max_float_difference(rank_averages[column], stored_averages[column])
            rank_average_max_differences[column] = difference
            if difference > 1e-14:
                errors.append(f"rank-average {column} differs")

    search_max_differences = {}
    if len(search) != len(stored_search):
        errors.append("MSE-search row count differs")
    else:
        for column in ("z", "mse"):
            difference = _max_float_difference(search[column], stored_search[column])
            search_max_differences[column] = difference
            if difference > 1e-14:
                errors.append(f"MSE-search {column} differs")
    stored_minimum = stored_search.iloc[stored_search["mse"].argmin()]
    if abs(float(stored_minimum["z"]) - alpha) > 1e-14:
        errors.append("optimal alpha differs")
    if abs(float(stored_minimum["mse"]) - mse) > 1e-14:
        errors.append("optimal MSE differs")

    return {
        "age_group": label,
        "n_pairs": len(pairs),
        "n_top_verbs": len(top_verbs),
        "n_ranks": len(rank_averages),
        "computed_alpha": alpha,
        "stored_alpha": float(stored_minimum["z"]),
        "computed_mse": mse,
        "stored_mse": float(stored_minimum["mse"]),
        "rank_average_max_abs_difference": rank_average_max_differences,
        "mse_search_max_abs_difference": search_max_differences,
        "passed": not errors,
        "errors": errors,
    }


def _parse_overall_utterances(summary_path: Path) -> int:
    match = re.search(
        r"^Total utterances:\s*([0-9,]+)\s*$",
        summary_path.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    if not match:
        raise RuntimeError(f"Could not find utterance count in {summary_path}")
    return int(match.group(1).replace(",", ""))


def _baseline_summary(root: Path, verification: list[dict]) -> pd.DataFrame:
    common = {
        "status": "ok",
        "scope": "repository_english",
        "collection": "mixed_english_unknown_manifest",
        "annotation_scheme": "spaCy",
    }
    overall_dir = root / "complete_dataset_96mos"
    overall_pairs = pd.read_csv(
        overall_dir / "nsubj_verb_pairs_96mos.csv",
        usecols=["subject_lemma", "verb_lemma"],
        dtype={"subject_lemma": str, "verb_lemma": str},
        keep_default_na=False,
    )
    overall_verification = verification[0]
    rows = [
        {
            "age_group": "overall",
            "alpha": overall_verification["stored_alpha"],
            "mse": overall_verification["stored_mse"],
            "n_utterances": _parse_overall_utterances(
                overall_dir / "summary_96mos.txt"
            ),
            "n_pairs": len(overall_pairs),
            "n_unique_subjects": overall_pairs["subject_lemma"].nunique(),
            "n_unique_verbs": overall_pairs["verb_lemma"].nunique(),
            **common,
        }
    ]
    ages = pd.read_csv(root / "age_groups_complete_96mos" / "summary_96mos.csv")
    age_verification = {item["age_group"]: item for item in verification[1:]}
    for record in ages.to_dict("records"):
        verified = age_verification[record["age_group"]]
        rows.append(
            {
                "age_group": record["age_group"],
                "alpha": verified["stored_alpha"],
                "mse": verified["stored_mse"],
                "n_utterances": int(record["n_utterances"]),
                "n_pairs": int(record["n_pairs"]),
                "n_unique_subjects": int(record["n_unique_subjects"]),
                "n_unique_verbs": int(record["n_unique_verbs"]),
                **common,
            }
        )
    return pd.DataFrame(rows)


def _verify_existing(source: Path, output: Path) -> None:
    manifest_path = output / "provenance_manifest.json"
    if not manifest_path.is_file():
        raise FileExistsError(f"Existing target has no provenance manifest: {output}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures = []
    for item in manifest["files"]:
        copied = output / item["path"]
        if not copied.is_file() or sha256_file(copied) != item["sha256"]:
            failures.append(item["path"])
        source_relative = item.get("source_relative_path")
        if source_relative:
            original = source / source_relative
            if not original.is_file() or sha256_file(original) != item["sha256"]:
                failures.append(f"source:{source_relative}")
    if failures:
        raise RuntimeError("Baseline hash verification failed: " + ", ".join(failures))


def preserve_baseline(source: Path, output: Path) -> None:
    source = source.resolve()
    output = output.resolve()
    if output.exists():
        _verify_existing(source, output)
        return
    for directory in ("complete_dataset_96mos", "age_groups_complete_96mos"):
        if not (source / directory).is_dir():
            raise FileNotFoundError(f"Missing baseline directory: {source / directory}")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        for directory in ("complete_dataset_96mos", "age_groups_complete_96mos"):
            shutil.copytree(source / directory, temporary / directory)

        verification = [
            _verify_dataset(temporary, label, directory, suffix)
            for label, directory, suffix in DATASETS
        ]
        report = {
            "schema_version": 1,
            "method": "Recomputed original stable-tie top-100 rank curves and full alpha grid from each saved pair table",
            "tolerance": 1e-14,
            "all_passed": all(item["passed"] for item in verification),
            "datasets": verification,
        }
        (temporary / "baseline_verification.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        if not report["all_passed"]:
            failures = [item["age_group"] for item in verification if not item["passed"]]
            raise RuntimeError("Numerical baseline verification failed: " + ", ".join(failures))

        _baseline_summary(temporary, verification).to_csv(
            temporary / "baseline_summary.csv", index=False
        )
        raw_source = {
            "available": RAW_UTTERANCE_SOURCE.is_file(),
            "path": str(RAW_UTTERANCE_SOURCE),
            "size_bytes": RAW_UTTERANCE_SOURCE.stat().st_size
            if RAW_UTTERANCE_SOURCE.is_file()
            else None,
            "parsed_for_preservation": False,
        }
        provenance = {
            "schema_version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_directory": str(source),
            "copied_directories": [
                "complete_dataset_96mos",
                "age_groups_complete_96mos",
            ],
            "raw_utterance_source": raw_source,
            "corpus_manifest": {
                "available": False,
                "reason": "The original baseline did not save a corpus-level source manifest.",
            },
            "files": [],
        }
        source_files = {
            path.relative_to(temporary).as_posix()
            for directory in provenance["copied_directories"]
            for path in (temporary / directory).rglob("*")
            if path.is_file()
        }
        for path in sorted(item for item in temporary.rglob("*") if item.is_file()):
            relative = path.relative_to(temporary).as_posix()
            entry = {"path": relative, "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}
            if relative in source_files:
                entry["source_relative_path"] = relative
            provenance["files"].append(entry)
        (temporary / "provenance_manifest.json").write_text(
            json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
        )
        temporary.rename(output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    preserve_baseline(args.source, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
