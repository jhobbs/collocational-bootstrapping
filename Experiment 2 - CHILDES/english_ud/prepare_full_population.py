"""Prepare the age-bounded earlier TalkBank population for copula diagnostics."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile


AGE_GROUPS = [(start, start + 12, f"{start}-{start + 12}mo") for start in range(0, 96, 12)]
EXPECTED_ALL = {
    "overall": (3_792_185, 1_775_181), "0-12mo": (186_696, 89_812),
    "12-24mo": (561_660, 201_409), "24-36mo": (1_601_873, 748_922),
    "36-48mo": (715_524, 363_466), "48-60mo": (346_240, 188_054),
    "60-72mo": (195_959, 93_772), "72-84mo": (95_973, 48_626),
    "84-96mo": (88_206, 41_109),
}
EXPECTED_ENG_NA = {
    "overall": (1_607_773, 766_392), "0-12mo": (135_499, 67_751),
    "12-24mo": (326_069, 118_443), "24-36mo": (507_763, 251_406),
    "36-48mo": (254_344, 131_440), "48-60mo": (143_539, 79_334),
    "60-72mo": (117_590, 59_548), "72-84mo": (58_898, 29_573),
    "84-96mo": (64_071, 28_897),
}


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def selected_age(value):
    try:
        age = float(value)
    except (TypeError, ValueError):
        return None
    return age if math.isfinite(age) and 0 <= age <= 96 else None


def age_group(age):
    for lower, upper, label in AGE_GROUPS:
        if lower <= age < upper:
            return label
    return None


def _copy_selected(source, destination, kind, audits=None):
    counts = Counter()
    by_age = Counter()
    eng_na = Counter()
    with source.open(newline="") as src, destination.open("w", newline="") as dst:
        reader = csv.DictReader(src)
        fields = reader.fieldnames or []
        required = ["target_child_age_months", "collection"]
        if kind == "utterances":
            required += ["included_in_cds", "annotation_status", "text"]
        missing = [field for field in required if field not in fields]
        if missing:
            raise ValueError(f"{kind} CSV missing required columns: {', '.join(missing)}")
        writer = csv.DictWriter(dst, fieldnames=fields)
        writer.writeheader()
        for row in reader:
            counts["input"] += 1
            try:
                raw_age = float(row["target_child_age_months"])
            except (TypeError, ValueError):
                raw_age = math.nan
            if (kind != "utterances" or row["included_in_cds"].strip().lower() == "true") and math.isfinite(raw_age) and raw_age < 0:
                counts["negative_finite"] += 1
            age = selected_age(row["target_child_age_months"])
            if age is None or (kind == "utterances" and row["included_in_cds"].strip().lower() != "true"):
                continue
            writer.writerow(row)
            counts["selected"] += 1
            label = age_group(age)
            if label:
                by_age[label] += 1
            if row["collection"] == "Eng-NA":
                eng_na["overall"] += 1
                if label:
                    eng_na[label] += 1
            if audits is not None:
                status = row["annotation_status"] or "empty"
                collection = row["collection"] or "empty"
                audits["overall"][status] += 1
                audits["by_collection"][collection][status] += 1
                if label:
                    audits["by_age_group"][label][status] += 1
                text_state = "nonempty" if row["text"].strip() else "empty"
                audits["text"][text_state] += 1
    return counts, by_age, eng_na


def _observed(utterance_age, utterance_na, pair_age, pair_na, total_u, total_p):
    all_counts = {"overall": (total_u, total_p)}
    na_counts = {"overall": (utterance_na["overall"], pair_na["overall"])}
    for _lo, _hi, label in AGE_GROUPS:
        all_counts[label] = (utterance_age[label], pair_age[label])
        na_counts[label] = (utterance_na[label], pair_na[label])
    return all_counts, na_counts


def prepare_population(source, output, verify_expected=True):
    source = Path(source).resolve(); output = Path(output)
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    utterance_source = source / "english_ud_utterances.csv"
    pair_source = source / "english_ud_subject_verb_pairs.csv"
    metadata_source = source / "metadata.json"
    for path in [utterance_source, pair_source, metadata_source]:
        if not path.is_file():
            raise FileNotFoundError(path)
    source_metadata = json.loads(metadata_source.read_text())
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".prepare-full-population-", dir=output.parent))
    audits = {"overall": Counter(), "by_collection": defaultdict(Counter),
              "by_age_group": defaultdict(Counter), "text": Counter()}
    try:
        uc, ua, un = _copy_selected(utterance_source, stage / utterance_source.name,
                                    "utterances", audits)
        pc, pa, pn = _copy_selected(pair_source, stage / pair_source.name, "pairs")
        observed_all, observed_na = _observed(
            ua, un, pa, pn, uc["selected"], pc["selected"])
        if verify_expected:
            if uc["negative_finite"] or pc["negative_finite"]:
                raise ValueError("Negative finite ages violate equivalence with fit_zipf overall policy")
            if observed_all != EXPECTED_ALL:
                raise ValueError(f"Full-population counts differ: {observed_all}")
            if observed_na != EXPECTED_ENG_NA:
                raise ValueError(f"Eng-NA counts differ: {observed_na}")
        parent_hashes = {
            "utterances_sha256": sha256_file(utterance_source),
            "pairs_sha256": sha256_file(pair_source),
            "metadata_sha256": sha256_file(metadata_source),
        }
        compact_source_metadata = {key: value for key, value in source_metadata.items()
                                   if key not in {"files", "sources", "matching_provenance"}}
        report = {
            "scope": "earlier_talkbank_population",
            "collection": source_metadata.get("collection"),
            "annotation_scheme": source_metadata.get("annotation_scheme"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "counts": {"n_input_utterances": uc["input"], "n_input_pairs": pc["input"],
                       "n_cds_utterances": uc["selected"], "n_pairs": pc["selected"]},
            "age_counts": {key: {"n_utterances": value[0], "n_pairs": value[1]}
                           for key, value in observed_all.items()},
            "eng_na_age_counts": {key: {"n_utterances": value[0], "n_pairs": value[1]}
                                  for key, value in observed_na.items()},
            "broader_age_counts": {key: {"n_utterances": observed_all[key][0] - observed_na[key][0],
                                         "n_pairs": observed_all[key][1] - observed_na[key][1]}
                                   for key in observed_all},
            "annotation_status_counts": {
                "overall": dict(audits["overall"]),
                "by_collection": {key: dict(value) for key, value in audits["by_collection"].items()},
                "by_age_group": {key: dict(value) for key, value in audits["by_age_group"].items()},
            },
            "text_counts": dict(audits["text"]),
            "selection_provenance": {
                "parent_directory": str(source),
                "parent_hashes": parent_hashes,
                "rule": "included_in_cds == true and finite 0 <= target_child_age_months <= 96",
                "pair_rule": "finite 0 <= target_child_age_months <= 96; unchanged strict UD pair rows",
                "fit_zipf_equivalence": "fit_zipf uses finite age <= 96; zero negative finite included utterances and pairs asserted",
                "transcript_matching": None,
                "source_replacement": None,
                "expected_counts_verified": verify_expected,
            },
            "source_metadata": compact_source_metadata,
            "files": source_metadata.get("files", []),
            "sources": source_metadata.get("sources", []),
        }
        report["output_hashes"] = {
            "utterances_sha256": sha256_file(stage / utterance_source.name),
            "pairs_sha256": sha256_file(stage / pair_source.name),
        }
        (stage / "metadata.json").write_text(json.dumps(report, indent=2) + "\n")
        stage.rename(output)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    print(json.dumps(report["counts"], indent=2))
    return report


def record_spacy_availability(spacy_output):
    """Record spaCy pair yield by source UD annotation availability."""
    spacy_output = Path(spacy_output)
    pairs_path = spacy_output / "english_ud_subject_verb_pairs.csv"
    metadata_path = spacy_output / "metadata.json"
    overall = Counter()
    by_collection = defaultdict(Counter)
    by_age = defaultdict(Counter)
    n_pairs = 0
    with pairs_path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        required = ["annotation_status", "collection", "target_child_age_months"]
        missing = [field for field in required if field not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"spaCy pair CSV missing required columns: {', '.join(missing)}")
        for row in reader:
            n_pairs += 1
            status = row["annotation_status"] or "empty"
            collection = row["collection"] or "empty"
            overall[status] += 1
            by_collection[collection][status] += 1
            age = selected_age(row["target_child_age_months"])
            label = age_group(age) if age is not None else None
            if label:
                by_age[label][status] += 1
    metadata = json.loads(metadata_path.read_text())
    if n_pairs != metadata.get("counts", {}).get("n_pairs"):
        raise ValueError(f"Counted {n_pairs} spaCy pairs but metadata declares {metadata.get('counts', {}).get('n_pairs')}")
    report = {
        "n_pairs": n_pairs,
        "non_annotated_pairs": n_pairs - overall.get("annotated", 0),
        "pair_counts": {
            "overall": dict(overall),
            "by_collection": {key: dict(value) for key, value in by_collection.items()},
            "by_age_group": {key: dict(value) for key, value in by_age.items()},
        },
        "meaning": "annotation_status is inherited from the strict UD source utterance; spaCy parses every retained row independently.",
    }
    diagnostic_path = spacy_output / "annotation_availability.json"
    temporary = spacy_output / ".annotation_availability.json.tmp"
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(diagnostic_path)
    metadata["annotation_availability"] = {
        "path": str(diagnostic_path.resolve()),
        "sha256": sha256_file(diagnostic_path),
        "non_annotated_pairs": report["non_annotated_pairs"],
    }
    temporary_metadata = spacy_output / ".metadata.json.tmp"
    temporary_metadata.write_text(json.dumps(metadata, indent=2) + "\n")
    temporary_metadata.replace(metadata_path)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare_population(args.source, args.output)
    except (FileExistsError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
