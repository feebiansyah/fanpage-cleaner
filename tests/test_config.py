import unittest

from config import Settings


class BulkSettings(unittest.TestCase):
    def test_capacity_default_and_hard_ceiling(self):
        self.assertEqual(Settings().bulk_delete_max_targets, 5000)
        for value in (1, 20, 5000):
            self.assertEqual(Settings(bulk_delete_max_targets=value).bulk_delete_max_targets, value)
        for value in (0, -1, 5001, True, 20.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Settings(bulk_delete_max_targets=value)

    def test_confirmation_ttl_default_and_bounds(self):
        self.assertEqual(Settings().bulk_confirmation_ttl, 600)
        for value in (60, 600, 900):
            self.assertEqual(Settings(bulk_confirmation_ttl=value).bulk_confirmation_ttl, value)
        for value in (59, 901, True, 60.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Settings(bulk_confirmation_ttl=value)
