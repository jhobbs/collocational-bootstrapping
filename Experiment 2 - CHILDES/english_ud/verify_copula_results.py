#!/usr/bin/env python3
"""Independently verify saved copula and ``be`` diagnostic results."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_GRID = {"z_min": 0.1, "z_max": 3.0, "z_step": 0.01}
AGE_BINS = tuple((start, start + 12, f"{start}-{start + 12}mo") for start in range(0, 96, 12))
EXPECTED_AGES = ("overall", *(label for _start, _end, label in AGE_BINS))


class VerificationError(ValueError):
    """Raised when a published diagnostic artifact fails verification."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot read JSON {path}: {exc}") from exc
    _require(isinstance(value, dict), f"JSON root is not an object: {path}")
    return value


def _sha256(path: Path, hashes: dict[str, str]) -> str:
    path = path.resolve()
    _require(path.is_file(), f"missing input artifact: {path}")
    key = str(path)
    if key not in hashes:
        with path.open("rb") as stream:
            hashes[key] = hashlib.file_digest(stream, "sha256").hexdigest()
    return hashes[key]


def _read_csv(path: Path, required: set[str]) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path, keep_default_na=False)
    except (OSError, pd.errors.ParserError) as exc:
        raise VerificationError(f"cannot read CSV {path}: {exc}") from exc
    missing = required - set(frame.columns)
    _require(not missing, f"{path} lacks columns: {sorted(missing)}")
    return frame


def _numeric(frame: pd.DataFrame, column: str, path: Path) -> np.ndarray:
    values = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float)
    _require(np.isfinite(values).all(), f"nonfinite {column} in {path}")
    return values


def _close(actual, expected, label: str, *, rtol=1e-10, atol=1e-15) -> None:
    try:
        np.testing.assert_allclose(actual, expected, rtol=rtol, atol=atol)
    except AssertionError as exc:
        raise VerificationError(f"{label} mismatch: {exc}") from exc


def _grid(settings: dict, label: str) -> np.ndarray:
    try:
        low = float(settings["z_min"])
        high = float(settings["z_max"])
        step = float(settings["z_step"])
    except (KeyError, TypeError, ValueError) as exc:
        raise VerificationError(f"invalid fitting grid in {label}") from exc
    _require(step > 0 and high >= low, f"invalid fitting grid in {label}")
    return np.arange(low, high + step / 2, step)


