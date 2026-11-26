import unittest
from backend.ai.board_spend import spending_answer

class BoardSpendTests(unittest.TestCase):
    def test_compound_query(self):
        result=spending_answer("Analyze repair spend breakdown under $500 vs over $500 and top shops",[
            {"cost":150,"shop":"A"},{"cost":500,"shop":"B"},{"cost":900,"shop":"B"},{"cost":None,"shop":"C"}])
        self.assertIn("$1,550.00",result)
        self.assertIn("1 cases · $150.00",result)
        self.assertIn("2 cases · $1,400.00",result)
        self.assertIn("**B** — $1,400.00",result)
    def test_simple_threshold_remains_separate(self):
        self.assertIsNone(spending_answer("show repairs under $500",[]))
    def test_mixed_dimensions_not_partially_answered(self):
        self.assertIsNone(spending_answer("compare repair spend and unassigned cases",[]))
