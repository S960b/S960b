import json, time, unittest
from unittest.mock import patch
from diag35o import metric_value, summarize_logs, CATEGORIES

class TestEvidence(unittest.TestCase):
    def test_metric(self):
        self.assertEqual(metric_value('commands_poll_last_successful_timestamp_seconds 1790000000\n', 'commands_poll_last_successful_timestamp_seconds'), 1790000000)
    def test_labels_not_accepted(self):
        self.assertIsNone(metric_value('commands_poll_last_successful_timestamp_seconds{key="private"} 3\n', 'commands_poll_last_successful_timestamp_seconds'))
    def test_secret_never_emitted(self):
        entry={'MESSAGE':'secret ABCDE  MCP upstream error; unsupported channel', '__REALTIME_TIMESTAMP':str(int(time.time()*1e6))}
        result=summarize_logs(json.dumps(entry)+'\n')
        self.assertEqual(result[2]['upstream_error'],1)
        self.assertEqual(result[3]['unsupported_channel'],1)
        self.assertNotIn('ABCDE',str(result))
    def test_no_raw_errors(self):
        self.assertEqual(summarize_logs('{bad\n')[0],0)
    def test_response_count(self):
        entry={'MESSAGE':'MCP 403 harpoon unauthorized', '__REALTIME_TIMESTAMP':str(int(time.time()*1e6))}
        counts=summarize_logs(json.dumps(entry)+'\n')[2]
        self.assertEqual(counts['http_403'],1)
        self.assertEqual(counts['harpoon'],1)

if __name__=='__main__':unittest.main()