def _verify_curve(folder: Path, row, settings: dict) -> dict:
    age = str(row.age_group)
    ranked_path = folder / f"english_ud_all_verbs_ranked_{age}.csv"
    averages_path = folder / f"english_ud_rank_averages_{age}.csv"
    search_path = folder / f"english_ud_mse_search_{age}.csv"
    comparison_path = folder / f"english_ud_actual_vs_predicted_{age}.csv"
    ranked = _read_csv(ranked_path, {"rank", "frequency", "total", "proportion"})
    averages = _read_csv(averages_path, {"rank", "average_proportion", "num_verbs"})
    search = _read_csv(search_path, {"z", "mse"})
    comparison = _read_csv(
        comparison_path,
        {"rank", "actual_frequency", "predicted_frequency", "squared_error", "num_verbs"},
    )
    _require(len(ranked) > 0 and len(averages) > 0, f"empty rank data in {folder} ({age})")

    frequency = _numeric(ranked, "frequency", ranked_path)
    total = _numeric(ranked, "total", ranked_path)
    _require((total > 0).all(), f"nonpositive verb total in {ranked_path}")
    _close(_numeric(ranked, "proportion", ranked_path), frequency / total,
           f"rank proportions in {ranked_path}", rtol=1e-12)

    ranked_copy = ranked.copy()
    ranked_copy["rank"] = pd.to_numeric(ranked_copy["rank"], errors="coerce")
    ranked_copy["proportion"] = pd.to_numeric(ranked_copy["proportion"], errors="coerce")
    _require(np.isfinite(ranked_copy["rank"]).all(), f"nonfinite rank in {ranked_path}")
    _require(np.isfinite(ranked_copy["proportion"]).all(),
             f"nonfinite proportion in {ranked_path}")
    reconstructed = ranked_copy.groupby("rank", sort=True).proportion.agg(["mean", "count", "std"])
    average_ranks = _numeric(averages, "rank", averages_path)
    _close(average_ranks, reconstructed.index.to_numpy(dtype=float),
           f"average rank labels in {averages_path}", rtol=0, atol=0)
    _close(_numeric(averages, "average_proportion", averages_path), reconstructed["mean"],
           f"rank averages in {averages_path}", rtol=1e-12)
    _close(_numeric(averages, "num_verbs", averages_path), reconstructed["count"],
           f"rank counts in {averages_path}", rtol=0, atol=0)
    if "sd_proportion" in averages.columns:
        saved_sd = pd.to_numeric(averages.sd_proportion, errors="coerce").to_numpy(dtype=float)
        calculated_sd = reconstructed["std"].to_numpy(dtype=float)
        _require(np.array_equal(np.isnan(saved_sd), np.isnan(calculated_sd)),
                 f"rank SD missingness mismatch in {averages_path}")
        finite = np.isfinite(calculated_sd)
        _close(saved_sd[finite], calculated_sd[finite], f"rank SD in {averages_path}", rtol=1e-10)

    ranks = average_ranks
    actual = _numeric(averages, "average_proportion", averages_path)
    _require(actual.sum() > 0, f"zero rank mass in {averages_path}")
    actual = actual / actual.sum()
    grid = _grid(settings, str(folder))
    _require(len(search) == len(grid), f"MSE grid length mismatch in {search_path}")
    _close(_numeric(search, "z", search_path), grid, f"MSE grid values in {search_path}",
           rtol=0, atol=1e-14)
    predicted = ranks[None, :] ** (-grid[:, None])
    predicted /= predicted.sum(axis=1)[:, None]
    errors = ((actual[None, :] - predicted) ** 2).mean(axis=1)
    _close(_numeric(search, "mse", search_path), errors, f"MSE values in {search_path}",
           rtol=1e-10, atol=1e-16)
    best = int(np.argmin(errors))
    _close(float(row.alpha), grid[best], f"best alpha in {folder} ({age})", rtol=0, atol=1e-14)
    _close(float(row.mse), errors[best], f"best MSE in {folder} ({age})", rtol=1e-10, atol=1e-16)

    _close(_numeric(comparison, "rank", comparison_path), ranks,
           f"comparison ranks in {comparison_path}", rtol=0, atol=0)
    _close(_numeric(comparison, "actual_frequency", comparison_path), actual,
           f"actual predictions in {comparison_path}")
    _close(_numeric(comparison, "predicted_frequency", comparison_path), predicted[best],
           f"Zipf predictions in {comparison_path}")
    _close(_numeric(comparison, "squared_error", comparison_path),
           (actual - predicted[best]) ** 2, f"prediction squared errors in {comparison_path}",
           rtol=1e-9, atol=1e-16)
    _close(_numeric(comparison, "num_verbs", comparison_path),
           _numeric(averages, "num_verbs", averages_path),
           f"comparison rank counts in {comparison_path}", rtol=0, atol=0)
    return {
        "directory": str(folder.resolve()), "age_group": age,
        "grid_points": len(grid), "alpha": float(row.alpha),
        "n_ranks": len(ranks), "all_passed": True,
    }


