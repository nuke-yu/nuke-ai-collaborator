# Online Story Task Processing

## Scope

The online task service accepts and executes **one Jira Story per request**. It does not accept Epics, enumerate Epic children, decide which Stories are unfinished, or synchronize a batch.

The Dashboard owns all Jira access: it reads the Epic and Stories, selects the Stories to process, and submits each selected Story to Collaborator as an independent request. Collaborator receives only four Jira fields: issue ID/key, summary, description, and acceptance criteria. A summary service marker such as `[aaa-service]` is resolved locally to a same-named GitHub repository under the configured owner.

Collaborator owns centralized Story task execution: validate the Story and repository input, deduplicate requests, queue and limit concurrent work, create an isolated coding task, run tests, retain attempt and recovery state, and track PR delivery for human review.

## Boundaries

| Responsibility | Owner |
|---|---|
| Read an Epic and find its Stories | Dashboard |
| Decide which Stories are eligible | Dashboard |
| Submit the four Story fields | Dashboard → Collaborator API |
| Maintain the `[service-name]` summary convention | Jira Story author/operator |
| Read Jira and format Story fields | Dashboard |
| Resolve the service marker to a repository | Collaborator input adapter |
| Deduplicate, queue, and limit Story task execution | Collaborator `online_tasks` |
| Run code, validate tests, preserve attempts, and deliver PR | Collaborator online coding workflow |
| Review and merge the PR | Human |

One Story is one logical Collaborator task and maps to one repository. The service name must be the GitHub repository slug. `NUKE_GITHUB_OWNER` selects the GitHub owner and defaults to `nuke-yu`; the initial base branch is `main`. If the summary has no valid leading marker, or the repository cannot be accessed, the Story is blocked with a reason. A retry creates a new attempt for the same logical task. Different Stories may target the same repository, but get separate task Groups, worktrees, and branches.

## API

Submit one Story:

```http
POST /api/agent/story-tasks
Content-Type: application/json
```

```json
{
  "jira_id": "PAY-123",
  "summary": "[aaa-service] Upgrade the cache client",
  "description": "Upgrade the dependency and preserve existing behavior.",
  "acceptance_criteria": "Tests pass and cache behavior remains compatible."
}
```

Collaborator makes no Jira API calls and needs no Jira credentials. It validates the four input fields and summary marker, then resolves `[aaa-service]` to `https://github.com/nuke-yu/aaa-service.git`. The coding agent receives the four supplied fields as its requirements and discovers the repository's test commands when none are configured.

Input limits are 500 characters for summary, 12,000 for description, and 8,000 for acceptance criteria. Oversized fields receive HTTP 422 before queue insertion; accepted fields are redacted and retained without truncation. These limits also apply after redaction. Errors identify the field and limit without echoing its content.

The Dashboard submits each selected Story separately. Repeating a Story submission returns its existing task. A blocked task with no execution attempt can be corrected by resubmitting the four fields; the API accepts no caller-supplied repository URL.

The response provides an ID and a status URL. The Dashboard can poll `GET /api/agent/story-tasks/{id}` and retry eligible execution failures with `POST /api/agent/story-tasks/{id}/retry`. If a missing or malformed service marker blocks a task, the Dashboard can correct the fields and resubmit them. There are no Epic run, batch pause, batch sync, or Epic retry endpoints in Collaborator.

`/api/agent/story-runs` remains a hidden-schema compatibility alias for existing single-Story clients. It does not support Epic execution.

## Execution and recovery

The Supervisor persists each Story request before execution. The queue enforces a global concurrency limit configured by `NUKE_ONLINE_STORY_MAX_PARALLEL` (default 2). Each claimed Story has a durable attempt number and a stable task ID. Worker dispatch is reconciled against persisted task and workflow state; uncertain dispatch is marked interrupted rather than blindly creating duplicate work.

The Worker executes the existing `coding_agent_v1` workflow in the task's isolated workspace. Test evidence is recorded before PR delivery. The service tracks PR status separately from agent completion, preserving human review as the merge boundary.

Existing Epic records in upgraded databases are retained as historical data but are excluded from new discovery, dispatch, and public status reads. New databases constrain online Jira requests to `kind='story'`.

## Design references

- DSH informs single-run lifecycle, explicit completion outcomes, durable session recovery, and ownership of an isolated execution workspace. It does not define Epic selection or Collaborator's Group-based Bot model.
- Superagent informs queue and execution-slot mechanics. Its Epic-to-Story discovery belongs in the Dashboard layer for this product.

These references do not add runtime dependencies on DSH or Superagent.

## Task execution module names

The Supervisor queue is `online_tasks/task_queue.py` (`TaskQueueService`), backed by `task_queue_store.py` (`TaskQueueStore`) and exposed through `agent_dashboard/task_queue_api.py`. These components schedule coding tasks; they do not integrate with Jira. The four-field input retains `jira_id` as external task metadata. `NUKE_ONLINE_TASK_SCOPE` configures the de-duplication namespace; legacy site settings remain accepted. Existing database table names and delivery evidence names are retained for compatibility.

## Reliable recovery and attempt evidence

An interrupted dispatch with a task still in `created` can be retried through the queue. The orchestrator atomically claims the retry and requires the previous Worker's abort ACK before capturing an event boundary and dispatching again. An ACK failure restores the prior task state and leaves the queue entry interrupted and recoverable.

Each attempt has its own delivery record in `task_attempt_delivery`. Retry archives the previous delivery and clears the current projection. Evidence is read only after the event boundary captured following abort ACK; retries without a known boundary cannot reuse older evidence. PR lookup writes require both the current attempt number and linked queue state, preventing late responses from overwriting a new attempt. Status responses expose `execution_status` and `delivery_status` separately, with historical PR and validation records in `attempts`.

## Existing capability audit

Code inspection confirmed that `ai/client.py` already provides up to three attempts with backoff for transient request failures, and retries streaming requests only before output has been yielded. `tool_loop_v1.py` saves session snapshots before dispatching model-requested tools and detects consecutive identical tool-call batches. The task queue already persists claims and attempts, reconciles uncertain dispatches, and scopes PR evidence to an execution attempt. These mechanisms should be extended where necessary rather than duplicated at the queue layer.

Online queue dispatches now carry a stable identity `(task_id, dispatch_attempt)`. Before workflow startup, the Worker validates the current central task round and assigned Group/Bot, then commits a Group-local receipt and initial workflow snapshot in one transaction. Duplicate or stale delivery does not restart work; conflicting payloads for the same identity are rejected. A crash after this commit is handled by existing workflow snapshot recovery. Receipt hashes contain no raw requirements. Legacy dispatches without the queue-specific field retain their existing behavior. Recovery reconciliation checks the snapshot task ID and round so an older snapshot cannot confirm a newer dispatch.
