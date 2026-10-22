"""Wire-level API/E2E validation; requires the actual MCP SDK."""
import unittest

from vv_api import api_probe


class APIContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_strict_schema_and_real_stdio_handoff(self):
        observed = await api_probe()
        self.assertEqual(observed['strict_rejections'], [True, True, True])
        self.assertTrue(observed['missing_rejected'])
        self.assertEqual(observed['tools'], 5)
        self.assertEqual(observed['commit'], 'verified')
        self.assertTrue(observed['valid'])
        self.assertTrue(observed['payload_oracle'])
        self.assertFalse(observed['bad'])
        self.assertEqual(observed['bad_action'], 'SYSTEM_QUARANTINE')
        self.assertEqual(observed['unknown'], 'unknown')
        self.assertFalse(observed['unregistered'])