def _check_pair_file(path: Path, expected: int, no_be: bool, eng_na: bool,
                     hashes: dict[str, str]) -> dict:
    columns = ["verb_lemma"] + (["collection"] if eng_na else [])
    count = 0
    try:
        iterator = pd.read_csv(
            path, usecols=columns, dtype=str, keep_default_na=False, chunksize=250_000
        )
        for chunk in iterator:
            lemmas = chunk.verb_lemma.str.strip().str.casefold()
            if no_be:
                _require(not lemmas.eq("be").any(), f"no-be pair file contains be: {path}")
            if eng_na:
                _require(chunk.collection.eq("Eng-NA").all(),
                         f"Eng-NA input contains another collection: {path}")
            count += len(chunk)
    except VerificationError:
        raise
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        raise VerificationError(f"cannot inspect pair file {path}: {exc}") from exc
    _require(count == expected, f"pair count mismatch for {path}: {count} != {expected}")
    _sha256(path, hashes)
    return {"path": str(path.resolve()), "n_pairs": count,
            "zero_be": no_be, "eng_na_only": eng_na}


def _parse_cds(values: pd.Series, path: Path) -> pd.Series:
    normalized = values.astype(str).str.strip().str.casefold()
    mapping = {"true": True, "t": True, "yes": True, "y": True, "1": True,
               "false": False, "f": False, "no": False, "n": False, "0": False}
    invalid = ~normalized.isin(mapping)
    _require(not invalid.any(), f"invalid included_in_cds value in {path}")
    return normalized.map(mapping).astype(bool)


def _summarize_pairs(path: Path, cache: dict[str, tuple[dict, dict, bool]]):
    key = str(path.resolve())
    if key in cache:
        return cache[key]
    counts = {age: 0 for age in EXPECTED_AGES}
    exclusions = {"pairs_missing_or_nonfinite_age": 0, "pairs_age_over_96": 0,
                  "pairs_missing_lemma": 0, "pairs_outside_age_bins_but_in_overall": 0}
    eng_na_only = True
    try:
        chunks = pd.read_csv(path, dtype=str, keep_default_na=False, chunksize=250_000)
        for chunk in chunks:
            required = {"subject_lemma", "verb_lemma", "target_child_age_months"}
            _require(required.issubset(chunk.columns), f"pair columns missing from {path}")
            ages = pd.to_numeric(chunk.target_child_age_months, errors="coerce")
            finite = np.isfinite(ages)
            valid_lemmas = chunk.subject_lemma.ne("") & chunk.verb_lemma.ne("")
            exclusions["pairs_missing_or_nonfinite_age"] += int((~finite).sum())
            exclusions["pairs_age_over_96"] += int((finite & (ages > 96)).sum())
            exclusions["pairs_missing_lemma"] += int((~valid_lemmas).sum())
            overall = finite & (ages <= 96) & valid_lemmas
            exclusions["pairs_outside_age_bins_but_in_overall"] += int(
                (overall & ((ages < 0) | (ages >= 96))).sum()
            )
            counts["overall"] += int(overall.sum())
            for low, high, label in AGE_BINS:
                counts[label] += int((finite & valid_lemmas & (ages >= low) & (ages < high)).sum())
            if "collection" in chunk:
                eng_na_only = eng_na_only and bool(chunk.collection.eq("Eng-NA").all())
            else:
                eng_na_only = False
    except VerificationError:
        raise
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        raise VerificationError(f"cannot summarize pair file {path}: {exc}") from exc
    cache[key] = counts, exclusions, eng_na_only
    return cache[key]


