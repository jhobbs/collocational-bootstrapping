"""Parse a matched utterance CSV with independent contiguous spaCy workers."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from parse_matched_spacy import sha256_file


def _input_inventory(path, text_column):
    total = included = 0
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        required = ["included_in_cds", "collection", "corpus", "transcript",
                    "utterance_id", "target_child_age_months", text_column]
        missing = [field for field in required if field not in fields]
        if missing:
            raise ValueError(f"Utterance CSV is missing required columns: {', '.join(missing)}")
        for row in reader:
            total += 1
            included += row["included_in_cds"].strip().lower() == "true"
    return fields, total, included


def _make_shards(source, stage, fields, included, shards, input_metadata):
    actual = min(shards, max(included, 1))
    chunk_size = math.ceil(included / actual) if included else 1
    paths = [stage / f"shard-{index:03d}.csv" for index in range(actual)]
    streams = [path.open("w", newline="") for path in paths]
    writers = [csv.DictWriter(stream, fieldnames=fields) for stream in streams]
    counts = [0] * actual
    try:
        for writer in writers:
            writer.writeheader()
        selected = 0
        with source.open(newline="") as stream:
            for row in csv.DictReader(stream):
                if row["included_in_cds"].strip().lower() != "true":
                    continue
                shard = min(selected // chunk_size, actual - 1)
                writers[shard].writerow(row)
                counts[shard] += 1
                selected += 1
    finally:
        for stream in streams:
            stream.close()
    if sum(counts) != included:
        raise RuntimeError(f"Wrote {sum(counts)} shard rows, expected {included}")
    metadata_paths = []
    for index, count in enumerate(counts):
        path = stage / f"shard-{index:03d}-metadata.json"
        path.write_text(json.dumps({
            "scope": input_metadata.get("scope"),
            "collection": input_metadata.get("collection"),
            "counts": {"n_cds_utterances": count},
        }) + "\n")
        metadata_paths.append(path)
    return paths, metadata_paths, counts


def _terminate(processes):
    for process in processes:
        if process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + 5
    for process in processes:
        if process.poll() is None:
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
    for process in processes:
        if process.poll() is None:
            process.wait()


def parse_sharded(utterances, metadata_path, output, text_column="text",
                  shards=8, model="en_core_web_sm"):
    utterances = Path(utterances).resolve()
    metadata_path = Path(metadata_path).resolve()
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    if shards < 1:
        raise ValueError("shards must be positive")
    fields, n_input_rows, n_included = _input_inventory(utterances, text_column)
    input_metadata = json.loads(metadata_path.read_text())
    expected = input_metadata.get("counts", {}).get("n_cds_utterances")
    if isinstance(expected, int) and expected != n_included:
        raise ValueError(
            f"Input metadata n_cds_utterances={expected} differs from supplied "
            f"included utterance count {n_included}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".parse-sharded-spacy-", dir=output.parent))
    processes = []
    logs = []
    try:
        shard_paths, shard_metadata, shard_counts = _make_shards(
            utterances, stage, fields, n_included, shards, input_metadata)
        parser = Path(__file__).with_name("parse_matched_spacy.py")
        for index, (shard_path, meta_path) in enumerate(zip(shard_paths, shard_metadata)):
            log = (stage / f"shard-{index:03d}.log").open("w")
            logs.append(log)
            command = [sys.executable, str(parser), "--utterances", str(shard_path),
                       "--metadata", str(meta_path), "--output",
                       str(stage / f"shard-{index:03d}-output"), "--text-column",
                       text_column, "--n-process", "1", "--model", model]
            processes.append(subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT))
        pending = set(range(len(processes)))
        while pending:
            for index in list(pending):
                status = processes[index].poll()
                if status is None:
                    continue
                pending.remove(index)
                if status:
                    _terminate(processes)
                    logs[index].flush()
                    detail = (stage / f"shard-{index:03d}.log").read_text()
                    raise RuntimeError(f"spaCy shard {index} failed ({status}):\n{detail}")
                print(f"Completed shard {index + 1}/{len(processes)}", flush=True)
            if pending:
                time.sleep(0.2)
        for log in logs:
            log.close()
        logs.clear()

        merged_path = stage / "english_ud_subject_verb_pairs.csv"
        shard_records = []
        merged_header = None
        n_pairs = 0
        parser_metadata = None
        with merged_path.open("w", newline="") as destination:
            writer = None
            for index, (shard_path, expected_count) in enumerate(zip(shard_paths, shard_counts)):
                worker_root = stage / f"shard-{index:03d}-output"
                worker_meta_path = worker_root / "metadata.json"
                worker_meta = json.loads(worker_meta_path.read_text())
                if worker_meta["counts"]["n_included_utterances"] != expected_count:
                    raise RuntimeError(f"Shard {index} utterance count changed")
                if parser_metadata is None:
                    parser_metadata = worker_meta["parser"]
                pair_path = worker_root / "english_ud_subject_verb_pairs.csv"
                with pair_path.open(newline="") as source:
                    reader = csv.DictReader(source)
                    if merged_header is None:
                        merged_header = reader.fieldnames
                        writer = csv.DictWriter(destination, fieldnames=merged_header)
                        writer.writeheader()
                    elif reader.fieldnames != merged_header:
                        raise RuntimeError(f"Shard {index} pair header differs")
                    copied = 0
                    for row in reader:
                        writer.writerow(row)
                        copied += 1
                if copied != worker_meta["counts"]["n_pairs"]:
                    raise RuntimeError(f"Shard {index} pair count changed")
                n_pairs += copied
                shard_records.append({
                    "shard_id": index, "n_utterances": expected_count,
                    "n_pairs": copied, "utterances_sha256": sha256_file(shard_path),
                    "worker_metadata_sha256": sha256_file(worker_meta_path),
                })
        report = {
            "scope": input_metadata.get("scope"),
            "collection": input_metadata.get("collection"),
            "annotation_scheme": "spaCy",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "counts": {"n_input_rows": n_input_rows,
                       "n_included_utterances": n_included, "n_pairs": n_pairs},
            "input": {
                "utterances_path": str(utterances),
                "utterances_sha256": sha256_file(utterances),
                "metadata_path": str(metadata_path),
                "metadata_sha256": sha256_file(metadata_path),
                "text_column": text_column,
                "renamed_columns": ({
                    "n_pairs": "source_ud_n_pairs (pair count from the source UD annotation)"
                } if "n_pairs" in fields else {}),
            },
            "parser": parser_metadata,
            "denominator": {
                "utterance_csv": str(utterances),
                "included_column": "included_in_cds",
                "note": "All supplied included utterances are counted, including utterances with zero extracted pairs.",
            },
            "sharding": {"requested_shards": shards, "completed_shards": len(shard_records),
                         "worker_n_process": 1, "merge_order": "ascending contiguous shard_id"},
            "shards": shard_records,
        }
        if "matching_provenance" in input_metadata:
            report["matching_provenance"] = input_metadata["matching_provenance"]
        (stage / "metadata.json").write_text(json.dumps(report, indent=2) + "\n")
        for path in list(stage.iterdir()):
            if path.name not in {"english_ud_subject_verb_pairs.csv", "metadata.json"}:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
        stage.rename(output)
    except BaseException:
        _terminate(processes)
        for log in logs:
            log.close()
        shutil.rmtree(stage, ignore_errors=True)
        raise
    print(json.dumps(report["counts"], indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--utterances", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--text-column", default="text")
    parser.add_argument("--shards", type=int, default=8)
    parser.add_argument("--model", default="en_core_web_sm")
    args = parser.parse_args()
    try:
        parse_sharded(args.utterances, args.metadata, args.output, args.text_column,
                      args.shards, args.model)
    except (FileExistsError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
