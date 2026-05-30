import unittest
from bus.events import _registry, TicketCreated, CodeCommitted, TaskFailed
import dataclasses

class TestDomainEvents(unittest.TestCase):
    def test_events_registered(self):
        self.assertIn("rd_ticket_created", _registry)
        self.assertIn("rd_code_committed", _registry)
        self.assertIn("rd_task_failed", _registry)

    def test_ticket_created_structure(self):
        ev = TicketCreated(
            group_id=1,
            ticket_id="JIRA-101",
            title="Fix Bug",
            description="Details",
            priority="high",
            creator_id=5
        )
        self.assertEqual(ev.type, "rd_ticket_created")
        self.assertEqual(ev.group_id, 1)
        self.assertEqual(ev.ticket_id, "JIRA-101")
        
        # Verify it's a dataclass
        self.assertTrue(dataclasses.is_dataclass(ev))

    def test_code_committed_structure(self):
        ev = CodeCommitted(
            group_id=1,
            ticket_id="JIRA-101",
            files=["app.py", "test.py"],
            commit_msg="finished coding",
            author_id=7
        )
        self.assertEqual(ev.type, "rd_code_committed")
        self.assertEqual(ev.files[0], "app.py")

if __name__ == "__main__":
    unittest.main()
