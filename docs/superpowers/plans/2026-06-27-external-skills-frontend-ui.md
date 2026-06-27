# External Skills Frontend UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Bot-config "External Skills" panel (spec §7.2): two-layer pool list with badges, per-bot assignment toggle, git-import modal, manage/remove, and a high-privilege approval-policy dropdown.

**Architecture:** A single self-contained React modal component `ExternalSkillPanel.jsx`, opened as an overlay from `SkillPanel`'s header (mirroring how `SkillTestPanel` overlays today). It talks to the already-shipped Plan-B backend endpoints through a new `externalSkillsApi.js` helper module. Capability (assignment → `bot_skills` via the groups endpoint) and security (approval policy → `permission_rules` via the members/permissions endpoint) stay two separate code paths, matching the backend's deliberate split.

**Tech Stack:** React 19 (hooks, no router), Tailwind CSS v4 (dark theme utility classes), `react-i18next` (`useTranslation` + `K` key registry), Vitest + `@testing-library/react` (jsdom), `vi.mock` for the API module.

## Global Constraints

- **Per-task commit for architect review:** each task ends with its own commit; another architect reviews each task's diff independently. Do not batch tasks into one commit.
- **Commit message rule (CLAUDE.md):** author is `nuke` only. NEVER add `Co-Authored-By` or any AI signature/trailer.
- **i18n is mandatory:** every user-facing string goes through `t(K....)`. Add keys to `frontend/src/i18n/keys.js` AND both `frontend/src/i18n/locales/zh.json` and `en.json`. Default UI language is `zh`; tests assert Chinese text.
- **Auth:** all network calls use `authFetch` (from `frontend/src/api.js`) so the JWT bearer token is attached. The groups/skills/permissions routers are all mounted with `Depends(auth.get_current_user)`.
- **Test command:** full suite `npm test` (= `vitest run`) run from `frontend/`. Single file: `npx vitest run src/<path>` from `frontend/`.
- **Backend is frozen for this plan.** These endpoints already exist and ship on this branch — do NOT modify backend code:
  - `GET /api/groups/{gid}/members/{botId}/skills` → `{ pool: ExternalRow[], assigned: Assignment[] }`
  - `PUT /api/groups/{gid}/members/{botId}/skills` body `{ assigned: {name,pool,enabled}[] }` → `{ assigned }` — **full reconcile**: bot_skills is made to match the array exactly; any skill not listed is removed.
  - `POST /api/skills/import` body `{ git_url, ref, scope }` where `scope` is `"global"` or `{ group_id }` → `{ imported: {id,name,version,platforms,high_privilege}[], rejected: {path,reason}[] }`.
  - `DELETE /api/skills/external/{id}` → removed row (404 if missing).
  - `GET /api/members/{botId}/permissions` → `{id,tool_pattern,args_pattern,action}[]`.
  - `POST /api/members/{botId}/permissions` body `{ tool_pattern, args_pattern, action }`, **action ∈ {"allow","deny"} only** (no "ask") → `{ id }`.
  - `DELETE /api/members/{botId}/permissions/{ruleId}`.
- **`ExternalRow` shape** (registry `_COLS`, NO `description` field): `{ id, name, scope_kind:"global"|"group", group_id, source_url, ref, commit_sha, version, platforms, high_privilege, imported_by, imported_at, status }`. The UI must render only fields that exist — there is no description to show.
- **`Assignment` shape:** `{ skill_name, pool:"external_global"|"external_group", enabled, assigned_by }`.
- **pool↔scope mapping:** an `ExternalRow` with `scope_kind:"global"` maps to assignment `pool:"external_global"`; `scope_kind:"group"` maps to `pool:"external_group"`.

---

## File Structure

- **Create** `frontend/src/externalSkillsApi.js` — all network helpers for this feature (assignment, import, remove, permission rules). One module, one responsibility: the HTTP surface for external skills.
- **Create** `frontend/src/components/ExternalSkillPanel.jsx` — the panel UI (pool list, assignment toggles, import modal, remove, approval dropdown). Built up across Tasks 2–5.
- **Modify** `frontend/src/i18n/keys.js` — add an `externalSkill` key block.
- **Modify** `frontend/src/i18n/locales/zh.json` and `en.json` — add the `externalSkill` strings.
- **Modify** `frontend/src/components/SkillPanel.jsx` — add a header button that opens `ExternalSkillPanel` as an overlay.
- **Create** test files alongside: `frontend/src/externalSkillsApi.test.js`, `frontend/src/components/ExternalSkillPanel.test.jsx`, and an entry-point assertion in a new `frontend/src/components/SkillPanel.external.test.jsx`.

---

## Task 1: API helper module + i18n keys

**Files:**
- Create: `frontend/src/externalSkillsApi.js`
- Modify: `frontend/src/i18n/keys.js` (add `externalSkill` block)
- Modify: `frontend/src/i18n/locales/zh.json`, `frontend/src/i18n/locales/en.json`
- Test: `frontend/src/externalSkillsApi.test.js`

**Interfaces:**
- Consumes: `authFetch` from `./api`.
- Produces (all async, all throw `Error(detail)` on non-2xx):
  - `fetchMemberExternalSkills(groupId, botId) -> { pool, assigned }`
  - `putMemberExternalSkills(groupId, botId, assigned) -> { assigned }`
  - `importExternalSkill({ git_url, ref, scope }) -> { imported, rejected }`
  - `removeExternalSkill(id) -> object`
  - `fetchPermissionRules(botId) -> Rule[]`
  - `addPermissionRule(botId, { tool_pattern, args_pattern, action }) -> { id }`
  - `removePermissionRule(botId, ruleId) -> object`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/externalSkillsApi.test.js`:

```js
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('./api', () => ({ authFetch: vi.fn() }))

function ok(body) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(body) })
}
function fail(detail, status = 400) {
  return Promise.resolve({ ok: false, status, statusText: 'err', json: () => Promise.resolve({ detail }) })
}

