# Ruflo Advantage Adoption Strategy

> Date: 2026-07-05
> Status: Draft for review
> Scope: Product-level strategy for selectively importing Ruflo's strengths into Nuke AI Collaborator.

## 1. Executive Decision

Nuke AI Collaborator should not become a Ruflo clone.

Ruflo is an agent harness, CLI, MCP server, and plugin ecosystem for Claude Code / Codex. Nuke AI Collaborator is a Group-first AI collaboration product: humans and role-based Bots work together inside isolated project groups.

The correct strategy is:

> Keep Nuke's Group-first product model, and absorb Ruflo's strengths as platform-layer capabilities: capability governance, signed verification, memory distillation, plugin packaging, and evaluation.

In product terms, Nuke should evolve from:

> AI group chat with role-based Bots

into:

> A team AI collaboration operating system where each Group owns isolated Bots, memory, skills, tools, permissions, workspaces, and verifiable capabilities.

## 2. What Not To Import

Several Ruflo capabilities are strategically unsuitable for direct adoption.

| Ruflo capability | Import? | Reason |
|---|---:|---|
| Full 300+ MCP tool surface | No | It would expand Nuke's attack surface and dilute the product model. |
| Ruflo CLI as primary interface | No | Nuke's primary interface should remain the Web Group workspace. |
| Trading / IoT / domain-specific experimental plugins | Not now | They are not aligned with Nuke's current collaboration use case. |
| Full swarm / hive-mind vocabulary | Partial | Nuke already has Group, Bot, workflow, and spawn-agent concepts. Avoid redundant user-facing concepts. |
| Tool-count-driven marketing | No | Nuke should sell governed collaboration, not raw tool quantity. |

## 3. Ruflo Strengths To Adopt

### 3.1 Capability Registry

Ruflo's capability inventory is valuable. Nuke should introduce a first-class `Capability Registry` that enumerates every tool, skill, MCP server, workflow, plugin, and Bot capability visible to a Group.

Target capability classes:

```text
Capability Registry
├── tools
│   ├── builtin: read_file / write_file / run_shell
│   ├── mcp: github / browser / filesystem / custom servers
│   └── workflow: signal_stage_done / signal_rework
├── skills
│   ├── system
│   ├── group
│   ├── role
│   ├── personal
│   └── learned
├── bots
├── workflows
├── memory indexes
└── plugins
```

Each capability should carry at least:

```json
{
  "id": "tool.run_shell",
  "type": "tool",
  "scope": "group",
  "risk": "high",
  "requires_approval": true,
  "owner": "system",
  "source": "backend/executors/plugins/workspace_tools.py",
  "version": "2026-07-05",
  "tests": ["test_p1_safety.py", "test_permission_patterns.py"]
}
```

Product value:

- Admins can see what a Group can do.
- High-risk tools are visible before use.
- Skills, MCP servers, and future plugins stop being scattered files.
- Verification, permission review, and plugin installation get a shared substrate.

Priority: P0.

### 3.2 Signed Verification / Witness

Ruflo's signed witness model is a major trust advantage. Nuke should build a lighter version focused on private deployment and operational trust.

Target interfaces:

```text
python -m backend.verify
GET /api/system/verify
```

The manifest should include:

- backend critical file hashes
- frontend build hash
- built-in skill hashes
- plugin manifests
- default permission policy hash
- migration version
- sandbox image reference
- MCP config schema hash
- capability inventory hash

Example output:

```json
{
  "status": "verified",
  "version": "0.8.0",
  "commit": "abc123",
  "capabilities": 87,
  "critical_files": 142,
  "drift": 0,
  "missing": 0
}
```

Product value:

- Private deployments can prove what is installed.
- Support can distinguish configuration drift from code drift.
- Built-in skills and permission defaults become auditable.
- Enterprise buyers get a concrete trust mechanism.

Priority: P1.

### 3.3 Memory Distillation

Nuke already has a strong memory foundation: facts, reflections, Chroma, salience, time decay, conflict resolution, and Group isolation. Ruflo's advantage is the next layer: turning long-running traces into structured, auditable patterns.

Target pipeline:

```text
messages / tool traces / workflow outcomes
  -> facts
  -> episodes
  -> patterns
  -> draft skills
  -> human approval
  -> active skills / promoted memories
```

