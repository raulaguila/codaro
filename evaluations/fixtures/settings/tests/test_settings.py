import unittest

from settings import merge_settings


class SettingsTests(unittest.TestCase):
    def test_explicit_falsy_values_override_defaults(self):
        self.assertEqual(
            merge_settings(
                {"enabled": True, "retries": 3, "name": "old"},
                {"enabled": False, "retries": 0, "name": ""},
            ),
            {"enabled": False, "retries": 0, "name": ""},
        )

    def test_none_means_use_default(self):
        self.assertEqual(merge_settings({"retries": 3}, {"retries": None}), {"retries": 3})

    def test_new_fields_are_preserved(self):
        self.assertEqual(merge_settings({}, {"extra": 0}), {"extra": 0})

    def test_input_mappings_are_unchanged(self):
        defaults, overrides = {"retries": 3}, {"retries": 1}
        result = merge_settings(defaults, overrides)
        self.assertEqual(defaults, {"retries": 3})
        self.assertEqual(overrides, {"retries": 1})
        self.assertIsNot(result, defaults)