def _summarize_utterances(path: Path, cache: dict[str, tuple[dict, dict, bool]]):
    key = str(path.resolve())
    if key in cache:
        return cache[key]
    counts = {age: 0 for age in EXPECTED_AGES}
    exclusions = {"utterances_total": 0, "utterances_not_cds": 0,
                  "utterances_missing_or_nonfinite_age": 0, "utterances_age_over_96": 0,
                  "utterances_outside_age_bins_but_in_overall": 0}
    eng_na_only = True
    try:
        chunks = pd.read_csv(path, dtype=str, keep_default_na=False, chunksize=250_000)
        for chunk in chunks:
            required = {"target_child_age_months", "included_in_cds"}
            _require(required.issubset(chunk.columns), f"utterance columns missing from {path}")
            exclusions["utterances_total"] += len(chunk)
            ages = pd.to_numeric(chunk.target_child_age_months, errors="coerce")
            finite = np.isfinite(ages)
            cds = _parse_cds(chunk.included_in_cds, path)
            exclusions["utterances_not_cds"] += int((~cds).sum())
            exclusions["utterances_missing_or_nonfinite_age"] += int((cds & ~finite).sum())
            exclusions["utterances_age_over_96"] += int((cds & finite & (ages > 96)).sum())
            overall = cds & finite & (ages <= 96)
            exclusions["utterances_outside_age_bins_but_in_overall"] += int(
                (overall & ((ages < 0) | (ages >= 96))).sum()
            )
            counts["overall"] += int(overall.sum())
            for low, high, label in AGE_BINS:
                counts[label] += int((cds & finite & (ages >= low) & (ages < high)).sum())
            if "collection" in chunk:
                eng_na_only = eng_na_only and bool(chunk.collection.eq("Eng-NA").all())
            else:
                eng_na_only = False
    except VerificationError:
        raise
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        raise VerificationError(f"cannot summarize utterance file {path}: {exc}") from exc
    cache[key] = counts, exclusions, eng_na_only
    return cache[key]


def _top_verbs(folder: Path, age: str) -> set[str]:
    path = folder / f"english_ud_top_verbs_{age}.csv"
    frame = _read_csv(path, {"verb"})
    verbs = frame.verb.astype(str).tolist()
    _require(len(verbs) > 0 and len(set(verbs)) == len(verbs),
             f"empty or duplicate top verbs in {path}")
    return set(verbs)


def _age_key(label: str):
    if label == "overall":
        return (-1, label)
    try:
        return (int(label.split("-", 1)[0]), label)
    except ValueError:
        return (10**9, label)


