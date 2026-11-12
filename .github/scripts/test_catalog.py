"""Tests for catalog.py, the catalog parsing shared by the catalog scripts."""
import unittest

import catalog


class GithubRepo(unittest.TestCase):
    def test_https_url(self):
        self.assertEqual(catalog.github_repo({"source": "url", "url": "https://github.com/jagp/omnilog.git"}),
                         ("jagp/omnilog", ""))

    def test_other_host_is_skipped(self):
        self.assertIsNone(catalog.github_repo({"source": "url", "url": "https://gitlab.com/x/y.git"}))

    def test_subdir_keeps_its_folder(self):
        self.assertEqual(catalog.github_repo({"source": "git-subdir", "url": "https://github.com/a/b.git", "path": "plugins/p/"}),
                         ("a/b", "plugins/p"))


class LoadCatalog(unittest.TestCase):
    def test_lists_the_gitflow_plugin(self):
        self.assertIn("cc-gitflow-regulator", [entry["name"] for entry in catalog.load_catalog()])


if __name__ == "__main__":
    unittest.main()
