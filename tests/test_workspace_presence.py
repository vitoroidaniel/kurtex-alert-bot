import unittest

from backend.core.workspace_presence import PresenceHub


class PresenceTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.hub = PresenceHub(ttl=30, event_limit=3, clock=lambda: self.now)

    def test_broadcast_and_identity(self):
        a = self.hub.join(12)
        self.assertTrue(self.hub.publish(a, "dragging", "KT-10", name="Agent"))
        cursor, events = self.hub.changes(0, "22")
        self.assertEqual(events[0]["agent_id"], "12")
        self.assertEqual(events[0]["action"], "dragging")
        self.assertEqual(self.hub.changes(0, "12")[1], [])

    def test_reject_bad_case_and_field(self):
        a = self.hub.join(12)
        self.assertFalse(self.hub.publish(a, "editing", "../etc/passwd", name="A"))
        self.assertFalse(self.hub.publish(a, "editing", "KT-10", "<script>", "A"))
        self.assertFalse(self.hub.publish(a, "unknown", "KT-10", name="A"))

    def test_leave_and_multiple_tabs(self):
        a = self.hub.join(12)
        b = self.hub.join(12)
        self.hub.publish(a, "viewing", "KT-1", name="A")
        self.hub.publish(b, "editing", "KT-2", name="A")
        cursor = self.hub.seq
        self.hub.leave(a)
        self.assertEqual(self.hub.changes(cursor, "99")[1], [])
        self.hub.leave(b)
        self.assertEqual(self.hub.changes(cursor, "99")[1][-1]["action"], "left")

    def test_timeout_and_snapshot(self):
        a = self.hub.join(12)
        self.hub.publish(a, "viewing", "KT-1", name="A")
        self.assertEqual(len(self.hub.snapshot(99)[1]), 1)
        self.now = 31
        self.assertEqual(self.hub.snapshot(99)[1], [])
        self.assertEqual(self.hub.changes(0, "99")[1][-1]["action"], "left")

    def test_idle_socket_can_publish_after_expiry(self):
        a = self.hub.join(12)
        self.hub.publish(a, "viewing", "KT-1", name="A")
        self.now = 31
        self.hub.snapshot(99)
        self.assertTrue(self.hub.publish(a, "editing", "KT-1", name="A"))
        self.assertEqual(self.hub.snapshot(99)[1][0]["action"], "editing")

    def test_bounded_log_requires_resync(self):
        a = self.hub.join(12)
        for i in range(5):
            self.hub.publish(a, "viewing", f"KT-{i}", name="A")
        self.assertIsNone(self.hub.changes(0, "99")[1])


if __name__ == "__main__":
    unittest.main()
