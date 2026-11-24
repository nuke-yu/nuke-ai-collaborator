"""Multi-turn conversation runtime boundary.

The Orchestrator selects this execution mode for ordinary group conversations.
Session persistence and Memory remain owned by their existing bounded contexts.
"""

EXECUTION_MODE = "multi_conversation"
