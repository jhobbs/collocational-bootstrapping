#!/usr/bin/env python3
"""Audit copula augmentation and spaCy pairs against one utterance population."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile

from extract_pairs import BASE_FIELDS


PAIR_KEY = ("collection", "transcript", "utterance_id", "subject_index", "verb_index")
NON_METADATA_FIELDS = frozenset({
    "included_in_cds", "exclusion_reason", "annotation_status", "n_pairs", "source_ud_n_pairs",
    "text", "chat_main_tier", "subject_surface", "subject_lemma", "subject_pos", "subject_mor",
    "verb_surface", "verb_lemma", "verb_pos", "verb_mor", "dependency", "subject_index",
    "verb_index", "index_space", "pair_origin",
})


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"metadata must contain an object: {path}")
    return value


def key(row: dict) -> tuple[str, ...]:
    return tuple(row.get(field, "") for field in PAIR_KEY)


def require_hash(actual: Path, expected: object, label: str) -> str:
    value = digest(actual)
    if value != expected:
        raise ValueError(f"{label} hash mismatch")
    return value


def require_columns(fieldnames, required, label: str, kind: str = "key") -> None:
    missing = sorted(set(required) - set(fieldnames or []))
    if missing:
        raise ValueError(f"{label} is missing required {kind} columns: {', '.join(missing)}")


def audit(population: Path, augmented: Path, spacy: Path, output: Path) -> dict:
    population, augmented, spacy, output = map(Path, (population, augmented, spacy, output))
    if output.exists():
        raise FileExistsError(output)
    paths = {
        "utterances": population / "english_ud_utterances.csv",
        "strict": population / "english_ud_subject_verb_pairs.csv",
        "augmented": augmented / "english_ud_subject_verb_pairs.csv",
        "additions": augmented / "copula_additions.csv",
        "spacy": spacy / "english_ud_subject_verb_pairs.csv",
    }
    population_meta = load_json(population / "metadata.json")
    augmented_meta = load_json(augmented / "metadata.json")
    spacy_meta = load_json(spacy / "metadata.json")
    augmented_input, spacy_input = augmented_meta.get("input", {}), spacy_meta.get("input", {})
    hashes = {
        "utterances": require_hash(paths["utterances"], augmented_input.get("utterances_sha256"), "augmented utterance input"),
        "strict": require_hash(paths["strict"], augmented_input.get("strict_pairs_sha256"), "augmented strict input"),
        "population_metadata": digest(population / "metadata.json"),
        "augmented_metadata": digest(augmented / "metadata.json"),
        "spacy_metadata": digest(spacy / "metadata.json"),
    }
    require_hash(paths["utterances"], spacy_input.get("utterances_sha256"), "spaCy utterance input")
    if augmented_meta.get("raw_source_members_verified") is not True:
        raise ValueError("Augmenter metadata lacks raw-source verification evidence")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".audit-copula-", dir=output.parent))
    database = sqlite3.connect(temporary / "audit.sqlite")
    counts = Counter()
    try:
        database.execute("CREATE TABLE utterance (collection TEXT, transcript TEXT, uid TEXT, data TEXT, PRIMARY KEY(collection,transcript,uid))")
        with paths["utterances"].open(newline="") as stream:
            reader = csv.DictReader(stream)
            utterance_fields = set(reader.fieldnames or [])
            require_columns(reader.fieldnames, PAIR_KEY[:3], "population utterances")
            required_pair_metadata = ({field for field in BASE_FIELDS if field in utterance_fields}
                                      | {field for field in utterance_fields if field.startswith("original_")}
                                      | {"target_child_age_months"})
            for row in reader:
                try:
                    database.execute("INSERT INTO utterance VALUES (?,?,?,?)",
                                     (row["collection"], row["transcript"], row["utterance_id"], json.dumps(row)))
                except sqlite3.IntegrityError as error:
                    raise ValueError(f"duplicate population utterance key: {row['transcript']}:{row['utterance_id']}") from error
                counts["population_utterances"] += 1
        database.commit()

        def verify_pair_population(row: dict, label: str) -> None:
            found = database.execute("SELECT data FROM utterance WHERE collection=? AND transcript=? AND uid=?",
                                     (row["collection"], row["transcript"], row["utterance_id"])).fetchone()
            if found is None:
                raise ValueError(f"{label} pair has no population utterance: {key(row)}")
            utterance = json.loads(found[0])
            shared = (set(row) & utterance_fields) - NON_METADATA_FIELDS
            differences = [field for field in sorted(shared) if row[field] != utterance[field]]
            if differences:
                raise ValueError(f"{label} pair metadata mismatch for {key(row)}: {', '.join(differences)}")

        database.execute("CREATE TABLE pair_key (arm TEXT, collection TEXT, transcript TEXT, uid TEXT, subject_index TEXT, verb_index TEXT, UNIQUE(arm,collection,transcript,uid,subject_index,verb_index))")
        strict_stream = paths["strict"].open(newline="")
        strict_reader = csv.DictReader(strict_stream)
        strict_fields = strict_reader.fieldnames or []
        require_columns(strict_fields, PAIR_KEY, "strict pairs")
        expected_strict = next(strict_reader, None)
        additions_stream = paths["additions"].open(newline="")
        additions_reader = csv.DictReader(additions_stream)
        require_columns(additions_reader.fieldnames, PAIR_KEY, "copula additions")
        expected_addition = next(additions_reader, None)
        with paths["augmented"].open(newline="") as stream:
            augmented_reader = csv.DictReader(stream)
            augmented_fields = augmented_reader.fieldnames or []
            require_columns(augmented_fields, PAIR_KEY, "augmented pairs")
            missing = sorted(required_pair_metadata - set(augmented_fields))
            if missing:
                raise ValueError("augmented is missing required metadata columns: " + ", ".join(missing))
            for row in augmented_reader:
                origin = row.get("pair_origin")
                if origin not in {"strict_ud", "explicit_copula"}:
                    raise ValueError(f"invalid augmented pair_origin: {origin!r}")
                try:
                    database.execute("INSERT INTO pair_key VALUES (?,?,?,?,?,?)", ("augmented", *key(row)))
                except sqlite3.IntegrityError as error:
                    raise ValueError(f"duplicate augmented pair key: {key(row)}") from error
                verify_pair_population(row, "augmented")
                counts["augmented_pairs"] += 1
                if origin == "strict_ud":
                    if expected_strict is None or {field: row.get(field, "") for field in strict_fields} != expected_strict:
                        raise ValueError("strict rows are not preserved field-for-field and in order")
                    expected_strict = next(strict_reader, None)
                    counts["strict_pairs"] += 1
                else:
                    if expected_addition is None:
                        raise ValueError("augmented additions exceed copula_additions.csv")
                    comparable = {field: expected_addition.get(field, "") for field in augmented_fields}
                    if comparable != row:
                        raise ValueError("copula addition rows differ from augmented explicit_copula rows")
                    expected_addition = next(additions_reader, None)
                    counts["copula_additions"] += 1
        strict_stream.close(); additions_stream.close()
        if expected_strict is not None:
            raise ValueError("strict rows are missing from augmented output")
        if expected_addition is not None:
            raise ValueError("copula_additions.csv rows are missing from augmented output")

        with paths["spacy"].open(newline="") as stream:
            spacy_reader = csv.DictReader(stream)
            require_columns(spacy_reader.fieldnames, PAIR_KEY, "spaCy pairs")
            missing = sorted(required_pair_metadata - set(spacy_reader.fieldnames or []))
            if missing:
                raise ValueError("spaCy is missing required metadata columns: " + ", ".join(missing))
            for row in spacy_reader:
                try:
                    database.execute("INSERT INTO pair_key VALUES (?,?,?,?,?,?)", ("spacy", *key(row)))
                except sqlite3.IntegrityError as error:
                    raise ValueError(f"duplicate spaCy pair key: {key(row)}") from error
                verify_pair_population(row, "spaCy")
                counts["spacy_pairs"] += 1
        expected_population = population_meta.get("counts", {}).get("n_cds_utterances")
        if counts["population_utterances"] != expected_population:
            raise ValueError("population utterance count differs from metadata")
        augmented_counts = augmented_meta.get("counts", {})
        expectations = {
            "strict_pairs": augmented_counts.get("n_existing_pairs"),
            "copula_additions": augmented_counts.get("n_copula_additions"),
            "augmented_pairs": augmented_counts.get("n_pairs"),
            "spacy_pairs": spacy_meta.get("counts", {}).get("n_pairs"),
        }
        for field, expected in expectations.items():
            if counts[field] != expected:
                raise ValueError(f"{field} count differs from metadata")
        if augmented_counts.get("n_cds_utterances") != counts["population_utterances"]:
            raise ValueError("augmented utterance denominator differs from population")
        if spacy_meta.get("counts", {}).get("n_included_utterances") != counts["population_utterances"]:
            raise ValueError("spaCy utterance denominator differs from population")
        hashes.update({name: digest(path) for name, path in paths.items() if name not in hashes})
        report = {
            "status": "passed", "population": str(population.resolve()),
            "augmented": str(augmented.resolve()), "spacy": str(spacy.resolve()),
            "counts": dict(counts), "sha256": hashes,
            "checks": [
                "unique population and within-arm pair keys",
                "strict rows preserved field-for-field and in original order",
                "copula additions agree with augmented explicit_copula rows",
                "all pair age/base/original metadata agree with population utterances",
                "metadata counts and input hashes reconcile",
                "augmenter raw-source verification evidence present",
            ],
        }
        staged = temporary / "report.json"
        staged.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        database.close()
        staged.replace(output)
        shutil.rmtree(temporary)
        return report
    except BaseException:
        database.close()
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population", required=True, type=Path)
    parser.add_argument("--augmented", required=True, type=Path)
    parser.add_argument("--spacy", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    audit(args.population, args.augmented, args.spacy, args.output)


if __name__ == "__main__":
    main()
