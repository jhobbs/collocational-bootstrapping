#!/usr/bin/env python3
"""Augment strict UD subject-verb pairs with explicit nonverbal-predicate copulas."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path
import shutil
import sqlite3
import tempfile
import warnings
import zipfile

import pylangacq

from extract_pairs import dependencies_aligned, lemma_from_mor, pair_edges


AUDIT_EXTRA = ["predicate_surface", "predicate_lemma", "predicate_pos", "predicate_mor",
               "predicate_index", "subject_dependency", "copula_dependency"]
STRICT_IDENTITY_FIELDS = ["subject_surface", "subject_lemma", "subject_pos", "subject_mor",
                          "verb_surface", "verb_lemma", "verb_pos", "verb_mor", "dependency",
                          "subject_index", "verb_index"]


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def raw_member(source: dict, member: str) -> bytes:
    path = Path(source["path"])
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            return archive.read(member)
    if path.is_dir():
        return (path / member).read_bytes()
    if path.suffix.lower() == ".cha":
        return path.read_bytes()
    raise ValueError(f"Unsupported CHAT source: {path}")


def parse_chat(raw: bytes, member: str, mor_tier: str, gra_tier: str):
    text = raw.decode("utf-8-sig")
    try:
        return pylangacq.CHAT.from_strs([text], ids=[member], parallel=False, strict=True,
                                        mor_tier=mor_tier, gra_tier=gra_tier)
    except ValueError:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return pylangacq.CHAT.from_strs([text], ids=[member], parallel=False, strict=False,
                                            mor_tier=mor_tier, gra_tier=gra_tier)


def copula_pairs(utterance):
    by_dep = {token.gra.dep: token for token in utterance.tokens if token.gra}
    for subject in utterance.tokens:
        edge = subject.gra
        if not edge or edge.rel.lower() != "nsubj":
            continue
        predicate = by_dep.get(edge.head)
        if predicate is None or predicate.pos.upper() in {"VERB", "AUX"}:
            continue
        subject_lemma = lemma_from_mor(subject.mor)
        predicate_lemma = lemma_from_mor(predicate.mor)
        if not subject_lemma or subject_lemma in {"_", "xxx", "yyy", "www"}:
            continue
        for copula in utterance.tokens:
            cop_edge = copula.gra
            if (not cop_edge or cop_edge.head != edge.head or cop_edge.rel.lower() != "cop"
                    or copula.pos.upper() not in {"VERB", "AUX"}):
                continue
            copula_lemma = lemma_from_mor(copula.mor)
            if not copula_lemma or copula_lemma in {"_", "xxx", "yyy", "www"}:
                continue
            yield {
                "subject_surface": subject.word, "subject_lemma": subject_lemma,
                "subject_pos": subject.pos.upper(), "subject_mor": subject.mor,
                "verb_surface": copula.word, "verb_lemma": copula_lemma,
                "verb_pos": copula.pos.upper(), "verb_mor": copula.mor,
                "dependency": edge.rel, "subject_index": edge.dep, "verb_index": cop_edge.dep,
                "predicate_surface": predicate.word, "predicate_lemma": predicate_lemma,
                "predicate_pos": predicate.pos.upper(), "predicate_mor": predicate.mor,
                "predicate_index": edge.head, "subject_dependency": edge.rel,
                "copula_dependency": cop_edge.rel,
            }


def age_group(value: str) -> str:
    try:
        age = float(value)
    except (TypeError, ValueError):
        return "missing"
    if not math.isfinite(age):
        return "missing"
    if 0 <= age < 96:
        start = int(age // 12) * 12
        return f"{start}-{start + 12}mo"
    return "outside_0_96mo"


def augment(population: Path, output: Path) -> dict:
    population, output = Path(population), Path(output)
    if output.exists():
        raise FileExistsError(output)
    metadata_path = population / "metadata.json"
    utterance_path = population / "english_ud_utterances.csv"
    pair_path = population / "english_ud_subject_verb_pairs.csv"
    metadata = json.loads(metadata_path.read_text())
    mor_tier, gra_tier = metadata.get("morphology_tier", "%mor"), metadata.get("grammar_tier", "%gra")
    sources = {(source["collection"], source["archive_id"]): source for source in metadata["sources"]}
    if len(sources) != len(metadata["sources"]):
        raise ValueError("Duplicate collection/archive_id source identity")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="." + output.name + "-", dir=output.parent))
    database = sqlite3.connect(stage / "index.sqlite")
    counts, copulas, predicates, ages = Counter(), Counter(), Counter(), Counter()
    samples = []
    try:
        database.execute("CREATE TABLE utterance (collection TEXT, transcript TEXT, uid INTEGER, row_json TEXT, PRIMARY KEY(collection, transcript, uid))")
        database.execute("CREATE TABLE baseline (collection TEXT, transcript TEXT, uid INTEGER, subject_index INTEGER, verb_index INTEGER, signature TEXT, UNIQUE(collection, transcript, uid, subject_index, verb_index))")
        database.execute("CREATE TABLE addition (collection TEXT, transcript TEXT, uid INTEGER, subject_index INTEGER, copula_index INTEGER, row_json TEXT, UNIQUE(collection, transcript, uid, subject_index, copula_index))")
        with utterance_path.open(newline="") as stream:
            utterance_reader = csv.DictReader(stream)
            utterance_fields = utterance_reader.fieldnames
            for row in utterance_reader:
                database.execute("INSERT INTO utterance VALUES (?,?,?,?)",
                                 (row["collection"], row["transcript"], int(row["utterance_id"]), json.dumps(row)))
                counts["n_utterances"] += 1
                if row["annotation_status"] == "annotated":
                    counts["n_expected_annotated_utterances"] += 1
        with pair_path.open(newline="") as stream:
            pair_reader = csv.DictReader(stream)
            pair_fields = pair_reader.fieldnames
            for row in pair_reader:
                signature = json.dumps([row[field] for field in STRICT_IDENTITY_FIELDS])
                try:
                    database.execute("INSERT INTO baseline VALUES (?,?,?,?,?,?)",
                                     (row["collection"], row["transcript"], int(row["utterance_id"]),
                                      int(row["subject_index"]), int(row["verb_index"]), signature))
                except sqlite3.IntegrityError as error:
                    raise ValueError("Duplicate full strict pair key") from error
                counts["n_existing_pairs"] += 1
        database.commit()
        for file_record in metadata["files"]:
            key = (file_record["collection"], file_record["archive_id"])
            source = sources.get(key)
            if source is None:
                raise ValueError(f"No source for {key}")
            raw = raw_member(source, file_record["member"])
            if hashlib.sha256(raw).hexdigest() != file_record["sha256"]:
                raise ValueError(f"Raw member hash mismatch: {file_record['member']}")
            counts["n_raw_members_verified"] += 1
            reader = parse_chat(raw, file_record["member"], mor_tier, gra_tier)
            uid = 0
            for utterance in reader.utterances():
                if utterance.changeable_header is not None:
                    continue
                uid += 1
                found = database.execute("SELECT row_json FROM utterance WHERE collection=? AND transcript=? AND uid=?",
                                         (file_record["collection"], file_record["transcript"], uid)).fetchone()
                if found is None:
                    continue
                row = json.loads(found[0])
                if row["annotation_status"] != "annotated":
                    continue
                if utterance.participant != row["speaker"]:
                    raise ValueError(f"Raw speaker mismatch: {file_record['transcript']}:{uid}")
                raw_text = " ".join(token.word for token in utterance.tokens if token.word)
                if raw_text != row["text"] or utterance.tiers.get(utterance.participant, "") != row["chat_main_tier"]:
                    raise ValueError(f"Raw text mismatch: {file_record['transcript']}:{uid}")
                if mor_tier not in utterance.tiers or gra_tier not in utterance.tiers or not dependencies_aligned(utterance, gra_tier):
                    raise ValueError(f"Stored annotated row is not aligned in raw source: {file_record['transcript']}:{uid}")
                raw_strict = list(pair_edges(utterance, Counter()))
                stored = [json.loads(item[0]) for item in database.execute(
                    "SELECT signature FROM baseline WHERE collection=? AND transcript=? AND uid=? ORDER BY subject_index,verb_index",
                    (row["collection"], row["transcript"], uid))]
                raw_signatures = sorted([[str(pair[field]) for field in STRICT_IDENTITY_FIELDS] for pair in raw_strict],
                                        key=lambda item: (int(item[-2]), int(item[-1])))
                if raw_signatures != stored:
                    raise ValueError(f"Raw strict pair identity mismatch: {file_record['transcript']}:{uid}")
                if len(raw_strict) != int(row["n_pairs"]) or len(stored) != int(row["n_pairs"]):
                    raise ValueError(f"Source UD n_pairs mismatch: {file_record['transcript']}:{uid}")
                counts["n_annotated_utterances_verified"] += 1
                found_additions = list(copula_pairs(utterance))
                multiplicity = Counter(item["predicate_index"] for item in found_additions)
                counts["n_predicates_with_multiple_direct_pairs"] += sum(n > 1 for n in multiplicity.values())
                counts["n_extra_pairs_from_direct_multiplicity"] += sum(max(0, n - 1) for n in multiplicity.values())
                for addition in found_additions:
                    duplicate = database.execute(
                        "SELECT 1 FROM baseline WHERE collection=? AND transcript=? AND uid=? AND subject_index=? AND verb_index=?",
                        (row["collection"], row["transcript"], uid, int(addition["subject_index"]),
                         int(addition["verb_index"]))).fetchone()
                    if duplicate:
                        raise ValueError(f"Copula addition duplicates full strict pair key: {file_record['transcript']}:{uid}")
                    combined = {field: row.get(field, "") for field in pair_fields}
                    combined.update({key: addition[key] for key in addition if key in pair_fields})
                    audit = {**combined, **addition}
                    try:
                        database.execute("INSERT INTO addition VALUES (?,?,?,?,?,?)",
                                         (row["collection"], row["transcript"], uid,
                                          int(addition["subject_index"]), int(addition["verb_index"]), json.dumps(audit)))
                    except sqlite3.IntegrityError as error:
                        raise ValueError(f"Duplicate subject/copula indices: {file_record['transcript']}:{uid}") from error
                    counts["n_copula_additions"] += 1
                    copulas[addition["verb_lemma"]] += 1
                    predicates[(addition["predicate_pos"], addition["predicate_lemma"])] += 1
                    ages[age_group(row["target_child_age_months"])] += 1
                    if len(samples) < 20:
                        samples.append({"collection": row["collection"], "corpus": row["corpus"],
                                        "transcript": row["transcript"], "utterance_id": uid,
                                        "text": row["text"], **addition})
        database.commit()
        if counts["n_annotated_utterances_verified"] != counts["n_expected_annotated_utterances"]:
            raise ValueError("Not every supplied annotated utterance was visited and verified")
        with (stage / "copula_additions.csv").open("w", newline="") as audit_stream:
            audit_fields = pair_fields + ["pair_origin"] + [field for field in AUDIT_EXTRA if field not in pair_fields]
            writer = csv.DictWriter(audit_stream, fieldnames=audit_fields)
            writer.writeheader()
            for (payload,) in database.execute("SELECT row_json FROM addition ORDER BY rowid"):
                writer.writerow({**json.loads(payload), "pair_origin": "explicit_copula"})
        with pair_path.open(newline="") as old_stream, (stage / "english_ud_subject_verb_pairs.csv").open("w", newline="") as new_stream:
            old_reader = csv.DictReader(old_stream)
            old_row = next(old_reader, None)
            output_pair_fields = pair_fields + ["pair_origin"]
            writer = csv.DictWriter(new_stream, fieldnames=output_pair_fields); writer.writeheader()
            with utterance_path.open(newline="") as utterance_stream:
                for utterance_row in csv.DictReader(utterance_stream):
                    identity = (utterance_row["collection"], utterance_row["transcript"], utterance_row["utterance_id"])
                    merged = []
                    while old_row is not None and (old_row["collection"], old_row["transcript"], old_row["utterance_id"]) == identity:
                        merged.append((int(old_row["subject_index"]), int(old_row["verb_index"]), 0, old_row))
                        old_row = next(old_reader, None)
                    for (payload,) in database.execute("SELECT row_json FROM addition WHERE collection=? AND transcript=? AND uid=?",
                                                       (identity[0], identity[1], int(identity[2]))):
                        row = json.loads(payload)
                        merged.append((int(row["subject_index"]), int(row["verb_index"]), 1, row))
                    for _subject, _verb, _kind, row in sorted(merged):
                        output_row = {field: row.get(field, "") for field in pair_fields}
                        output_row["pair_origin"] = "strict_ud" if _kind == 0 else "explicit_copula"
                        writer.writerow(output_row)
                        counts["n_augmented_pairs"] += 1
            if old_row is not None:
                raise ValueError("Existing pair order does not follow supplied utterance order")
        counts["n_cds_utterances"] = metadata["counts"]["n_cds_utterances"]
        counts["n_pairs"] = counts["n_augmented_pairs"]
        if counts["n_augmented_pairs"] != counts["n_existing_pairs"] + counts["n_copula_additions"]:
            raise ValueError("Augmented total does not equal strict pairs plus copula additions")
        report = dict(metadata)
        report.update({
            "annotation_scheme": "Universal Dependencies plus explicit nonverbal-predicate copula links",
            "counts": dict(counts), "copula_lemma_counts": dict(copulas),
            "predicate_counts": [{"predicate_pos": pos, "predicate_lemma": lemma, "count": count}
                                 for (pos, lemma), count in predicates.most_common()],
            "additions_by_age": dict(ages), "sample_additions": samples,
            "augmentation_rule": "Exact case-insensitive NSUBJ to a non-VERB/AUX predicate, linked to each exact case-insensitive COP dependent of that predicate whose POS is VERB/AUX; no auxiliary, passive, progressive, conjunction, or inferred-subject bridges.",
            "direct_pairing_policy": "Emit every direct subject/copula combination sharing the same predicate index; multiplicity is counted and no subject or copula is inferred.",
            "input": {"population": str(population.resolve()), "utterances": str(utterance_path.resolve()),
                      "utterances_sha256": digest(utterance_path), "strict_pairs": str(pair_path.resolve()),
                      "strict_pairs_sha256": digest(pair_path), "metadata": str(metadata_path.resolve()),
                      "metadata_sha256": digest(metadata_path)},
            "raw_source_members_verified": True,
        })
        (stage / "metadata.json").write_text(json.dumps(report, indent=2) + "\n")
        database.close(); (stage / "index.sqlite").unlink()
        stage.rename(output)
        return report
    except BaseException:
        database.close()
        shutil.rmtree(stage, ignore_errors=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    augment(args.population, args.output)


if __name__ == "__main__":
    main()
