"""Group-local durable receipts for online task dispatches."""
ONLINE_DISPATCH_RECEIPT_DDL = '''CREATE TABLE IF NOT EXISTS online_dispatch_receipts (
    task_id TEXT PRIMARY KEY, group_id INTEGER NOT NULL,
    attempt INTEGER NOT NULL, payload_hash TEXT NOT NULL
)'''
