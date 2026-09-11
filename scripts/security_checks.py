#!/usr/bin/env python3
"""Offline hostile-input checks for the research portal client. No live attack traffic."""
import argparse
import io
import os
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sept11.adapters import portal as api
from sept11 import config


class SecurityChecks(unittest.TestCase):
    def test_live_search_is_opt_in(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(config.Config().allow_live_search)

    def test_origin_contract(self):
        for url in ['http://sept11documents.cityofnewyork.us/x',
                    'https://user:secret@sept11documents.cityofnewyork.us/x',
                    'https://sept11documents.cityofnewyork.us:444/x',
                    'https://localhost/x']:
            with self.subTest(url=url), self.assertRaises(api.PortalError):
                api.PortalClient()._throttle(url)

    def test_redirect_denied(self):
        handler = api.NoRedirect()
        with self.assertRaises(api.PortalError):
            handler.redirect_request(urllib.request.Request(api.FRONT), None, 302, 'redirect', {}, 'http://127.0.0.1/')

    def test_response_size(self):
        with self.assertRaises(api.PortalError):
            api.read_bounded(io.BytesIO(b'x' * 33), 32)
        self.assertEqual(api.read_bounded(io.BytesIO(b'x' * 32), 32), b'x' * 32)

    def test_catalog_rejects_partial_and_duplicate(self):
        for csv in [api._FIXTURE_CSV + 'bad;not-a-bates\n',
                    api._FIXTURE_CSV.replace(';149;4584946;', ';-1;4584946;'),
                    api._FIXTURE_CSV.replace('NYC-WTC_000136670','NYC-WTC_000058159')]:
            with self.subTest(csv=csv[-80:]), self.assertRaises((api.PortalError, ValueError)):
                api.parse_catalog_csv(csv)

    def test_missing_cursor_fails(self):
        response = {'resultset': {'results': [api._FIXTURE_RESULT], 'next_avail': True}}
        with patch.object(api.PortalClient, 'search', return_value=response):
            with self.assertRaises(api.PortalError):
                list(api.PortalClient().iter_results('ALL extension:pdf', max_results=3))

    def test_service_cursor_forwarded(self):
        state = {'id': 'unnamed', 'digest': 'fixture', 'state_base64': 'fixture'}
        first = {'resultset': {'results': [api._FIXTURE_RESULT], 'next_avail': True,
                              'per_service_dataset': [{'paging_state': state}]}}
        with patch.object(api.PortalClient, 'search', side_effect=[first, {'resultset': {'results': []}}]) as search:
            list(api.PortalClient().iter_results('ALL extension:pdf'))
            self.assertEqual(search.call_args.kwargs['paging_state'], [state])

    def test_cached_size_mismatch(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'NYC-WTC_000000001.pdf'
            p.write_bytes(b'%PDF-fake')
            with self.assertRaises(api.PortalError):
                api.PortalClient().fetch_pdf('NYC-WTC_000000001', Path(folder), expected_size=99)

    def test_valid_cached_pdf_is_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'NYC-WTC_000000001.pdf'
            p.write_bytes(b'%PDF-fake')
            client = api.PortalClient()
            with patch.object(client, '_request', side_effect=AssertionError('cache miss')):
                self.assertEqual(client.fetch_pdf('NYC-WTC_000000001', Path(folder)), p)

    def test_bad_pdf_never_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(api.PortalClient, '_request', return_value=(b'%PDF-fake', 'application/pdf')):
                with self.assertRaises(api.PortalError):
                    api.PortalClient().fetch_pdf('NYC-WTC_000000001', Path(folder), expected_size=99)
            self.assertFalse(list(Path(folder).glob('*.pdf')))

    def test_repeated_paging_fails(self):
        response = {'resultset': {'results': [api._FIXTURE_RESULT], 'next_avail': True, 'paging_state': {'x': 1}}}
        with patch.object(api.PortalClient, 'search', return_value=response):
            with self.assertRaises(api.PortalError):
                list(api.PortalClient().iter_results('ALL extension:pdf', max_results=3))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selftest', action='store_true')
    parser.parse_args()
    result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(SecurityChecks))
    print(f'examined {result.testsRun} security test groups; network calls: 0')
    raise SystemExit(0 if result.wasSuccessful() else 1)
