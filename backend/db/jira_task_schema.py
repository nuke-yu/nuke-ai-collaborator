"""Central tables for the online single-Story execution queue."""

JIRA_TASK_TABLES = {"jira_task_runs", "jira_story_jobs", "jira_run_stories", "jira_job_attempts", "task_attempt_delivery"}

JIRA_TASK_DDL = (
    """CREATE TABLE IF NOT EXISTS task_attempt_delivery (
        job_id INTEGER NOT NULL REFERENCES jira_story_jobs(id),
        attempt INTEGER NOT NULL,
        event_floor INTEGER NOT NULL DEFAULT 0,
        pr_url TEXT,
        pr_state TEXT,
        validation TEXT,
        PRIMARY KEY (job_id, attempt)
    )""",
    """CREATE TABLE IF NOT EXISTS jira_task_runs (
        id TEXT PRIMARY KEY,
        site TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind = 'story'),
        issue_key TEXT NOT NULL,
        mappings TEXT NOT NULL,
        max_parallel INTEGER NOT NULL,
        operator_id TEXT NOT NULL,
        discovery TEXT NOT NULL DEFAULT 'pending',
        generation INTEGER NOT NULL DEFAULT 1,
        cursor TEXT,
        discovery_attempts INTEGER NOT NULL DEFAULT 0,
        retry_at REAL NOT NULL DEFAULT 0,
        paused INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        UNIQUE (site, kind, issue_key)
    )""",
    """CREATE TABLE IF NOT EXISTS jira_story_jobs (
        id INTEGER PRIMARY KEY,
        site TEXT NOT NULL,
        issue_id TEXT NOT NULL,
        issue_key TEXT NOT NULL,
        task_id TEXT NOT NULL UNIQUE,
        requirements TEXT NOT NULL,
        mapping TEXT,
        state TEXT NOT NULL,
        attempt INTEGER NOT NULL DEFAULT 0,
        claimed_run TEXT REFERENCES jira_task_runs(id),
        pr_url TEXT,
        pr_state TEXT,
        pr_checked_at REAL NOT NULL DEFAULT 0,
        validation TEXT,
        error TEXT,
        UNIQUE (site, issue_id)
    )""",
    """CREATE TABLE IF NOT EXISTS jira_run_stories (
        run_id TEXT NOT NULL REFERENCES jira_task_runs(id) ON DELETE CASCADE,
        job_id INTEGER NOT NULL REFERENCES jira_story_jobs(id),
        jira_done INTEGER NOT NULL,
        mapping_conflict INTEGER NOT NULL DEFAULT 0,
        seen_generation INTEGER NOT NULL,
        PRIMARY KEY (run_id, job_id)
    )""",
    """CREATE TABLE IF NOT EXISTS jira_job_attempts (
        job_id INTEGER NOT NULL REFERENCES jira_story_jobs(id),
        number INTEGER NOT NULL,
        run_id TEXT NOT NULL REFERENCES jira_task_runs(id),
        stage TEXT NOT NULL,
        started_at TEXT DEFAULT CURRENT_TIMESTAMP,
        dispatch_ended_at TEXT,
        error TEXT,
        PRIMARY KEY (job_id, number)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_jira_story_jobs_state
       ON jira_story_jobs(state, claimed_run)""",
)