Add logical artifacts:

```text
episodes
- one coherent unit of work, conversation, or workflow stage

patterns
- repeated reusable behavior extracted from episodes

pattern_evidence
- links to messages, tool calls, files, and workflow outcomes

promotion_state
- draft / approved / rejected / retired
```

Rules:

- No learned pattern may auto-promote into an active skill.
- No pattern may cross Group boundaries.
- Person memory and project memory must remain separate.
- Every draft skill must include evidence.
- Proxy patterns may help retrieval, but must not justify autonomous high-risk action.

Product surface:

```text
Learning Inbox
├── detected team habit
├── detected project rule
├── reusable workflow candidate
├── evidence links
├── proposed memory / skill diff
└── approve / edit / reject
```

Product value:

- Chat history becomes reusable organizational knowledge.
- Bots compound project experience without becoming black boxes.
- Human approval remains the promotion gate.
- Nuke gets a durable differentiation from generic chat products.

Priority: P0/P1.

### 3.4 Plugin Packaging And Tiers

Ruflo's plugin ecosystem is useful, but Nuke should adopt a stricter, smaller, product-aligned version.

Recommended tiers:

```text
Core
- System-owned and not uninstallable: chat, group, permission, workspace, memory.

Official
- Maintained by Nuke: GitHub, Jira, browser testing, docs, testgen, security audit, cost tracker.

Verified
- Team or enterprise internal plugins that pass manifest, tests, and signing.

Experimental
- Lab features disabled by default for production Groups.
```

Plugin manifest:

```json
{
  "id": "nuke.github",
  "name": "GitHub Integration",
  "version": "0.1.0",
  "capabilities": ["tool.github_pr", "tool.github_issue"],
  "permissions": ["network", "mcp", "write:workspace"],
  "risk": "medium",
  "scopes": ["group", "role"],
  "entry": "plugins/github",
  "tests": ["test_github_tools.py"]
}
```

First official plugin candidates:

| Plugin | Product value |
|---|---|
| GitHub / Jira | Connect AI collaboration to real engineering work. |
| Browser testing | Let QA Bots verify web behavior. |
| Docs | Maintain project-facing documentation. |
| Testgen | Generate and repair test coverage. |
| Security audit | Scan code and dependencies before release. |
| Cost tracker | Show model and workflow spend by Group. |

Priority: P1/P2.

### 3.5 Evaluation Harness

Ruflo's benchmark culture should be adapted into product-level evals for Nuke.

Evaluation categories:

```text
evals/
├── group_collaboration/
├── memory_recall/
├── tool_safety/
├── workflow_completion/
├── permission_gate/
├── skill_learning/
└── mcp_integration/
```

Core metrics:

| Metric | Meaning |
|---|---|
| Task completion rate | Whether a workflow reaches a useful done state. |
| Human intervention count | How often users must correct the Bots. |
| Tool failure recovery | Whether the agent recovers after a failed tool call. |
| Memory recall precision | Whether retrieved memory is relevant and scoped correctly. |
| Cross-group leakage | Whether information crosses Group boundaries. |
| Permission bypass rate | Whether a tool avoids approval unexpectedly. |
| Skill promotion quality | Whether learned skills are useful and evidence-backed. |
| Cost per workflow | Token and provider cost per completed workflow. |

Priority: P1.

## 4. Product Roadmap

### Phase 0 — Strategic Alignment

Duration: 2 weeks.

Deliverables:

- this strategy reviewed and accepted
- Capability Registry design
- plugin manifest design
- memory distillation design
- evaluation standard design

Decisions:

- Nuke remains Web-first and Group-first.
- Ruflo's full tool surface is not imported.
- Every Group capability must be enumerable and auditable.
- Learned behavior requires evidence and human approval before activation.

### Phase 1 — Capability Registry

Duration: 3-4 weeks.

Suggested backend module:

```text
backend/capabilities/
├── registry.py
├── models.py
├── scanner.py
├── routes.py
└── manifest.py
```

Suggested APIs:

```text
GET /api/capabilities
GET /api/groups/{group_id}/capabilities
GET /api/members/{member_id}/capabilities
```

Initial UI:

```text
Capabilities
├── Tools
├── Skills
├── MCP Servers
├── Workflows
├── Plugins
└── Risk Level
```

Success criteria:

