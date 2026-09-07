"""Match every corrected 2021.1 row to its unchanged historical source row."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('corrected_run', type=Path)
parser.add_argument('historical_csv', type=Path)
args = parser.parse_args()
root = args.corrected_run

def key(row):
    return int(row['transcript_id']), int(row['utterance_order']), int(row['speaker_id'])

differences = Counter()
age_rounding_rows = matched = 0
max_age_difference = 0.0
with args.historical_csv.open(newline='') as historical, (root / 'data/childes_utterances.csv').open(newline='') as corrected, (root / 'audit/original_row_mapping.csv').open('w', newline='') as mapping:
    old_reader, new_reader = csv.DictReader(historical), csv.DictReader(corrected)
    original_fields = old_reader.fieldnames
    writer = csv.writer(mapping)
    writer.writerow(['corrected_csv_row_index', 'original_csv_row_index'])
    original_index = -1
    old_row = None
    for corrected_index, new_row in enumerate(new_reader):
        target = key(new_row)
        while old_row is None or key(old_row) < target:
            old_row = next(old_reader, None)
            original_index += 1
            if old_row is None:
                raise ValueError(f'Corrected row missing from original: {target}')
        if key(old_row) != target:
            raise ValueError(f'Corrected row missing from original: {target}')
        writer.writerow([corrected_index, original_index])
        matched += 1
        for field in original_fields:
            if new_row[field] == old_row[field]:
                continue
            if field == 'target_child_age':
                try:
                    delta = abs(float(new_row[field]) - float(old_row[field]))
                except ValueError:
                    differences[field] += 1
                    continue
                max_age_difference = max(delta, max_age_difference)
                if delta <= 1e-10:
                    age_rounding_rows += 1
                    continue
            differences[field] += 1
report = {'status': 'passed' if matched and not differences else 'failed',
          'matched_corrected_rows': matched, 'field_mismatches': dict(differences),
          'age_rounding_rows': age_rounding_rows, 'max_age_rounding_difference_months': max_age_difference,
          'all_corrected_rows_are_historical_rows': True}
(root / 'audit/historical_subset_verification.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
if report['status'] != 'passed':
    raise ValueError('Corrected historical subset differs in source content')