describe('externalSkillsApi', () => {
  let authFetch
  beforeEach(async () => { ({ authFetch } = await import('./api')) })
  afterEach(() => vi.restoreAllMocks())

  it('fetchMemberExternalSkills GETs the group/member skills route', async () => {
    const api = await import('./externalSkillsApi')
    authFetch.mockReturnValueOnce(ok({ pool: [], assigned: [] }))
    const r = await api.fetchMemberExternalSkills(7, 3)
    expect(authFetch).toHaveBeenCalledWith('/api/groups/7/members/3/skills')
    expect(r).toEqual({ pool: [], assigned: [] })
  })

  it('putMemberExternalSkills PUTs the assigned array', async () => {
    const api = await import('./externalSkillsApi')
    authFetch.mockReturnValueOnce(ok({ assigned: [] }))
    const assigned = [{ name: 'deploy', pool: 'external_global', enabled: true }]
    await api.putMemberExternalSkills(7, 3, assigned)
    const [url, opts] = authFetch.mock.calls[0]
    expect(url).toBe('/api/groups/7/members/3/skills')
    expect(opts.method).toBe('PUT')
    expect(JSON.parse(opts.body)).toEqual({ assigned })
  })

  it('importExternalSkill POSTs git_url/ref/scope', async () => {
    const api = await import('./externalSkillsApi')
    authFetch.mockReturnValueOnce(ok({ imported: [], rejected: [] }))
    await api.importExternalSkill({ git_url: 'https://github.com/x/y', ref: 'main', scope: 'global' })
    const [url, opts] = authFetch.mock.calls[0]
    expect(url).toBe('/api/skills/import')
    expect(JSON.parse(opts.body)).toEqual({ git_url: 'https://github.com/x/y', ref: 'main', scope: 'global' })
  })

  it('removeExternalSkill DELETEs by id', async () => {
    const api = await import('./externalSkillsApi')
    authFetch.mockReturnValueOnce(ok({ id: 5 }))
    await api.removeExternalSkill(5)
    expect(authFetch).toHaveBeenCalledWith('/api/skills/external/5', { method: 'DELETE' })
  })

  it('addPermissionRule POSTs a name-scoped rule', async () => {
    const api = await import('./externalSkillsApi')
    authFetch.mockReturnValueOnce(ok({ id: 9 }))
    await api.addPermissionRule(3, { tool_pattern: 'run_skill', args_pattern: 'deploy', action: 'deny' })
    const [url, opts] = authFetch.mock.calls[0]
    expect(url).toBe('/api/members/3/permissions')
    expect(JSON.parse(opts.body)).toEqual({ tool_pattern: 'run_skill', args_pattern: 'deploy', action: 'deny' })
  })

  it('throws the backend detail on non-ok', async () => {
    const api = await import('./externalSkillsApi')
    authFetch.mockReturnValueOnce(fail('scope must be global or {group_id}'))
    await expect(api.importExternalSkill({ git_url: 'x', ref: '', scope: 'bad' }))
      .rejects.toThrow('scope must be global or {group_id}')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/externalSkillsApi.test.js`
Expected: FAIL — cannot resolve `./externalSkillsApi`.

- [ ] **Step 3: Create the API module**

Create `frontend/src/externalSkillsApi.js`:

```js
import { authFetch } from './api'

async function jsonOrThrow(res) {
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail || detail } catch { /* non-json */ }
    throw new Error(detail)
  }
  return res.json()
}

// --- Capability: per-bot assignment (bot_skills via the groups route) ---

export async function fetchMemberExternalSkills(groupId, botId) {
  return jsonOrThrow(await authFetch(`/api/groups/${groupId}/members/${botId}/skills`))
}

// `assigned` is the FULL desired set: the backend reconciles bot_skills to match
// it exactly, so omitting a skill removes its assignment. Each entry is
// { name, pool, enabled }.
export async function putMemberExternalSkills(groupId, botId, assigned) {
  return jsonOrThrow(await authFetch(`/api/groups/${groupId}/members/${botId}/skills`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ assigned }),
  }))
}

// --- Pool lifecycle: import / remove (external_skills registry) ---

export async function importExternalSkill({ git_url, ref, scope }) {
  return jsonOrThrow(await authFetch('/api/skills/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ git_url, ref, scope }),
  }))
}

export async function removeExternalSkill(id) {
  return jsonOrThrow(await authFetch(`/api/skills/external/${id}`, { method: 'DELETE' }))
}

// --- Security: execution-approval policy (permission_rules) ---
// Separate path from assignment on purpose: capability != permission.

export async function fetchPermissionRules(botId) {
  return jsonOrThrow(await authFetch(`/api/members/${botId}/permissions`))
}