def _verify_fit_bundle(directory: Path, hashes: dict[str, str], pair_cache: dict,
                       utterance_cache: dict):
    summary_path = directory / "copula_summary.csv"
    summary = _read_csv(
        summary_path,
        {"dataset", "scope", "arm", "be_filter", "age_group", "alpha", "mse",
         "n_utterances", "n_pairs", "status"},
    )
    metadata = _read_json(directory / "metadata.json")
    _require(len(summary) > 0 and summary.status.eq("ok").all(),
             f"non-ok or empty fit summary: {summary_path}")
    datasets = summary.dataset.astype(str).unique().tolist()
    _require(len(datasets) == 1 and datasets[0] == metadata.get("dataset"),
             f"dataset metadata mismatch in {directory}")
    dataset = datasets[0]
    arms = list(metadata.get("arms", []))
    conditions = list(metadata.get("conditions", []))
    scopes = summary.scope.astype(str).drop_duplicates().tolist()
    _require({"ud_strict", "ud_copula"}.issubset(arms),
             f"required UD arms absent from {directory}")
    _require(set(conditions) == {"all", "no_be"},
             f"unexpected be-filter conditions in {directory}")
    groups = list(summary.groupby(["scope", "arm", "be_filter"], sort=False))
    _require(len(groups) == int(metadata.get("n_fits", -1)),
             f"n_fits mismatch in {directory}")
    expected_groups = {(scope, arm, condition) for scope in scopes
                       for arm in arms for condition in conditions}
    _require({tuple(map(str, key)) for key, _ in groups} == expected_groups,
             f"fit matrix is incomplete in {directory}")
    _require(summary.groupby(["scope", "age_group"]).n_utterances.nunique().eq(1).all(),
             f"utterance denominators differ among arms in {directory}")

    input_provenance = metadata.get("input_provenance", {})
    _require(set(input_provenance) == set(arms), f"input provenance mismatch in {directory}")
    for arm, source in input_provenance.items():
        for kind in ("pairs", "metadata"):
            path = Path(source[kind])
            _require(_sha256(path, hashes) == source[f"{kind}_sha256"],
                     f"source {kind} hash mismatch for {arm}: {path}")

    labels_by_group = [set(group.age_group.astype(str)) for _, group in groups]
    _require(all(labels == labels_by_group[0] for labels in labels_by_group),
             f"age labels differ among fits in {directory}")
    ages = sorted(labels_by_group[0], key=_age_key)
    _require(tuple(ages) == EXPECTED_AGES, f"age coverage mismatch in {directory}: {ages}")
    curves, filters, overlaps, effects, input_paths = [], [], [], [], set()
    group_lookup = {}
    for (scope, arm, condition), group in groups:
        group_lookup[(str(scope), str(arm), str(condition))] = group
        folder = directory / "fits" / str(scope) / str(arm) / str(condition)
        fit_metadata = _read_json(folder / "fit_metadata.json")
        inputs = fit_metadata.get("inputs", {})
        _require(set(inputs) >= {"pairs", "utterances", "metadata"},
                 f"fit inputs missing in {folder}")
        for value in inputs.values():
            input_path = Path(value)
            _require(input_path.is_absolute() and input_path.is_file(),
                     f"fit input path is not absolute and live: {value}")
            input_paths.add(str(input_path))
        overall_rows = group[group.age_group.astype(str).eq("overall")]
        _require(len(overall_rows) == 1, f"overall row count mismatch in {folder}")
        overall = overall_rows.iloc[0]
        bins = group[~group.age_group.astype(str).eq("overall")]
        exclusions = fit_metadata.get("exclusion_counts", {})
        _require(int(overall.n_pairs) == int(bins.n_pairs.sum())
                 + int(exclusions.get("pairs_outside_age_bins_but_in_overall", -1)),
                 f"pair age-bin balance mismatch in {folder}")
        _require(int(overall.n_utterances) == int(bins.n_utterances.sum())
                 + int(exclusions.get("utterances_outside_age_bins_but_in_overall", -1)),
                 f"utterance age-bin balance mismatch in {folder}")
        settings = fit_metadata.get("fitting_settings", {})
        pair_path, utterance_path = Path(inputs["pairs"]), Path(inputs["utterances"])
        pair_counts, pair_exclusions, pair_eng_na = _summarize_pairs(pair_path, pair_cache)
        utterance_counts, utterance_exclusions, utterance_eng_na = _summarize_utterances(
            utterance_path, utterance_cache
        )
        if str(scope) == "eng_na":
            _require(pair_eng_na, f"Eng-NA fit pair input contains another collection: {pair_path}")
            _require(utterance_eng_na,
                     f"Eng-NA fit utterance input contains another collection: {utterance_path}")
        for item in group.itertuples(index=False):
            label = str(item.age_group)
            _require(int(item.n_pairs) == pair_counts[label],
                     f"pair denominator mismatch for {folder} ({label})")
            _require(int(item.n_utterances) == utterance_counts[label],
                     f"utterance denominator mismatch for {folder} ({label})")
        for key, value in {**pair_exclusions, **utterance_exclusions}.items():
            _require(int(exclusions.get(key, -1)) == value,
                     f"exclusion count mismatch for {folder}: {key}")
        filters.append(_check_pair_file(
            pair_path, int(overall.n_pairs), str(condition) == "no_be",
            str(scope) == "eng_na", hashes
        ))
        for row in group.itertuples(index=False):
            curve = _verify_curve(folder, row, settings)
            curve.update({"dataset": dataset, "scope": str(scope),
                          "arm": str(arm), "be_filter": str(condition)})
            curves.append(curve)

    for scope in scopes:
        for age in ages:
            indexed = pd.concat(
                [group_lookup[(scope, arm, condition)] for arm in arms for condition in conditions]
            )
            indexed = indexed[indexed.age_group.astype(str).eq(age)].set_index(["arm", "be_filter"])
            _require(not indexed.index.duplicated().any(),
                     f"duplicate result rows for {dataset}/{scope}/{age}")
            strict_pairs = int(indexed.loc[("ud_strict", "all")].n_pairs)
            for arm in arms:
                all_row = indexed.loc[(arm, "all")]
                no_row = indexed.loc[(arm, "no_be")]
                all_pairs, no_pairs = int(all_row.n_pairs), int(no_row.n_pairs)
                _require(no_pairs <= all_pairs,
                         f"no-be pair count exceeds all pairs for {dataset}/{scope}/{arm}/{age}")
                effects.append({
                    "dataset": dataset, "scope": scope, "age_group": age, "arm": arm,
                    "n_utterances": int(all_row.n_utterances), "all_pairs": all_pairs,
                    "no_be_pairs": no_pairs, "be_pairs_removed": all_pairs - no_pairs,
                    "copula_pairs_added": all_pairs - strict_pairs if arm == "ud_copula" else 0,
                })
                before = _top_verbs(directory / "fits" / scope / arm / "all", age)
                after = _top_verbs(directory / "fits" / scope / arm / "no_be", age)
                _require("be" not in {verb.strip().casefold() for verb in after},
                         f"no-be top verbs contain be for {dataset}/{scope}/{arm}/{age}")
                overlaps.append({
                    "dataset": dataset, "scope": scope, "age_group": age,
                    "comparison": f"{arm}: all vs no_be", "n_common": len(before & after),
                    "only_before": ";".join(sorted(before - after)),
                    "only_after": ";".join(sorted(after - before)),
                })
            for condition in conditions:
                before = _top_verbs(directory / "fits" / scope / "ud_strict" / condition, age)
                after = _top_verbs(directory / "fits" / scope / "ud_copula" / condition, age)
                overlaps.append({
                    "dataset": dataset, "scope": scope, "age_group": age,
                    "comparison": f"ud_strict vs ud_copula: {condition}",
                    "n_common": len(before & after),
                    "only_before": ";".join(sorted(before - after)),
                    "only_after": ";".join(sorted(after - before)),
                })
    return curves, filters, overlaps, effects, input_paths


