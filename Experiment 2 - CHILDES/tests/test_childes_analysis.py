import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
import spacy
import analyze_complete_dataset_96mos as original
from run_childes_analysis import prepare_parser_input, CachedPairs, run_analysis, sha256
from parse_sharded_spacy import parse_sharded


class CachedAnalysisTest(unittest.TestCase):
    def test_analysis_refuses_failed_or_stale_verification(self):
        for status, matching_hash in [('failed', True), ('passed', False)]:
            with self.subTest(status=status, matching_hash=matching_hash), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'audit').mkdir()
                (root / 'data').mkdir()
                source = root / 'data/childes_utterances.csv'
                source.write_text('full_utterance,target_child_age\nI see you.,24\n')
                (root / 'audit/extraction_status.txt').write_text('complete')
                (root / 'audit/export_verification.json').write_text(json.dumps({
                    'status': status, 'csv_sha256': sha256(source) if matching_hash else 'stale',
                }))
                with self.assertRaisesRegex(ValueError, 'verification'):
                    run_analysis(root, shards=1)
                self.assertFalse((root / 'parser_input').exists())

    def test_sharded_cache_matches_original_and_keeps_age_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = pd.DataFrame({
                'full_utterance': ['I see you.', None, 'Birds fly.', 'They sleep.', 'Dogs bark.', 'Rain fell.'],
                'target_child_age': [11.9, 20, 12, 96, 96.1, None],
                'collection_name': ['Eng-NA'] * 6, 'corpus_name': ['Fixture'] * 6,
                'transcript_id': [100] * 6, 'utterance_id': range(6),
            })
            source = root / 'source.csv'
            raw.to_csv(source, index=False)
            prepared = root / 'prepared'
            frame = prepare_parser_input(source, prepared)
            self.assertEqual(frame.index.tolist(), [0, 2, 3])
            parse_sharded(prepared / 'utterances.csv', prepared / 'metadata.json', root / 'parsed', shards=2)
            cache = CachedPairs(root / 'parsed/english_ud_subject_verb_pairs.csv', frame.index)
            expected = [('i', 'see'), ('bird', 'fly'), ('they', 'sleep')]
            self.assertEqual(cache(frame, None), expected)
            nlp = spacy.load('en_core_web_sm')
            self.assertEqual(cache(frame, None), original.extract_subject_verb_pairs(frame, nlp))
            self.assertEqual(cache(frame[frame.target_child_age < 12], None), [('i', 'see')])
            self.assertEqual(cache(frame[(frame.target_child_age >= 12) & (frame.target_child_age < 24)], None), [('bird', 'fly')])
            self.assertEqual(cache(frame[(frame.target_child_age >= 84) & (frame.target_child_age < 96)], None), [])
            metadata = json.loads((prepared / 'metadata.json').read_text())
            self.assertEqual(metadata['counts']['n_cds_utterances'], 3)


if __name__ == '__main__':
    unittest.main()