- builtin tools are enumerated
- skills are enumerated by layer
- MCP schemas are enumerated after collector sync
- every capability has a risk level
- high-risk capabilities link to permission policy

### Phase 2 — Learning Inbox And Memory Distillation

Duration: 4-6 weeks.

Suggested backend module:

```text
backend/learning/
├── episodes.py
├── distill.py
├── patterns.py
├── evidence.py
├── promotion.py
└── routes.py
```

Success criteria:

- Bots cannot auto-activate learned skills.
- Every draft has evidence.
- Approved drafts move into `learned/active`.
- Rejected low-quality candidates do not repeatedly reappear.
- Group isolation is enforced on every learned artifact.

### Phase 3 — Verification Manifest

Duration: 3-4 weeks.

Targets:

- local manifest generation
- CI manifest verification
- UI status: verified / drifted / missing
- drift report points to concrete files or capabilities

Success criteria:

- private deployments can run verification after install
- built-in skills and permission defaults are covered
- capability inventory hash is included

### Phase 4 — Official Plugin System

Duration: 6-8 weeks.

Suggested initial plugins:

```text
plugins/
├── github/
├── jira/
├── browser/
├── docs/
├── testgen/
└── security-audit/
```

Each plugin must include:

```text
plugin.json
README.md
capabilities.json
permissions.json
tests/
```

Success criteria:

- a Group can enable / disable an official plugin
- Bots only see capabilities enabled for their Group
- plugin tools use the existing permission engine
- disabling a plugin removes its tool schemas and capabilities

### Phase 5 — Evaluation Harness

Duration: ongoing.

First eval set:

1. Group isolation leakage
2. MCP permission bypass
3. memory recall precision
4. learned skill quality
5. BA -> Dev -> QA workflow completion
6. tool failure recovery

Outputs:

```text
docs/evals/latest.json
docs/evals/report.md
```

Success criteria:

- AI loop changes can be regression-tested
- memory strategies can be compared
- model choices can be compared
- learning quality is measured instead of guessed

## 5. Target Architecture

```text
Nuke AI Collaborator
│
├── Product Layer
│   ├── Web Chat
│   ├── Group Workspace
│   ├── Workflow UI
│   ├── Skill UI
│   └── Learning Inbox
│
├── Runtime Layer
│   ├── Supervisor
│   ├── Worker
│   ├── MCP Collector
│   ├── Tool Loop
│   └── Permission Engine
│
├── Capability Layer
│   ├── Capability Registry
│   ├── Plugin Manifests
│   ├── Risk Classification
│   ├── Signed Witness
│   └── Evaluation Harness
│
├── Intelligence Layer
│   ├── Memory Distillation
│   ├── Evidence Graph
│   ├── Pattern Store
│   ├── Draft Skill Generation
│   └── Promotion Gate
│
└── Extension Layer
    ├── Official Plugins
    ├── External Skills
    ├── MCP Servers
    └── Enterprise Internal Plugins
```

## 6. Product Messaging

Do not frame this as "Nuke uses Ruflo."

Frame it as:

> Nuke supports a governed AI capability system. Every Group can see what its AI members can do, what they have learned, which capabilities were approved, which tools require permission, and whether the deployment is verified.

Recommended product claims:

- AI members have tools, memory, skills, and approval boundaries.
- Each Group evolves independently without cross-project memory leakage.
- Experience turns into evidence-backed draft skills.
- Tools and plugins are enumerable, disableable, and auditable.
- Deployments are verifiable, and AI behavior can be regression-tested.

## 7. Priority Order

If resources are limited, execute in this order:

1. Capability Registry
2. Learning Inbox with evidence-backed draft skills
3. Memory Distillation
4. Verification Manifest
5. Official Plugin System
6. Evaluation Harness
7. Remote marketplace / plugin distribution

Do not start with a marketplace. Without capability governance and permission integration, a marketplace only amplifies risk.

## 8. Strategic Summary

Nuke should absorb Ruflo's platform credibility, not its complexity.

The strongest path is:

> Nuke remains a Group-first AI collaboration product, while Ruflo-like strengths become internal platform layers: capability governance, memory distillation, plugin packaging, signed verification, and evaluation.

This preserves Nuke's product clarity while adding the trust, extensibility, and compounding-learning advantages that make Ruflo strategically valuable.
