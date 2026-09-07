import pickle
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analyze_age_groups_96mos as analysis


class AgePlotTest(unittest.TestCase):
    def test_overall_reference_uses_this_runs_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'age_groups_complete_96mos'
            output.mkdir()
            overall = root / 'complete_dataset_96mos'
            overall.mkdir()
            with (overall / 'summary_96mos.pkl').open('wb') as stream:
                pickle.dump({'optimal_z': 1.52}, stream)
            results = [{'age_group': f'{age}-{age + 12}mo', 'z': 1.50, 'n_utterances': 100, 'n_pairs': 50}
                       for age in range(0, 96, 12)]
            # Keep the real generated figure open to inspect the plotted result.
            with patch.object(analysis.plt, 'close'):
                analysis.plot_z_by_age(results, str(output))
                texts = [text.get_text() for text in analysis.plt.gca().texts]
                self.assertTrue(any('Overall' in text and '1.52' in text for text in texts), texts)
            analysis.plt.close('all')


if __name__ == '__main__':
    unittest.main()