def _verify_paper(directory: Path, hashes: dict[str, str]):
    summary = _read_csv(
        directory / "summary.csv",
        {"age_group", "alpha", "mse", "n_utterances", "n_pairs", "status"},
    )
    metadata = _read_json(directory / "metadata.json")
    _require(len(summary) > 0 and summary.status.eq("ok").all(),
             f"non-ok or empty paper no-be summary: {directory}")
    baseline_summary = Path(metadata["source_baseline"]) / "baseline_summary.csv"
    _require(_sha256(baseline_summary, hashes) == metadata["baseline_summary_sha256"],
             f"baseline summary hash mismatch: {baseline_summary}")
    sources = metadata.get("sources", [])
    _require({str(source["age_group"]) for source in sources}
             == set(summary.age_group.astype(str)), f"paper source/summary age mismatch in {directory}")
    _require(tuple(sorted(summary.age_group.astype(str), key=_age_key)) == EXPECTED_AGES,
             f"paper age coverage mismatch in {directory}")
    settings = metadata.get("fitting_settings", DEFAULT_GRID)
    curves, filters = [], []
    for source in sources:
        age = str(source["age_group"])
        source_path = Path(source["source_pairs"])
        _require(_sha256(source_path, hashes) == source["source_pairs_sha256"],
                 f"paper source hash mismatch: {source_path}")
        _check_pair_file(source_path, int(source["original_pairs"]), False, False, hashes)
        filters.append(_check_pair_file(
            directory / "filtered_pairs" / f"{age}.csv",
            int(source["remaining_pairs"]), True, False, hashes,
        ))
        rows = summary[summary.age_group.astype(str).eq(age)]
        _require(len(rows) == 1, f"paper summary row count mismatch for {age}")
        row = rows.iloc[0]
        _require(int(row.n_utterances) == int(source["n_utterances"]),
                 f"paper utterance denominator mismatch for {age}")
        _require(int(row.n_pairs) == int(source["remaining_pairs"])
                 == int(source["original_pairs"]) - int(source["removed_be_pairs"]),
                 f"paper no-be pair accounting mismatch for {age}")
        curve = _verify_curve(directory / "fits", row, settings)
        curve.update({"dataset": "paper_saved_spacy", "scope": metadata.get("scope", ""),
                      "arm": "spacy_saved", "be_filter": "no_be"})
        curves.append(curve)
    return curves, filters


