import unittest

from auth import can_edit


class AuthorizationTests(unittest.TestCase):
    def test_missing_user_is_denied(self):
        self.assertFalse(can_edit(None))

    def test_missing_role_is_denied(self):
        self.assertFalse(can_edit({}))

    def test_editor_is_allowed(self):
        self.assertTrue(can_edit({"role": "editor"}))

    def test_admin_is_allowed(self):
        self.assertTrue(can_edit({"role": "admin"}))

    def test_other_roles_are_denied(self):
        self.assertFalse(can_edit({"role": "viewer"}))


if __name__ == "__main__":
    unittest.main()
