import math
import unittest
from p4_market_features import closed_precutoff_features, MarketFeatureError, HOUR_MS

START = 1_700_000_000_000 // HOUR_MS * HOUR_MS

def fixtures(n=27):
    return [[START+i*HOUR_MS, '100', '100', '100', str(100+i),
             str(10+i), START+(i+1)*HOUR_MS-1] for i in range(n)]

class MarketTests(unittest.TestCase):
    def test_closed_data_and_digest(self):
        candles=fixtures()
        cutoff=START+27*HOUR_MS-5_000
        result=closed_precutoff_features(candles,cutoff)
        self.assertEqual(result['candle_count'],26)
        self.assertEqual(result['last_closed_open_ms'],START+25*HOUR_MS)
        self.assertLessEqual(result['available_ms'],cutoff)
        self.assertEqual(len(result['digest']),64)
        self.assertEqual(result,closed_precutoff_features(candles,cutoff))
        self.assertAlmostEqual(result['values']['return_1h'],125/124-1)

    def test_unfinished_candle_never_used(self):
        candles=fixtures(); cutoff=START+27*HOUR_MS-5_000
        baseline=closed_precutoff_features(candles,cutoff)
        candles[-1][4]='9999999999'; candles[-1][5]='9999999999'
        self.assertEqual(baseline,closed_precutoff_features(candles,cutoff))

    def test_pre_cutoff_margin(self):
        candles=fixtures(26)
        with self.assertRaises(MarketFeatureError):
            closed_precutoff_features(candles,START+26*HOUR_MS+1_000)

    def test_gap(self):
        candles=fixtures(27); del candles[9]
        with self.assertRaises(MarketFeatureError):
            closed_precutoff_features(candles,START+27*HOUR_MS-1)

    def test_duplicate(self):
        candles=fixtures(27); candles[4]=candles[3].copy()
        with self.assertRaises(MarketFeatureError):
            closed_precutoff_features(candles,START+27*HOUR_MS-1)

    def test_stale(self):
        with self.assertRaises(MarketFeatureError):
            closed_precutoff_features(fixtures(27),START+40*HOUR_MS)

    def test_invalid_price(self):
        candles=fixtures(27); candles[0][4]='nan'
        with self.assertRaises(MarketFeatureError):
            closed_precutoff_features(candles,START+27*HOUR_MS-1)

    def test_invalid_cutoff(self):
        with self.assertRaises(MarketFeatureError):
            closed_precutoff_features(fixtures(27),True)

    def test_insufficient(self):
        with self.assertRaises(MarketFeatureError):
            closed_precutoff_features(fixtures(25), START+30*HOUR_MS)

    def test_missing_zero_volume_guard(self):
        candles=fixtures(27)
        for row in candles: row[5]='0'
        result=closed_precutoff_features(candles,START+27*HOUR_MS-5_000)
        self.assertIsNone(result['values']['volume_z_1h'])
        self.assertIsNone(result['values']['volume_change_4h'])

if __name__=='__main__': unittest.main()