def verify_results(matched_fits, full_fits, paper_no_be, output) -> dict:
    """Verify three diagnostic result trees and atomically publish the audit."""
    inputs = {
        "matched_fits": Path(matched_fits).resolve(),
        "full_fits": Path(full_fits).resolve(),
        "paper_no_be": Path(paper_no_be).resolve(),
    }
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"output directory already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    hashes: dict[str, str] = {}
    try:
        curves, filters, overlaps, effects, input_paths = [], [], [], [], set()
        pair_cache, utterance_cache = {}, {}
        for key in ("matched_fits", "full_fits"):
            result = _verify_fit_bundle(inputs[key], hashes, pair_cache, utterance_cache)
            curves.extend(result[0]); filters.extend(result[1]); overlaps.extend(result[2])
            effects.extend(result[3]); input_paths.update(result[4])
        paper_curves, paper_filters = _verify_paper(inputs["paper_no_be"], hashes)
        curves.extend(paper_curves); filters.extend(paper_filters)

        for root in inputs.values():
            _require(root.is_dir(), f"input result directory does not exist: {root}")
            for path in root.rglob("*"):
                if path.is_file():
                    _sha256(path, hashes)

        overlap_path = stage / "top_verb_overlap.csv"
        effects_path = stage / "pair_effects.csv"
        pd.DataFrame(overlaps).to_csv(overlap_path, index=False)
        pd.DataFrame(effects).to_csv(effects_path, index=False)
        generated = {
            "top_verb_overlap.csv": _sha256(overlap_path, hashes),
            "pair_effects.csv": _sha256(effects_path, hashes),
        }
        report = {
            "all_passed": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "inputs": {key: str(path) for key, path in inputs.items()},
            "output": str(output),
            "verifier": str(Path(__file__).resolve()),
            "verifier_sha256": _sha256(Path(__file__), hashes),
            "n_curves": len(curves),
            "n_mse_points_verified": sum(item["grid_points"] for item in curves),
            "curves": curves,
            "common_utterance_denominators": True,
            "overall_age_bin_balance_verified": True,
            "age_96_overall_only_preserved": True,
            "n_published_input_paths_verified": len(input_paths),
            "no_be_filters": filters,
            "generated_outputs": generated,
            "input_artifact_sha256": {
                path: digest for path, digest in hashes.items()
                if not Path(path).is_relative_to(stage)
            },
        }
        (stage / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
        stage.rename(output)
        return report
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matched-fits", type=Path,
                        default=Path("output/copula_matched_fits"))
    parser.add_argument("--full-fits", type=Path,
                        default=Path("output/copula_full_fits"))
    parser.add_argument("--paper-no-be", type=Path,
                        default=Path("output/copula_paper_without_be"))
    parser.add_argument("--output", type=Path, required=True,
                        help="New directory for verification.json and diagnostic CSVs")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    report = verify_results(args.matched_fits, args.full_fits, args.paper_no_be, args.output)
    print(json.dumps({
        "output": str(args.output.resolve()), "n_curves": report["n_curves"],
        "n_mse_points_verified": report["n_mse_points_verified"],
        "all_passed": report["all_passed"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
