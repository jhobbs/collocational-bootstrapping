#!/usr/bin/env python3
"""Audit historical CHILDES corpus coverage and transcript-age overlap."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Iterable

import pandas as pd


HERE = Path(__file__).resolve().parent
DEFAULT_ORIGINAL_UTTERANCES = Path(
    "/home/jason/imagining-syntax/childes_full_utterances_20251211_120050.csv"
)
DEFAULT_ORIGINAL_TRANSCRIPTS = Path(
    "/home/jason/talkbank-data/2026-09-07/original_transcript_ages.csv"
)
DEFAULT_MANIFEST = Path("/home/jason/talkbank-data/2026-09-07/repository_english.json")
DEFAULT_LINKS = HERE / "output/original_transcript_links/transcript_links.csv"
DEFAULT_EXTRACTION_METADATA = HERE / "output/repository_extraction_v2/metadata.json"
DEFAULT_OUTPUT = HERE / "output/original_scope_audit"
LINK_KEYS = ["collection", "corpus", "transcript"]


def require_columns(frame: pd.DataFrame, required: Iterable[str], label: str) -> None:
    missing = sorted(set(required) - set(frame.columns))
    if missing:
        raise ValueError(f"{label} missing required columns: {', '.join(missing)}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_table(manifest: dict) -> pd.DataFrame:
    rows = []
    for source in manifest["sources"]:
        archive_id = str(source["archive_id"])
        without_suffix = archive_id[:-4] if archive_id.endswith(".zip") else archive_id
        rows.append(
            {
                "collection": source["collection"],
                "source_corpus": source["corpus"],
                "archive_id": archive_id,
                "archive_leaf": without_suffix.rsplit("/", 1)[-1],
            }
        )
    return pd.DataFrame(rows)


def stream_original_corpus_counts(path: Path, chunksize: int = 500_000) -> pd.DataFrame:
    """Count utterances and usable ages without loading the 5.1M-row CSV."""
    totals: Counter = Counter()
    usable: Counter = Counter()
    through_96: Counter = Counter()
    over_96: Counter = Counter()
    for chunk in pd.read_csv(
        path,
        usecols=["corpus_name", "target_child_age"],
        dtype=str,
        keep_default_na=False,
        chunksize=chunksize,
    ):
        age = pd.to_numeric(chunk["target_child_age"], errors="coerce")
        age_usable = age.notna() & age.ge(0)
        work = pd.DataFrame(
            {
                "original_corpus": chunk["corpus_name"],
                "n_utterances_total": 1,
                "n_utterances_age_usable": age_usable.astype("int64"),
                "n_utterances_age_le_96": (age_usable & age.le(96)).astype("int64"),
                "n_utterances_age_gt_96": (age_usable & age.gt(96)).astype("int64"),
            }
        )
        grouped = work.groupby("original_corpus", sort=False).sum()
        for corpus, row in grouped.iterrows():
            totals[corpus] += int(row["n_utterances_total"])
            usable[corpus] += int(row["n_utterances_age_usable"])
            through_96[corpus] += int(row["n_utterances_age_le_96"])
            over_96[corpus] += int(row["n_utterances_age_gt_96"])
    rows = []
    for corpus in sorted(totals):
        rows.append(
            {
                "original_corpus": corpus,
                "n_utterances_total": totals[corpus],
                "n_utterances_age_usable": usable[corpus],
                "n_utterances_age_le_96": through_96[corpus],
                "n_utterances_age_gt_96": over_96[corpus],
                "n_utterances_age_missing_or_invalid": totals[corpus] - usable[corpus],
            }
        )
    return pd.DataFrame(rows)


def map_corpus_coverage(coverage: pd.DataFrame, sources: pd.DataFrame) -> pd.DataFrame:
    """Map original names by exact source corpus, then by archive leaf."""
    required = {"archive_id", "source_corpus"}
    require_columns(sources, required, "manifest sources")
    sources = sources.copy()
    if "archive_leaf" not in sources:
        sources["archive_leaf"] = sources["archive_id"].astype(str).str.removesuffix(".zip").str.rsplit("/").str[-1]
    mapped = []
    for row in coverage.to_dict("records"):
        corpus = str(row["original_corpus"])
        exact = sorted(sources.loc[sources["source_corpus"] == corpus, "archive_id"].astype(str))
        leaf = sorted(sources.loc[sources["archive_leaf"] == corpus, "archive_id"].astype(str))
        if len(exact) == 1:
            status, candidates = "exact_source_corpus_match", exact
        elif len(exact) > 1:
            status, candidates = "ambiguous_exact_match", exact
        elif len(leaf) == 1:
            status, candidates = "unique_leaf_match", leaf
        elif len(leaf) > 1:
            status, candidates = "ambiguous_leaf_match", leaf
        else:
            status, candidates = "not_represented", []
        row.update(
            exact_archive_ids=";".join(exact),
            leaf_archive_ids=";".join(leaf),
            mapping_status=status,
            candidate_archive_count=len(candidates),
            candidate_archive_ids=";".join(candidates),
            represented_in_current_manifest=status != "not_represented",
            mapping_ambiguous=status.startswith("ambiguous_"),
        )
        mapped.append(row)
    return pd.DataFrame(mapped)


def current_file_table(metadata: dict) -> pd.DataFrame:
    rows = []
    for item in metadata["files"]:
        rows.append(
            {
                "collection": str(item.get("collection", "")),
                "corpus": str(item.get("corpus", "")),
                "transcript": str(item.get("transcript", "")),
                "target_child_age_months": item.get("target_child_age_months"),
                "age_status": str(item.get("age_status", "")),
                "parse_status": str(item.get("status", "")),
            }
        )
    return pd.DataFrame(rows)


def classify_age_overlap(
    links: pd.DataFrame,
    original_transcripts: pd.DataFrame,
    current_files: pd.DataFrame,
) -> tuple[pd.DataFrame, dict]:
    """Join high-confidence links to authoritative original/current metadata."""
    require_columns(
        links,
        LINK_KEYS + ["status", "original_transcript_id"],
        "transcript links",
    )
    require_columns(
        original_transcripts,
        ["transcript_id", "target_child_age"],
        "original transcript metadata",
    )
    require_columns(
        current_files,
        LINK_KEYS + ["target_child_age_months", "age_status"],
        "current extraction metadata",
    )
    links = links.copy()
    original_transcripts = original_transcripts.copy()
    current_files = current_files.copy()
    for column in LINK_KEYS + ["original_transcript_id"]:
        if column in links:
            links[column] = links[column].astype(str)
    original_transcripts["transcript_id"] = original_transcripts["transcript_id"].astype(str)
    for column in LINK_KEYS:
        current_files[column] = current_files[column].astype(str)
    if original_transcripts["transcript_id"].duplicated().any():
        raise ValueError("original transcript metadata contains duplicate transcript_id values")
    if current_files.duplicated(LINK_KEYS).any():
        raise ValueError("current extraction metadata contains duplicate transcript keys")

    high = links.loc[links["status"] == "high_confidence_text_match"].copy()
    original = original_transcripts[["transcript_id", "target_child_age"]].rename(
        columns={
            "transcript_id": "original_transcript_id",
            "target_child_age": "original_age_raw",
        }
    )
    detail = high.merge(original, on="original_transcript_id", how="left", validate="many_to_one", indicator="original_metadata_join")
    current_columns = LINK_KEYS + ["target_child_age_months", "age_status"]
    if "parse_status" in current_files:
        current_columns.append("parse_status")
    detail = detail.merge(
        current_files[current_columns].rename(
            columns={
                "target_child_age_months": "current_age_raw",
                "age_status": "current_age_status",
            }
        ),
        on=LINK_KEYS,
        how="left",
        validate="one_to_one",
        indicator="current_metadata_join",
    )
    detail["original_age_months"] = pd.to_numeric(detail["original_age_raw"], errors="coerce")
    detail["current_age_months"] = pd.to_numeric(detail["current_age_raw"], errors="coerce")
    original_usable = detail["original_age_months"].notna() & detail["original_age_months"].ge(0)
    current_usable = detail["current_age_months"].notna() & detail["current_age_months"].ge(0)
    detail["original_age_usable"] = original_usable
    detail["current_age_usable"] = current_usable
    detail["age_combination"] = "both_missing"
    detail.loc[original_usable & current_usable, "age_combination"] = "both_usable"
    detail.loc[original_usable & ~current_usable, "age_combination"] = "original_usable_current_missing"
    detail.loc[~original_usable & current_usable, "age_combination"] = "original_missing_current_usable"
    detail["absolute_age_difference_months"] = (
        detail["original_age_months"] - detail["current_age_months"]
    ).abs()
    detail["age_conflict_gt_0_1_month"] = (
        original_usable & current_usable & detail["absolute_age_difference_months"].gt(0.1)
    )
    detail["original_id_used_by_multiple_current_transcripts"] = detail[
        "original_transcript_id"
    ].duplicated(keep=False)
    categories = [
        "original_usable_current_missing",
        "original_missing_current_usable",
        "both_usable",
        "both_missing",
    ]
    combination_counts = detail["age_combination"].value_counts().to_dict()
    duplicate_rows = detail[detail["original_id_used_by_multiple_current_transcripts"]]
    current_age_missing = int((~current_usable).sum())
    recoverable_age_count = int(
        (detail["age_combination"] == "original_usable_current_missing").sum()
    )
    summary = {
        "n_high_confidence_matched_current_transcripts": int(len(detail)),
        "n_high_confidence_unique_original_transcript_ids": int(detail["original_transcript_id"].nunique()),
        "age_combinations": {name: int(combination_counts.get(name, 0)) for name in categories},
        "n_high_confidence_current_age_missing": current_age_missing,
        "n_currently_age_missing_with_usable_original_age": recoverable_age_count,
        "fraction_current_age_missing_with_usable_original_age": (
            recoverable_age_count / current_age_missing if current_age_missing else None
        ),
        "n_age_conflicts_gt_0_1_month": int(detail["age_conflict_gt_0_1_month"].sum()),
        "n_duplicated_original_transcript_ids": int(duplicate_rows["original_transcript_id"].nunique()),
        "n_current_transcripts_using_duplicated_original_ids": int(len(duplicate_rows)),
        "n_missing_original_metadata_joins": int((detail["original_metadata_join"] != "both").sum()),
        "n_missing_current_metadata_joins": int((detail["current_metadata_join"] != "both").sum()),
    }
    return detail, summary


def _input_provenance(paths: dict[str, Path]) -> dict:
    return {
        name: {
            "path": str(path.resolve()),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for name, path in paths.items()
    }


def _write_report(path: Path, summary: dict) -> None:
    corpus = summary["corpus_scope"]
    ages = summary["transcript_age_overlap"]
    lines = [
        "# Original CHILDES scope and age audit",
        "",
        f"The historical utterance export contains {corpus['n_original_utterances']:,} utterances across {corpus['n_original_corpora']} corpus names. "
        f"{corpus['n_original_utterances_age_le_96']:,} have a numeric nonnegative target-child age at or below 96 months.",
        "",
        f"The current repository manifest contains {corpus['n_current_archives']} archives. "
        f"Of the historical corpus names, {corpus['n_original_corpora_not_represented']} have no exact source-corpus or leaf match and "
        f"{corpus['n_original_corpora_with_ambiguous_mapping']} have ambiguous matches.",
        "",
        f"There are {ages['n_high_confidence_matched_current_transcripts']:,} high-confidence linked current transcripts. "
        f"Among the {ages['n_high_confidence_current_age_missing']:,} that lack a usable current age, "
        f"{ages['n_currently_age_missing_with_usable_original_age']:,} ({ages['fraction_current_age_missing_with_usable_original_age']:.1%}) have a usable historical age; "
        f"{ages['n_age_conflicts_gt_0_1_month']:,} have both ages and differ by more than 0.1 month.",
        "",
        "Corpus-name coverage is not a language audit. The historical R workflow filtered the participant table to `language == \"eng\"`, "
        "then requested utterances by corpus and role without a language argument. No language was inferred from corpus names here.",
        "",
        "See `original_corpus_coverage.csv`, `current_missing_with_original_age.csv`, `age_conflicts_gt_0_1mo.csv`, "
        "`matched_transcript_age_audit.csv`, and `audit_summary.json` for details.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def run_audit(
    original_utterances: Path,
    original_transcripts_path: Path,
    manifest_path: Path,
    links_path: Path,
    extraction_metadata_path: Path,
    output: Path,
) -> dict:
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    extraction = json.loads(extraction_metadata_path.read_text(encoding="utf-8"))
    sources = source_table(manifest)
    coverage = map_corpus_coverage(stream_original_corpus_counts(original_utterances), sources)
    coverage.to_csv(output / "original_corpus_coverage.csv", index=False)
    coverage.loc[coverage["mapping_status"] == "not_represented"].to_csv(
        output / "original_unrepresented_corpora.csv", index=False
    )
    coverage.loc[coverage["mapping_ambiguous"]].to_csv(
        output / "ambiguous_corpus_matches.csv", index=False
    )

    links = pd.read_csv(links_path, dtype=str, keep_default_na=False)
    original_transcripts = pd.read_csv(
        original_transcripts_path, dtype=str, keep_default_na=False
    )
    current_files = current_file_table(extraction)
    age_detail, age_summary = classify_age_overlap(
        links, original_transcripts, current_files
    )
    age_detail.to_csv(output / "matched_transcript_age_audit.csv", index=False)
    age_detail.loc[
        age_detail["age_combination"] == "original_usable_current_missing"
    ].to_csv(output / "current_missing_with_original_age.csv", index=False)
    age_detail.loc[age_detail["age_conflict_gt_0_1_month"]].to_csv(
        output / "age_conflicts_gt_0_1mo.csv", index=False
    )
    pd.DataFrame(
        [
            {"age_combination": name, "n_transcripts": count}
            for name, count in age_summary["age_combinations"].items()
        ]
    ).to_csv(output / "age_overlap_summary.csv", index=False)
    duplicate_ids = (
        age_detail.loc[age_detail["original_id_used_by_multiple_current_transcripts"]]
        .groupby("original_transcript_id", as_index=False)
        .agg(
            n_current_transcripts=("transcript", "size"),
            current_transcripts=("transcript", lambda values: ";".join(sorted(values))),
        )
    )
    duplicate_ids.to_csv(output / "duplicated_original_transcript_ids.csv", index=False)

    status_counts = links["status"].value_counts().to_dict()
    mapping_counts = coverage["mapping_status"].value_counts().to_dict()
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "corpus_scope": {
            "n_original_utterances": int(coverage["n_utterances_total"].sum()),
            "n_original_utterances_age_usable": int(coverage["n_utterances_age_usable"].sum()),
            "n_original_utterances_age_le_96": int(coverage["n_utterances_age_le_96"].sum()),
            "n_original_utterances_age_gt_96": int(coverage["n_utterances_age_gt_96"].sum()),
            "n_original_utterances_age_missing_or_invalid": int(coverage["n_utterances_age_missing_or_invalid"].sum()),
            "n_original_corpora": int(len(coverage)),
            "n_current_archives": int(len(sources)),
            "n_current_unique_source_corpus_labels": int(sources["source_corpus"].nunique()),
            "mapping_status_counts": {key: int(value) for key, value in mapping_counts.items()},
            "n_original_corpora_not_represented": int((coverage["mapping_status"] == "not_represented").sum()),
            "n_original_utterances_in_unrepresented_corpora": int(
                coverage.loc[coverage["mapping_status"] == "not_represented", "n_utterances_total"].sum()
            ),
            "n_original_corpora_with_ambiguous_mapping": int(coverage["mapping_ambiguous"].sum()),
        },
        "transcript_age_overlap": {
            "n_original_transcripts_in_metadata": int(len(original_transcripts)),
            "n_current_transcripts_in_extraction_metadata": int(len(current_files)),
            "current_extraction_reported_counts": {
                key: int(extraction["counts"][key])
                for key in [
                    "n_files",
                    "n_files_missing_age",
                    "n_utterances",
                    "n_cds_utterances",
                    "n_cds_utterances_missing_age",
                ]
            },
            "n_current_transcripts_in_link_table": int(len(links)),
            "link_status_counts": {key: int(value) for key, value in status_counts.items()},
            **age_summary,
        },
        "language_scope_caveat": {
            "language_inference_from_corpus_names_performed": False,
            "observation": (
                "childes_get_adult_speakers.R filters participants where language == eng, "
                "but childes_get_utterances.R calls get_utterances(corpus, role) without a language filter."
            ),
            "implication": (
                "Historical corpus-name coverage cannot establish that every returned utterance was English."
            ),
        },
        "age_definitions": {
            "usable": "numeric and nonnegative",
            "historical_fit_eligible": "usable and <= 96 months",
            "conflict": "both usable and absolute difference > 0.1 month",
        },
        "provenance": _input_provenance(
            {
                "original_utterances": original_utterances,
                "original_transcript_ages": original_transcripts_path,
                "current_repository_manifest": manifest_path,
                "transcript_links": links_path,
                "current_extraction_metadata": extraction_metadata_path,
                "original_participant_selection_r": HERE.parent / "childes_get_adult_speakers.R",
                "original_utterance_retrieval_r": HERE.parent / "childes_get_utterances.R",
            }
        ),
    }
    (output / "audit_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    _write_report(output / "REPORT.md", summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-utterances", type=Path, default=DEFAULT_ORIGINAL_UTTERANCES)
    parser.add_argument("--original-transcripts", type=Path, default=DEFAULT_ORIGINAL_TRANSCRIPTS)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--links", type=Path, default=DEFAULT_LINKS)
    parser.add_argument("--extraction-metadata", type=Path, default=DEFAULT_EXTRACTION_METADATA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    summary = run_audit(
        args.original_utterances,
        args.original_transcripts,
        args.manifest,
        args.links,
        args.extraction_metadata,
        args.output,
    )
    print(json.dumps({"corpus_scope": summary["corpus_scope"], "transcript_age_overlap": summary["transcript_age_overlap"]}, indent=2))


if __name__ == "__main__":
    main()