export async function addPermissionRule(botId, { tool_pattern, args_pattern, action }) {
  return jsonOrThrow(await authFetch(`/api/members/${botId}/permissions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tool_pattern, args_pattern, action }),
  }))
}

export async function removePermissionRule(botId, ruleId) {
  return jsonOrThrow(await authFetch(`/api/members/${botId}/permissions/${ruleId}`, { method: 'DELETE' }))
}
```

- [ ] **Step 4: Add i18n keys**

In `frontend/src/i18n/keys.js`, add a new top-level block right after the closing `}` of the `skill: { ... }` block (i.e. after the `copyToGroup` line's `},`):

```js
  externalSkill: {
    title: 'externalSkill.title',
    open: 'externalSkill.open',
    poolEmpty: 'externalSkill.poolEmpty',
    assign: 'externalSkill.assign',
    source: 'externalSkill.source',
    sourceGlobal: 'externalSkill.sourceGlobal',
    sourceGroup: 'externalSkill.sourceGroup',
    version: 'externalSkill.version',
    highPrivilege: 'externalSkill.highPrivilege',
    platformWarn: 'externalSkill.platformWarn',
    importButton: 'externalSkill.importButton',
    importTitle: 'externalSkill.importTitle',
    importScope: 'externalSkill.importScope',
    importUrl: 'externalSkill.importUrl',
    importRef: 'externalSkill.importRef',
    importRefPlaceholder: 'externalSkill.importRefPlaceholder',
    importSubmit: 'externalSkill.importSubmit',
    importing: 'externalSkill.importing',
    importedCount: 'externalSkill.importedCount',
    rejectedCount: 'externalSkill.rejectedCount',
    remove: 'externalSkill.remove',
    confirmRemove: 'externalSkill.confirmRemove',
    importedBy: 'externalSkill.importedBy',
    policy: 'externalSkill.policy',
    policyAllow: 'externalSkill.policyAllow',
    policyAsk: 'externalSkill.policyAsk',
    policyDeny: 'externalSkill.policyDeny',
  },
```

- [ ] **Step 5: Add locale strings**

In `frontend/src/i18n/locales/zh.json`, add a `"externalSkill"` object (place it right after the `"skill": { ... }` object):

```json
  "externalSkill": {
    "title": "外部技能",
    "open": "外部技能",
    "poolEmpty": "该范围暂无已导入的外部技能",
    "assign": "分配",
    "source": "来源",
    "sourceGlobal": "全局",
    "sourceGroup": "本群",
    "version": "版本",
    "highPrivilege": "高权限：{{tools}}",
    "platformWarn": "平台：{{platforms}}（与当前主机可能不兼容）",
    "importButton": "导入技能",
    "importTitle": "导入外部技能",
    "importScope": "导入范围",
    "importUrl": "Git 仓库地址",
    "importRef": "分支 / Tag",
    "importRefPlaceholder": "留空则用默认分支",
    "importSubmit": "导入",
    "importing": "导入中…",
    "importedCount": "已导入 {{count}} 个",
    "rejectedCount": "拒绝 {{count}} 个",
    "remove": "移除",
    "confirmRemove": "从池中移除外部技能「{{name}}」？文件与注册表行将一并删除。",
    "importedBy": "导入者",
    "policy": "审批策略",
    "policyAllow": "免审批",
    "policyAsk": "每次审批",
    "policyDeny": "禁止"
  },
```

In `frontend/src/i18n/locales/en.json`, add the parallel object (same position):

```json
  "externalSkill": {
    "title": "External Skills",
    "open": "External",
    "poolEmpty": "No external skills imported in this scope",
    "assign": "Assign",
    "source": "Source",
    "sourceGlobal": "Global",
    "sourceGroup": "This group",
    "version": "Version",
    "highPrivilege": "High-privilege: {{tools}}",
    "platformWarn": "Platform: {{platforms}} (may not match this host)",
    "importButton": "Import skill",
    "importTitle": "Import external skill",
    "importScope": "Scope",
    "importUrl": "Git repository URL",
    "importRef": "Branch / tag",
    "importRefPlaceholder": "blank = default branch",
    "importSubmit": "Import",
    "importing": "Importing…",
    "importedCount": "Imported {{count}}",
    "rejectedCount": "Rejected {{count}}",
    "remove": "Remove",
    "confirmRemove": "Remove external skill \"{{name}}\" from the pool? Its files and registry row are both deleted.",
    "importedBy": "Imported by",
    "policy": "Approval policy",
    "policyAllow": "Auto-allow",
    "policyAsk": "Ask each time",
    "policyDeny": "Deny"
  },
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `npx vitest run src/externalSkillsApi.test.js`
Expected: PASS (6 tests).

- [ ] **Step 7: Commit**

```bash
git -C /Users/Nuke/claudeFolder/nuke-ai-collaborator add frontend/src/externalSkillsApi.js frontend/src/externalSkillsApi.test.js frontend/src/i18n/keys.js frontend/src/i18n/locales/zh.json frontend/src/i18n/locales/en.json
git -C /Users/Nuke/claudeFolder/nuke-ai-collaborator commit -m "feat(skills-ui): external-skills API client + i18n keys"
```

---

## Task 2: ExternalSkillPanel — pool list + assignment toggle

**Files:**
- Create: `frontend/src/components/ExternalSkillPanel.jsx`
- Test: `frontend/src/components/ExternalSkillPanel.test.jsx`

**Interfaces:**
- Consumes: `fetchMemberExternalSkills`, `putMemberExternalSkills` (Task 1); `useTranslation`, `K`.
- Produces: default-export `ExternalSkillPanel({ bot, groupId, onClose })` — a fixed-overlay modal. On mount it loads `{ pool, assigned }`. Each pool row shows name + source badge + version + high-privilege warning + platform warning, and an assignment toggle. Toggling rebuilds the full desired array from current on-toggles and PUTs it.

**Design notes for the implementer:**
- The PUT is a **full reconcile**. Hold the set of assigned skill names in state (`assignedNames: Set`). On toggle, mutate that set, then build `assigned = [...assignedNames].map(name => ({ name, pool: poolForName(name), enabled: true }))` and PUT it. `poolForName` reads `scope_kind` from the pool row.
- v1 toggle semantics = **assigned-or-not** (presence in bot_skills, `enabled:true`). The "assigned-but-disabled" state from the spec is intentionally deferred; do not add a second control.
- `high_privilege` is a comma-joined string (possibly empty). Show the warning only when non-empty.
- `platforms` warning is advisory only — show it when `platforms` is `"posix"` or `"windows"` (a host-specific declaration); skip for `"pure"`/`"cross"`/empty. Never block.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/ExternalSkillPanel.test.jsx`:

```jsx
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import ExternalSkillPanel from './ExternalSkillPanel'

vi.mock('../externalSkillsApi', () => ({
  fetchMemberExternalSkills: vi.fn(),
  putMemberExternalSkills: vi.fn(() => Promise.resolve({ assigned: [] })),
  importExternalSkill: vi.fn(),
  removeExternalSkill: vi.fn(),
  fetchPermissionRules: vi.fn(() => Promise.resolve([])),
  addPermissionRule: vi.fn(),
  removePermissionRule: vi.fn(),
}))

const POOL = [
  { id: 1, name: 'deploy', scope_kind: 'global', group_id: 0, source_url: 'https://github.com/x/y',
    version: '1.2.0', platforms: 'pure', high_privilege: '', imported_by: null, imported_at: '2026-06-27', status: 'active' },
  { id: 2, name: 'nuke-prod', scope_kind: 'group', group_id: 7, source_url: 'https://github.com/x/z',
    version: '', platforms: 'posix', high_privilege: 'run_shell', imported_by: 42, imported_at: '2026-06-27', status: 'active' },
]

describe('ExternalSkillPanel — pool + assignment', () => {
  let api
  beforeEach(async () => {
    api = await import('../externalSkillsApi')
    api.fetchMemberExternalSkills.mockResolvedValue({
      pool: POOL,
      assigned: [{ skill_name: 'deploy', pool: 'external_global', enabled: true, assigned_by: null }],
    })
  })
  afterEach(() => vi.restoreAllMocks())

  it('renders pool rows with badges and high-privilege warning', async () => {
    render(<ExternalSkillPanel bot={{ id: 3, name: 'dev' }} groupId={7} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('deploy')).toBeInTheDocument())
    expect(screen.getByText('nuke-prod')).toBeInTheDocument()
    // high-privilege warning shows the tool name
    expect(screen.getByText(/run_shell/)).toBeInTheDocument()
  })

  it('toggling an unassigned skill PUTs the full desired set', async () => {
    render(<ExternalSkillPanel bot={{ id: 3, name: 'dev' }} groupId={7} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('nuke-prod')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('assign-toggle-nuke-prod'))
    await waitFor(() => expect(api.putMemberExternalSkills).toHaveBeenCalled())
    const [gid, botId, assigned] = api.putMemberExternalSkills.mock.calls[0]
    expect(gid).toBe(7)
    expect(botId).toBe(3)
    const names = assigned.map(a => a.name).sort()
    expect(names).toEqual(['deploy', 'nuke-prod'])           // deploy kept, nuke-prod added
    const np = assigned.find(a => a.name === 'nuke-prod')
    expect(np.pool).toBe('external_group')                    // scope_kind:group -> external_group
    expect(np.enabled).toBe(true)
  })

  it('toggling off an assigned skill removes it from the desired set', async () => {
    render(<ExternalSkillPanel bot={{ id: 3, name: 'dev' }} groupId={7} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('deploy')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('assign-toggle-deploy'))
    await waitFor(() => expect(api.putMemberExternalSkills).toHaveBeenCalled())
    const assigned = api.putMemberExternalSkills.mock.calls[0][2]
    expect(assigned.map(a => a.name)).toEqual([])             // deploy removed, nothing else assigned
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/ExternalSkillPanel.test.jsx`
Expected: FAIL — cannot resolve `./ExternalSkillPanel`.

- [ ] **Step 3: Create the component (pool + toggle only)**

Create `frontend/src/components/ExternalSkillPanel.jsx`:

```jsx
import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { K } from '../i18n/keys'
import { fetchMemberExternalSkills, putMemberExternalSkills } from '../externalSkillsApi'

const POOL_FOR_SCOPE = { global: 'external_global', group: 'external_group' }

export default function ExternalSkillPanel({ bot, groupId, onClose }) {
  const { t } = useTranslation()
  const [pool, setPool] = useState([])
  const [assignedNames, setAssignedNames] = useState(new Set())
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(null)   // skill name currently being PUT

  const load = useCallback(async () => {
    setLoading(true)
    const data = await fetchMemberExternalSkills(groupId, bot.id)
    setPool(data.pool || [])
    setAssignedNames(new Set((data.assigned || []).filter(a => a.enabled).map(a => a.skill_name)))
    setLoading(false)
  }, [groupId, bot.id])

  useEffect(() => { load() }, [load])

  const poolFor = useCallback(
    (name) => {
      const row = pool.find(p => p.name === name)
      return POOL_FOR_SCOPE[row?.scope_kind] || 'external_global'
    },
    [pool],
  )

  const toggleAssign = async (skill) => {
    setBusy(skill.name)
    const next = new Set(assignedNames)
    if (next.has(skill.name)) next.delete(skill.name)
    else next.add(skill.name)
    const desired = [...next].map(name => ({ name, pool: poolFor(name), enabled: true }))
    try {
      await putMemberExternalSkills(groupId, bot.id, desired)
      setAssignedNames(next)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-gray-800 rounded-2xl shadow-xl flex flex-col overflow-hidden relative"
        style={{ width: '720px', maxWidth: '95vw', height: '580px', maxHeight: '90vh' }}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700 flex-shrink-0">
          <div>
            <div className="text-base font-semibold text-white">{t(K.externalSkill.title)}</div>
            <div className="text-xs text-gray-500 mt-0.5">{bot.name}</div>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white text-xl leading-none">×</button>
        </div>

        {/* Pool list */}
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center h-full text-gray-500 text-sm">{t(K.common.loading)}</div>
          ) : pool.length === 0 ? (
            <div className="flex items-center justify-center h-full text-gray-600 text-sm">{t(K.externalSkill.poolEmpty)}</div>
          ) : (
            <div className="divide-y divide-gray-700/50">
              {pool.map(skill => (
                <ExternalSkillRow
                  key={skill.id}
                  skill={skill}
                  assigned={assignedNames.has(skill.name)}
                  busy={busy === skill.name}
                  onToggle={() => toggleAssign(skill)}
                  t={t}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function ExternalSkillRow({ skill, assigned, busy, onToggle, t }) {
  const isGlobal = skill.scope_kind === 'global'
  const hostSpecific = skill.platforms === 'posix' || skill.platforms === 'windows'
  return (
    <div className="px-5 py-3 flex items-start gap-3 hover:bg-gray-750 transition-colors">
      <span className={`mt-0.5 text-[10px] font-medium px-2 py-0.5 rounded-full flex-shrink-0 ${
        isGlobal ? 'bg-purple-900/50 text-purple-300' : 'bg-blue-900/50 text-blue-300'
      }`}>
        {isGlobal ? t(K.externalSkill.sourceGlobal) : t(K.externalSkill.sourceGroup)}
      </span>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-white">{skill.name}</span>
          {skill.version && (
            <span className="text-[10px] bg-gray-700 text-gray-300 px-1.5 py-0.5 rounded">v{skill.version}</span>
          )}
        </div>
        <div className="text-xs text-gray-600 mt-0.5 truncate">{skill.source_url}</div>
        {skill.high_privilege && (
          <div className="mt-1.5 text-[10px] px-2 py-0.5 rounded border bg-red-950/40 border-red-900/40 text-red-300 leading-tight inline-block">
            ⚠️ {t(K.externalSkill.highPrivilege, { tools: skill.high_privilege })}
          </div>
        )}
        {hostSpecific && (
          <div className="mt-1 text-[10px] px-2 py-0.5 rounded border bg-yellow-950/40 border-yellow-900/40 text-yellow-300 leading-tight inline-block">
            {t(K.externalSkill.platformWarn, { platforms: skill.platforms })}
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 flex-shrink-0">
        <button
          data-testid={`assign-toggle-${skill.name}`}
          onClick={onToggle}
          disabled={busy}
          className={`relative w-10 h-5 rounded-full transition-colors flex-shrink-0 ${busy ? 'opacity-40' : 'cursor-pointer'} ${assigned ? 'bg-indigo-600' : 'bg-gray-700'}`}
        >
          <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-all ${assigned ? 'left-5' : 'left-0.5'}`} />
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx vitest run src/components/ExternalSkillPanel.test.jsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git -C /Users/Nuke/claudeFolder/nuke-ai-collaborator add frontend/src/components/ExternalSkillPanel.jsx frontend/src/components/ExternalSkillPanel.test.jsx
git -C /Users/Nuke/claudeFolder/nuke-ai-collaborator commit -m "feat(skills-ui): external-skill pool list + per-bot assignment toggle"
```

---

## Task 3: Import modal

**Files:**
- Modify: `frontend/src/components/ExternalSkillPanel.jsx`
- Test: append to `frontend/src/components/ExternalSkillPanel.test.jsx`

**Interfaces:**
- Consumes: `importExternalSkill` (Task 1).
- Produces: an "Import skill" button in the panel header that opens an inline modal (scope select global/group, git URL input, ref input). Submit calls `importExternalSkill({ git_url, ref, scope })` where `scope` is `"global"` or `{ group_id: groupId }`, then reloads the pool and shows an imported/rejected summary line.

- [ ] **Step 1: Write the failing test**

Append this `describe` block to `frontend/src/components/ExternalSkillPanel.test.jsx` (the `vi.mock`, `POOL`, and imports from Task 2 are reused):

```jsx
describe('ExternalSkillPanel — import', () => {
  let api
  beforeEach(async () => {
    api = await import('../externalSkillsApi')
    api.fetchMemberExternalSkills.mockResolvedValue({ pool: [], assigned: [] })
    api.importExternalSkill.mockResolvedValue({ imported: [{ id: 9, name: 'new-skill' }], rejected: [] })
  })
  afterEach(() => vi.restoreAllMocks())

  it('submits the import form with group scope and reloads the pool', async () => {
    render(<ExternalSkillPanel bot={{ id: 3, name: 'dev' }} groupId={7} onClose={() => {}} />)
    await waitFor(() => expect(api.fetchMemberExternalSkills).toHaveBeenCalledTimes(1))

    fireEvent.click(screen.getByTestId('open-import'))
    fireEvent.change(screen.getByTestId('import-url'), { target: { value: 'https://github.com/x/y' } })
    fireEvent.change(screen.getByTestId('import-ref'), { target: { value: 'v1' } })
    // scope select defaults to 'group'
    fireEvent.click(screen.getByTestId('submit-import'))

    await waitFor(() => expect(api.importExternalSkill).toHaveBeenCalled())
    expect(api.importExternalSkill).toHaveBeenCalledWith({
      git_url: 'https://github.com/x/y', ref: 'v1', scope: { group_id: 7 },
    })
    // pool reloaded after import
    await waitFor(() => expect(api.fetchMemberExternalSkills).toHaveBeenCalledTimes(2))
  })

  it('sends scope:"global" when global is selected', async () => {
    render(<ExternalSkillPanel bot={{ id: 3, name: 'dev' }} groupId={7} onClose={() => {}} />)
    await waitFor(() => expect(api.fetchMemberExternalSkills).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByTestId('open-import'))
    fireEvent.change(screen.getByTestId('import-scope'), { target: { value: 'global' } })
    fireEvent.change(screen.getByTestId('import-url'), { target: { value: 'https://github.com/a/b' } })
    fireEvent.click(screen.getByTestId('submit-import'))
    await waitFor(() => expect(api.importExternalSkill).toHaveBeenCalled())
    expect(api.importExternalSkill.mock.calls[0][0].scope).toBe('global')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/ExternalSkillPanel.test.jsx`
Expected: FAIL — `open-import` testid not found.

- [ ] **Step 3: Add the import button + modal**

In `frontend/src/components/ExternalSkillPanel.jsx`, update the import line to add `importExternalSkill`:

```jsx
import { fetchMemberExternalSkills, putMemberExternalSkills, importExternalSkill } from '../externalSkillsApi'
```

Add import state inside the component (after the `busy` state):

```jsx
  const [importing, setImporting] = useState(false)
  const [showImport, setShowImport] = useState(false)
  const [importScope, setImportScope] = useState('group')   // 'group' | 'global'
  const [gitUrl, setGitUrl] = useState('')
  const [gitRef, setGitRef] = useState('')
  const [importResult, setImportResult] = useState(null)

  const submitImport = async () => {
    if (!gitUrl.trim() || importing) return
    setImporting(true)
    setImportResult(null)
    try {
      const scope = importScope === 'global' ? 'global' : { group_id: groupId }
      const res = await importExternalSkill({ git_url: gitUrl.trim(), ref: gitRef.trim(), scope })
      setImportResult(res)
      setShowImport(false)
      setGitUrl('')
      setGitRef('')
      await load()
    } catch (e) {
      setImportResult({ imported: [], rejected: [{ path: gitUrl, reason: e.message }] })
    } finally {
      setImporting(false)
    }
  }
```

Replace the header `<button onClick={onClose} ...>×</button>` with this cluster (import button + close):

```jsx
          <div className="flex items-center gap-3">
            <button
              data-testid="open-import"
              onClick={() => setShowImport(true)}
              className="text-xs px-3 py-1 rounded-lg bg-indigo-700 hover:bg-indigo-600 text-white transition-colors"
            >
              {t(K.externalSkill.importButton)}
            </button>
            <button onClick={onClose} className="text-gray-500 hover:text-white text-xl leading-none">×</button>
          </div>
```

Add the import summary banner just below the header `</div>` (before the Pool list block):

```jsx
        {importResult && (
          <div className="px-5 py-2 border-b border-gray-700 text-xs text-gray-400 flex-shrink-0">
            {t(K.externalSkill.importedCount, { count: importResult.imported.length })}
            {importResult.rejected.length > 0 && (
              <span className="text-red-400 ml-3">{t(K.externalSkill.rejectedCount, { count: importResult.rejected.length })}</span>
            )}
          </div>
        )}
```

Add the import modal as the last child inside the inner panel `<div>` (right before its closing `</div>` that pairs with `onClick={e => e.stopPropagation()}`):

```jsx
        {showImport && (
          <div className="absolute inset-0 bg-gray-800/95 z-10 flex flex-col rounded-2xl overflow-hidden">
            <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700">
              <div className="text-base font-semibold text-white">{t(K.externalSkill.importTitle)}</div>
              <button onClick={() => setShowImport(false)} className="text-gray-500 hover:text-white text-xl leading-none">×</button>
            </div>
            <div className="flex-1 flex flex-col gap-3 p-5">
              <label className="text-xs text-gray-400">{t(K.externalSkill.importScope)}</label>
              <select
                data-testid="import-scope"
                value={importScope}
                onChange={e => setImportScope(e.target.value)}
                className="bg-gray-900 text-gray-200 text-sm rounded-lg px-3 py-2 outline-none"
              >
                <option value="group">{t(K.externalSkill.sourceGroup)}</option>
                <option value="global">{t(K.externalSkill.sourceGlobal)}</option>
              </select>

              <label className="text-xs text-gray-400">{t(K.externalSkill.importUrl)}</label>
              <input
                data-testid="import-url"
                value={gitUrl}
                onChange={e => setGitUrl(e.target.value)}
                placeholder="https://github.com/org/repo"
                className="bg-gray-900 text-gray-100 text-sm rounded-lg px-3 py-2 outline-none focus:ring-1 focus:ring-indigo-500 font-mono"
              />

              <label className="text-xs text-gray-400">{t(K.externalSkill.importRef)}</label>
              <input
                data-testid="import-ref"
                value={gitRef}
                onChange={e => setGitRef(e.target.value)}
                placeholder={t(K.externalSkill.importRefPlaceholder)}
                className="bg-gray-900 text-gray-100 text-sm rounded-lg px-3 py-2 outline-none focus:ring-1 focus:ring-indigo-500 font-mono"
              />

              <button
                data-testid="submit-import"
                onClick={submitImport}
                disabled={!gitUrl.trim() || importing}
                className="self-start text-sm px-4 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white transition-colors"
              >
                {importing ? t(K.externalSkill.importing) : t(K.externalSkill.importSubmit)}
              </button>
            </div>
          </div>
        )}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx vitest run src/components/ExternalSkillPanel.test.jsx`
Expected: PASS (Task 2's 3 + Task 3's 2 = 5 tests).

- [ ] **Step 5: Commit**

```bash
git -C /Users/Nuke/claudeFolder/nuke-ai-collaborator add frontend/src/components/ExternalSkillPanel.jsx frontend/src/components/ExternalSkillPanel.test.jsx
git -C /Users/Nuke/claudeFolder/nuke-ai-collaborator commit -m "feat(skills-ui): git-import modal for external skills"
```

---

## Task 4: Manage / remove from pool

**Files:**
- Modify: `frontend/src/components/ExternalSkillPanel.jsx`
- Test: append to `frontend/src/components/ExternalSkillPanel.test.jsx`

**Interfaces:**
- Consumes: `removeExternalSkill` (Task 1).
- Produces: a "Remove" button per pool row that, after a `confirm`, calls `removeExternalSkill(skill.id)` and reloads the pool. Shows `imported_by` when present.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/components/ExternalSkillPanel.test.jsx`:

```jsx
describe('ExternalSkillPanel — remove', () => {
  let api
  beforeEach(async () => {
    api = await import('../externalSkillsApi')
    api.fetchMemberExternalSkills.mockResolvedValue({ pool: POOL, assigned: [] })
    api.removeExternalSkill.mockResolvedValue({ id: 1 })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
  })
  afterEach(() => vi.restoreAllMocks())

  it('removes a pool skill after confirm and reloads', async () => {
    render(<ExternalSkillPanel bot={{ id: 3, name: 'dev' }} groupId={7} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('deploy')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('remove-skill-1'))
    await waitFor(() => expect(api.removeExternalSkill).toHaveBeenCalledWith(1))
    await waitFor(() => expect(api.fetchMemberExternalSkills).toHaveBeenCalledTimes(2))
  })

  it('does nothing when confirm is cancelled', async () => {
    window.confirm.mockReturnValue(false)
    render(<ExternalSkillPanel bot={{ id: 3, name: 'dev' }} groupId={7} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('deploy')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('remove-skill-1'))
    expect(api.removeExternalSkill).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/ExternalSkillPanel.test.jsx`
Expected: FAIL — `remove-skill-1` testid not found.

- [ ] **Step 3: Add the remove action**

In `frontend/src/components/ExternalSkillPanel.jsx`, update the import line to add `removeExternalSkill`:

```jsx
import { fetchMemberExternalSkills, putMemberExternalSkills, importExternalSkill, removeExternalSkill } from '../externalSkillsApi'
```

Add a remove handler inside the component (after `submitImport`):

```jsx
  const removeFromPool = async (skill) => {
    if (!confirm(t(K.externalSkill.confirmRemove, { name: skill.name }))) return
    await removeExternalSkill(skill.id)
    await load()
  }
```

Pass it to the row in the pool `.map(...)`:

```jsx
                <ExternalSkillRow
                  key={skill.id}
                  skill={skill}
                  assigned={assignedNames.has(skill.name)}
                  busy={busy === skill.name}
                  onToggle={() => toggleAssign(skill)}
                  onRemove={() => removeFromPool(skill)}
                  t={t}
                />
```

Update `ExternalSkillRow`'s signature and actions block. Change the signature to:

```jsx
function ExternalSkillRow({ skill, assigned, busy, onToggle, onRemove, t }) {
```

Add an `imported_by` line inside the info `<div>` after the `source_url` line:

```jsx
        {skill.imported_by != null && (
          <div className="text-[10px] text-gray-600 mt-0.5">{t(K.externalSkill.importedBy)} #{skill.imported_by}</div>
        )}
```

Add a Remove button in the actions block, before the assign toggle button:

```jsx
        <button
          data-testid={`remove-skill-${skill.id}`}
          onClick={onRemove}
          className="text-xs px-2.5 py-1 rounded-lg bg-red-900/60 hover:bg-red-800 text-red-300 transition-colors"
        >
          {t(K.externalSkill.remove)}
        </button>
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx vitest run src/components/ExternalSkillPanel.test.jsx`
Expected: PASS (3 + 2 + 2 = 7 tests).

- [ ] **Step 5: Commit**

```bash
git -C /Users/Nuke/claudeFolder/nuke-ai-collaborator add frontend/src/components/ExternalSkillPanel.jsx frontend/src/components/ExternalSkillPanel.test.jsx
git -C /Users/Nuke/claudeFolder/nuke-ai-collaborator commit -m "feat(skills-ui): remove external skill from pool"
```

---

## Task 5: High-privilege approval-policy dropdown

**Files:**
- Modify: `frontend/src/components/ExternalSkillPanel.jsx`
- Test: append to `frontend/src/components/ExternalSkillPanel.test.jsx`

**Interfaces:**
- Consumes: `fetchPermissionRules`, `addPermissionRule`, `removePermissionRule` (Task 1).
- Produces: for pool rows with non-empty `high_privilege`, an approval-policy `<select>` (Allow / Ask / Deny) that reads & writes `permission_rules` for the bot. This is the **security** path — kept separate from the assignment toggle (capability).

**Design notes for the implementer:**
- The backend permission action vocabulary is `{"allow","deny"}` only. The third UI state **Ask** = *no rule* (fall through to the default HIL gate). So:
  - **Allow** → ensure exactly one `{tool_pattern:'run_skill', args_pattern:<name>, action:'allow'}` rule exists; delete any matching `deny`.
  - **Deny** → ensure one matching `action:'deny'`; delete any matching `allow`.
  - **Ask** → delete every matching rule.
- A rule "matches" a skill when `tool_pattern` is `run_skill` or `run_skill*` AND `args_pattern === skill.name`. Skill names are backend-validated kebab-case (`_is_safe_name`), so `args_pattern` carries no glob metacharacters — sending the raw name is exactly what the engine's name-scoped matcher compares against; no escaping needed here.
- Load the bot's rules once on mount (parallel with the pool load). Derive each skill's current policy from those rules.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/components/ExternalSkillPanel.test.jsx`:

```jsx
describe('ExternalSkillPanel — approval policy', () => {
  let api
  beforeEach(async () => {
    api = await import('../externalSkillsApi')
    api.fetchMemberExternalSkills.mockResolvedValue({ pool: POOL, assigned: [] })
    // 'nuke-prod' is high-privilege; start with no rules
    api.fetchPermissionRules.mockResolvedValue([])
    api.addPermissionRule.mockResolvedValue({ id: 11 })
    api.removePermissionRule.mockResolvedValue({})
  })
  afterEach(() => vi.restoreAllMocks())

  it('shows a policy dropdown only for high-privilege skills', async () => {
    render(<ExternalSkillPanel bot={{ id: 3, name: 'dev' }} groupId={7} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('nuke-prod')).toBeInTheDocument())
    expect(screen.getByTestId('policy-nuke-prod')).toBeInTheDocument()
    expect(screen.queryByTestId('policy-deploy')).not.toBeInTheDocument()   // deploy has no high_privilege
  })

  it('selecting Deny posts a name-scoped deny rule', async () => {
    render(<ExternalSkillPanel bot={{ id: 3, name: 'dev' }} groupId={7} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('policy-nuke-prod')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('policy-nuke-prod'), { target: { value: 'deny' } })
    await waitFor(() => expect(api.addPermissionRule).toHaveBeenCalled())
    expect(api.addPermissionRule).toHaveBeenCalledWith(3, {
      tool_pattern: 'run_skill', args_pattern: 'nuke-prod', action: 'deny',
    })
  })

  it('selecting Ask deletes the existing matching rule', async () => {
    api.fetchPermissionRules.mockResolvedValue([
      { id: 88, tool_pattern: 'run_skill', args_pattern: 'nuke-prod', action: 'allow' },
    ])
    render(<ExternalSkillPanel bot={{ id: 3, name: 'dev' }} groupId={7} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('policy-nuke-prod')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('policy-nuke-prod'), { target: { value: 'ask' } })
    await waitFor(() => expect(api.removePermissionRule).toHaveBeenCalledWith(3, 88))
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/ExternalSkillPanel.test.jsx`
Expected: FAIL — `policy-nuke-prod` testid not found.

- [ ] **Step 3: Add the approval-policy path**

In `frontend/src/components/ExternalSkillPanel.jsx`, update the import line to pull the permission helpers:

```jsx
import {
  fetchMemberExternalSkills, putMemberExternalSkills, importExternalSkill, removeExternalSkill,
  fetchPermissionRules, addPermissionRule, removePermissionRule,
} from '../externalSkillsApi'
```

Add a module-level matcher helper above the component:

```jsx
const RUN_SKILL_PATTERNS = new Set(['run_skill', 'run_skill*'])
const matchesSkill = (rule, name) =>
  RUN_SKILL_PATTERNS.has(rule.tool_pattern) && rule.args_pattern === name
```

Add rules state + loader inside the component (after the `assignedNames` state):

```jsx
  const [rules, setRules] = useState([])

  const loadRules = useCallback(async () => {
    setRules(await fetchPermissionRules(bot.id))
  }, [bot.id])

  useEffect(() => { loadRules() }, [loadRules])
```

Add a policy-derivation + setter inside the component (after `removeFromPool`):

```jsx
  const policyOf = useCallback(
    (name) => {
      const r = rules.find(rule => matchesSkill(rule, name))
      return r ? r.action : 'ask'   // no rule = default HIL = 'ask'
    },
    [rules],
  )

  const setPolicy = async (skill, action) => {
    // Clear every existing matching rule first, then add one if not 'ask'.
    for (const r of rules.filter(rule => matchesSkill(rule, skill.name))) {
      await removePermissionRule(bot.id, r.id)
    }
    if (action !== 'ask') {
      await addPermissionRule(bot.id, {
        tool_pattern: 'run_skill', args_pattern: skill.name, action,
      })
    }
    await loadRules()
  }
```

Pass policy props to the row in the pool `.map(...)`:

```jsx
                <ExternalSkillRow
                  key={skill.id}
                  skill={skill}
                  assigned={assignedNames.has(skill.name)}
                  busy={busy === skill.name}
                  policy={policyOf(skill.name)}
                  onToggle={() => toggleAssign(skill)}
                  onRemove={() => removeFromPool(skill)}
                  onPolicy={(action) => setPolicy(skill, action)}
                  t={t}
                />
```

Update `ExternalSkillRow` signature and add the dropdown. Change signature to:

```jsx
function ExternalSkillRow({ skill, assigned, busy, policy, onToggle, onRemove, onPolicy, t }) {
```

Add the policy `<select>` in the actions block, between the Remove button and the assign toggle, rendered only for high-privilege skills:

```jsx
        {skill.high_privilege && (
          <select
            data-testid={`policy-${skill.name}`}
            value={policy}
            onChange={e => onPolicy(e.target.value)}
            className="bg-gray-900 text-gray-300 text-xs rounded-lg px-2 py-1 outline-none"
          >
            <option value="allow">{t(K.externalSkill.policyAllow)}</option>
            <option value="ask">{t(K.externalSkill.policyAsk)}</option>
            <option value="deny">{t(K.externalSkill.policyDeny)}</option>
          </select>
        )}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx vitest run src/components/ExternalSkillPanel.test.jsx`
Expected: PASS (7 + 3 = 10 tests).

- [ ] **Step 5: Commit**

```bash
git -C /Users/Nuke/claudeFolder/nuke-ai-collaborator add frontend/src/components/ExternalSkillPanel.jsx frontend/src/components/ExternalSkillPanel.test.jsx
git -C /Users/Nuke/claudeFolder/nuke-ai-collaborator commit -m "feat(skills-ui): high-privilege approval-policy dropdown"
```

---

## Task 6: Wire entry point in SkillPanel + full regression

**Files:**
- Modify: `frontend/src/components/SkillPanel.jsx`
- Test: `frontend/src/components/SkillPanel.external.test.jsx`

**Interfaces:**
- Consumes: `ExternalSkillPanel` (Tasks 2–5).
- Produces: an "External" button in the `SkillPanel` header that opens `ExternalSkillPanel` as a full-overlay sibling (same `bot`/`groupId`/`onClose` contract). Closing the overlay returns to `SkillPanel`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/SkillPanel.external.test.jsx`:

```jsx
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import SkillPanel from './SkillPanel'

vi.mock('../skillsApi', () => ({
  fetchScopeSkills: vi.fn(() => Promise.resolve({ skills: [] })),
  copyScopeSkill: vi.fn(),
}))
vi.mock('../externalSkillsApi', () => ({
  fetchMemberExternalSkills: vi.fn(() => Promise.resolve({ pool: [], assigned: [] })),
  putMemberExternalSkills: vi.fn(),
  importExternalSkill: vi.fn(),
  removeExternalSkill: vi.fn(),
  fetchPermissionRules: vi.fn(() => Promise.resolve([])),
  addPermissionRule: vi.fn(),
  removePermissionRule: vi.fn(),
}))

describe('SkillPanel external-skills entry point', () => {
  beforeEach(() => {
    global.localStorage = { getItem: () => 'zh', setItem: () => {}, removeItem: () => {} }
    global.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ skills: [] }) }))
  })
  afterEach(() => vi.restoreAllMocks())

  it('opens the external-skills panel from the header button', async () => {
    render(<SkillPanel bot={{ id: 3, name: 'dev', role: 'developer' }} groupId={7} onClose={() => {}} />)
    fireEvent.click(screen.getByTestId('open-external-skills'))
    const { fetchMemberExternalSkills } = await import('../externalSkillsApi')
    await waitFor(() => expect(fetchMemberExternalSkills).toHaveBeenCalledWith(7, 3))
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/SkillPanel.external.test.jsx`
Expected: FAIL — `open-external-skills` testid not found.

- [ ] **Step 3: Wire the entry point**

In `frontend/src/components/SkillPanel.jsx`, add the import at the top (after the existing imports):

```jsx
import ExternalSkillPanel from './ExternalSkillPanel'
```

Add state inside the component (next to the other `useState` hooks, e.g. after `const [scopeSkills, setScopeSkills] = useState([])`):

```jsx
  const [showExternal, setShowExternal] = useState(false)
```

Add an early overlay return — place it just before the component's main `return (` (mirrors the `SkillTestPanel` overlay idea but as a sibling panel). It must come **after** all hooks:

```jsx
  if (showExternal) {
    return <ExternalSkillPanel bot={bot} groupId={groupId} onClose={() => setShowExternal(false)} />
  }
```

Add the button in the header actions cluster — insert it right before the existing `browse-scopes-toggle` button:

```jsx
            <button
              data-testid="open-external-skills"
              onClick={() => setShowExternal(true)}
              className="text-xs px-3 py-1 rounded-lg bg-gray-700 text-gray-300 hover:text-white transition-colors"
            >
              {t(K.externalSkill.open)}
            </button>
```

- [ ] **Step 4: Run the new test to verify it passes**

Run: `npx vitest run src/components/SkillPanel.external.test.jsx`
Expected: PASS.

- [ ] **Step 5: Run the FULL frontend suite (regression gate)**

Run: `npm test`
Expected: all suites pass, including the pre-existing `SkillPanel.scope.test.jsx`, `MemberList.roles.test.jsx`, `api.test.js`, `skillsApi.test.js`, and the new external-skills tests. If any pre-existing test fails, stop and investigate before committing.

- [ ] **Step 6: Commit**

```bash
git -C /Users/Nuke/claudeFolder/nuke-ai-collaborator add frontend/src/components/SkillPanel.jsx frontend/src/components/SkillPanel.external.test.jsx
git -C /Users/Nuke/claudeFolder/nuke-ai-collaborator commit -m "feat(skills-ui): open external-skills panel from SkillPanel header"
```

---

## Out of scope (deferred)

- **Skill description in the pool list.** The `GET …/skills` pool comes from the `external_skills` registry (`_COLS`), which has no `description` column. Showing a description would need a backend change to join the scanner's parsed metadata — a separate backend follow-up, not this UI plan. The UI shows name/version/source/source_url/high_privilege/platforms only.
- **Assigned-but-disabled state.** The spec distinguishes "assigned but temporarily disabled" from "not assigned". v1 uses a single assignment toggle (present+enabled vs absent). A second disable control is a future enhancement; the backend `bot_skills.enabled` column already supports it.
- **`external_skills` update/pin/audit UI.** v1 backend only models these columns; no endpoints exist, so no UI.

---

## Self-Review

**Spec coverage (§7.2):**
- ✅ Two-layer pool list with version + platform badge + high-privilege warning + source — Task 2.
- ✅ Per-skill assignment toggle (writes `bot_skills`) — Task 2.
- ✅ High-privilege approval-policy dropdown (writes `permission_rules`) — Task 5.
- ✅ "Import skill" button → scope select → git URL → drives §4 → refresh — Task 3.
- ✅ Manage list (source/version/importer) + remove — Task 4.
- ⚠️ Description in pool list — not available from the backend endpoint; documented as deferred (no fabricated field).

**Placeholder scan:** every code step shows complete code; every test step has runnable assertions; every run step has an exact command + expected result. No TBD/TODO.

**Type/name consistency:** the seven `externalSkillsApi.js` exports (Task 1) are consumed with matching names/arities in Tasks 2–5. `ExternalSkillPanel({ bot, groupId, onClose })` (Task 2) is mounted identically by `SkillPanel` (Task 6) and every test. `ExternalSkillRow` props grow monotonically across Tasks 2/4/5 — each task updates the signature and the call site together. The `pool`↔`scope_kind`↔`external_global/external_group` mapping is applied consistently (Task 2 `POOL_FOR_SCOPE`, asserted in Task 2's PUT test). Approval rule shape `{tool_pattern:'run_skill', args_pattern:<name>, action}` matches the backend `POST /permissions` body and the engine's name-scoped matcher.

**i18n:** every new string is keyed in `keys.js` and present in both `zh.json` and `en.json` (Task 1); tests assert against the default `zh` render.
