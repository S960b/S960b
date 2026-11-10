import tempfile
import json
import pathlib
import unittest

from p4_compare import ARMS, InvalidStudy, evaluate, load_jsonl, net, validate_event

H = 'a' * 64
NOW = 1_795_000_000_000
CUT = NOW - 30 * 3_600_000
ENTRY = CUT + 90_000


def sample(eid='a' * 20, direction=('UP', 'DOWN', 'NEUTRAL')):
    return {
        'event_id': eid,
        'cutoff_ms': CUT,
        'entry_ms': ENTRY,
        'entry_price': 100.0,
        'features': {s: {'available_ms': CUT - 1000, 'digest': H} for s in ('news', 'market', 'social')},
        'decisions': {
            arm: {'direction': d, 'confidence': 0.6, 'model': 'gateway/deepseek',
                  'completed_ms': ENTRY - 1000, 'input_hash': H}
            for arm, d in zip(ARMS, direction)
        },
        'outcomes': [
            {'horizon_h': h, 'exit_ms': ENTRY + h * 3_600_000,
             'exit_price': 101.0, 'settled_ms': ENTRY + h * 3_600_000 + 120_000}
            for h in (1, 4, 24)
        ],
    }


class Phase4OfflineTests(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(validate_event(sample(), NOW))

    def test_metrics(self):
        result = evaluate([sample()], NOW)
        self.assertEqual(result['events'], 1)
        self.assertEqual(result['arms']['news']['horizons']['1']['direction_accuracy'], 1.0)
        self.assertEqual(result['arms']['news_market']['horizons']['1']['direction_accuracy'], 0.0)
        self.assertEqual(result['arms']['news_market_social']['horizons']['1']['directional_decisions'], 0)
        self.assertAlmostEqual(result['arms']['news']['horizons']['1']['scenarios']['0.0020']['mean_model_net'], 0.008)
        self.assertAlmostEqual(result['arms']['news_market']['horizons']['1']['scenarios']['0.0020']['mean_model_net'], -0.012)
        self.assertAlmostEqual(result['pairs']['news_market_minus_news']['1']['mean_delta_net']['0.0020'], -0.02)

    def test_zero_samples(self):
        self.assertIsNone(evaluate([], NOW)['arms']['news']['horizons']['1']['direction_accuracy'])

    def test_no_outcome_before_due(self):
        event = sample()
        event['outcomes'][0]['exit_ms'] -= 1
        with self.assertRaises(InvalidStudy): validate_event(event, NOW)

    def test_guard_required(self):
        event = sample()
        event['outcomes'][0]['settled_ms'] -= 1
        with self.assertRaises(InvalidStudy): validate_event(event, NOW)

    def test_late_feature_rejected(self):
        event = sample()
        event['features']['social']['available_ms'] += 1500
        with self.assertRaises(InvalidStudy): validate_event(event, NOW)

    def test_quote_before_response_rejected(self):
        event = sample()
        event['decisions']['news']['completed_ms'] = ENTRY + 1
        with self.assertRaises(InvalidStudy): validate_event(event, NOW)

    def test_unknown_field_rejected(self):
        event = sample(); event['raw_prompt'] = 'secret'
        with self.assertRaises(InvalidStudy): validate_event(event, NOW)

    def test_model_identity_required(self):
        event = sample(); event['decisions']['news']['model'] = 'hardcoded_model'
        with self.assertRaises(InvalidStudy): validate_event(event, NOW)

    def test_missing_social_is_partial_not_lookahead(self):
        event = sample(); del event['features']['social']; del event['decisions']['news_market_social']
        out = evaluate([event], NOW)
        self.assertEqual(out['arms']['news_market_social']['feature_available'], 0)
        self.assertEqual(out['pairs']['news_market_social_minus_news_market']['1']['matched_events'], 0)
        self.assertEqual(out['pairs']['news_market_minus_news']['1']['matched_events'], 1)

    def test_duplicate_events(self):
        with self.assertRaises(InvalidStudy): evaluate([sample(), sample()], NOW)

    def test_duplicate_horizons(self):
        event = sample(); event['outcomes'][1]['horizon_h'] = 1
        with self.assertRaises(InvalidStudy): validate_event(event, NOW)

    def test_overlapping_windows_are_reported(self):
        a = sample('a'*20); b = sample('b'*20)
        b['entry_ms'] += 5*60_000
        b['cutoff_ms'] += 5*60_000
        for source in b['features'].values(): source['available_ms'] += 5*60_000
        for d in b['decisions'].values(): d['completed_ms'] += 5*60_000
        for o in b['outcomes']: o['exit_ms'] += 5*60_000; o['settled_ms'] += 5*60_000
        self.assertEqual(evaluate([a,b],NOW)['arms']['news']['horizons']['24']['overlapping_adjacent_windows'],1)

    def test_neutral_zero_cost(self):
        self.assertEqual(net('NEUTRAL',0.05,0.003),0)

    def test_nonfinite_confidence(self):
        event = sample(); event['decisions']['news']['confidence'] = float('nan')
        with self.assertRaises(InvalidStudy): validate_event(event, NOW)

    def test_immature_labels_not_scored(self):
        event = sample(); event['outcomes'] = [event['outcomes'][2]]
        self.assertEqual(evaluate([event], ENTRY+4*3_600_000)['arms']['news']['horizons']['24']['labeled'], 0)

    def test_jsonl_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory)/'sealed.jsonl'
            path.write_text(json.dumps(sample())+'\n',encoding='utf8')
            self.assertEqual(len(load_jsonl(str(path),NOW)),1)
            self.assertEqual(path.read_text(encoding='utf8').count('\n'),1)

    def test_jsonl_duplicate_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory)/'sealed.jsonl'
            path.write_text((json.dumps(sample())+'\n')*2,encoding='utf8')
            with self.assertRaises(InvalidStudy): load_jsonl(str(path),NOW)

    def test_jsonl_reject_db(self):
        with self.assertRaises(InvalidStudy): load_jsonl('/home/kali/research/news-phase3/news.sqlite',NOW)


if __name__ == '__main__':
    unittest.main()
