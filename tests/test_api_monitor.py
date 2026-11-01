"""Offline checks for API cost pagination and threshold handling."""
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import api_monitor


class APIAlertTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.events = []
        self.monitor = api_monitor.APIMonitor(Path(self.temp.name), lambda *event: self.events.append(event) or True)
        self.monitor.key = 'test-key'

    def test_cost_sums_pages_and_warns_at_threshold(self):
        pages = [
            {'data':[{'results':[{'amount':{'currency':'usd','value':40}}]}], 'has_more':True, 'next_page':'next'},
            {'data':[{'results':[{'amount':{'currency':'usd','value':50}}]}], 'has_more':False},
        ]
        def response(*args, **kwargs):
            return io.BytesIO(json.dumps(pages.pop(0)).encode())
        with patch.object(api_monitor.urllib.request, 'urlopen', side_effect=response), patch.object(self.monitor, 'collect_details'):
            self.monitor.collect()
        self.assertEqual(self.monitor.status()['cost']['amount'], 90)
        self.assertEqual(len(self.events), 1)
        self.assertTrue(self.events[0][0].startswith('api-cost:'))

    def test_bad_currency_cannot_generate_a_cost(self):
        page = {'data':[{'results':[{'amount':{'currency':'eur','value':100}}]}], 'has_more':False}
        with patch.object(api_monitor.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(page).encode())), patch.object(self.monitor, 'collect_details'):
            self.monitor.collect()
        self.assertNotIn('cost', self.monitor.status())
        self.assertTrue(self.monitor.status()['cost_error'])
        self.assertEqual(self.events, [])


if __name__ == '__main__':
    unittest.main()
