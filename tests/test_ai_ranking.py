import unittest
from backend.ai.ranking import ranking_limit

class RankingTests(unittest.TestCase):
    def test_singular(self):
        for query in ("most expensive repair","what is the most expensive repair?","highest cost","cheapest repair"):
            self.assertEqual(ranking_limit(query),1)
    def test_plural(self):
        self.assertEqual(ranking_limit("most expensive repairs"),5)
    def test_explicit_counts(self):
        for query,n in (("top 3 most expensive repair",3),("3 most expensive repairs",3),("top one repair",1),("top 10 repairs",10)):
            self.assertEqual(ranking_limit(query),n)
