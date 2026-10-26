import unittest

from pagination import page


class PaginationTests(unittest.TestCase):
    def test_offset_and_limit(self):
        self.assertEqual(page(list(range(10)), offset=3, limit=2), [3, 4])

    def test_empty_page_and_end(self):
        self.assertEqual(page([1, 2], limit=0), [])
        self.assertEqual(page([1, 2], offset=20), [])

    def test_negative_arguments_are_rejected(self):
        for arguments in ({"offset": -1}, {"limit": -1}):
            with self.assertRaises(ValueError):
                page([1, 2], **arguments)

    def test_only_integers_are_accepted(self):
        for value in (True, "2", 1.5, None):
            for key in ("offset", "limit"):
                with self.assertRaises(TypeError):
                    page([1, 2], **{key: value})

    def test_input_is_preserved(self):
        items = [1, 2, 3]
        page(items, offset=1, limit=1)
        self.assertEqual(items, [1, 2, 3])
