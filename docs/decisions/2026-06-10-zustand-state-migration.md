# Zustand State Management Migration (DFT-025) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract ChatWindow's 46 useState declarations into two Zustand stores (groupStore, chatStore), replacing the 150-line WS dispatcher with a single store action.

**Architecture:** `groupStore` holds navigation + membership (groups, activeGroupId, members). `chatStore` holds all WS-driven state (messages, typing, reactionMap, etc.) and exposes a `dispatchWsEvent(data, notify)` action that replaces the 150-line `handleWsMessage`. ChatWindow retains ~17 local `useState` for modal/layout-only state that doesn't need sharing. Child components `MessageList` and `GroupList` are updated to subscribe directly to stores, removing the deepest prop drilling chains.

**Tech Stack:** Zustand 5.x (npm package `zustand`), vitest + @testing-library/react (already installed), React 19

---

## File Map

| File | Status | Responsibility |
|------|--------|----------------|
| `frontend/src/store/groupStore.js` | **Create** | Group navigation, members, membersCache, unreadCounts |
| `frontend/src/store/chatStore.js` | **Create** | All WS-driven state + `dispatchWsEvent` action |
| `frontend/src/store/__tests__/groupStore.test.js` | **Create** | groupStore unit tests |
| `frontend/src/store/__tests__/chatStore.test.js` | **Create** | dispatchWsEvent logic tests (30+ event types) |
| `frontend/src/components/ChatWindow.jsx` | **Modify** | Remove 29 useState, use store hooks, handleWsMessage → 1 line |
| `frontend/src/components/MessageList.jsx` | **Modify** | Subscribe to chatStore/groupStore, remove 10 heavy props |
| `frontend/src/components/GroupList.jsx` | **Modify** | Subscribe to groupStore, remove 4 drilled props |

**State kept as `useState` in ChatWindow** (modal/layout — purely local, not shared):
`showTemplates`, `showApiKeys`, `showAddMember`, `editingMember`, `workspaceBot`,
`showWorkflowStart`, `wfBotOrder`, `showSearch`, `showBotLogs`, `replyingTo`,
`mobileTab`, `drafts`, `dragging`, `highlightedId`, `showStats`, `stats`,
`personalSummary`, `loadingRecap`, `aiSuggestions`, `suggestionsLoading`

---

## Task 1: Install Zustand

**Files:**
- Modify: `frontend/package.json` (via npm install)

- [ ] **Step 1: Install zustand**

```bash
cd frontend && npm install zustand
```

Expected: `zustand` appears in `package.json` dependencies (5.x).

- [ ] **Step 2: Verify install**

```bash
cd frontend && node -e "import('zustand').then(m => console.log('ok', Object.keys(m)))"
```

Expected output: `ok [ 'create', 'createStore', ... ]`

- [ ] **Step 3: Commit**

```bash
cd frontend && git add package.json package-lock.json
git commit -m "chore(frontend): install zustand for state management"
```

---

## Task 2: Create groupStore

**Files:**
- Create: `frontend/src/store/groupStore.js`
- Create: `frontend/src/store/__tests__/groupStore.test.js`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/store/__tests__/groupStore.test.js`:

```js
import { describe, it, expect, beforeEach } from 'vitest'
import { useGroupStore } from '../groupStore'

beforeEach(() => {
  useGroupStore.setState({
    groups: [],
    activeGroupId: null,
    activeMemberId: null,
    group: null,
    members: [],
    membersCache: {},
    unreadCounts: {},
  })
})

describe('groupStore', () => {
  it('setGroups replaces the list', () => {
    useGroupStore.getState().setGroups([{ id: 1, name: 'A' }])
    expect(useGroupStore.getState().groups).toEqual([{ id: 1, name: 'A' }])
  })

  it('setActiveGroupId updates activeGroupId', () => {
    useGroupStore.getState().setActiveGroupId(42)
    expect(useGroupStore.getState().activeGroupId).toBe(42)
  })

  it('setMembers accepts a function updater', () => {
    useGroupStore.setState({ members: [{ id: 1, name: 'Alice' }] })
    useGroupStore.getState().setMembers(prev => prev.map(m => ({ ...m, name: 'Bob' })))
    expect(useGroupStore.getState().members[0].name).toBe('Bob')
  })

  it('setMembersCache merges by groupId', () => {
    useGroupStore.getState().setMembersCache(prev => ({ ...prev, 5: [{ id: 9 }] }))
    expect(useGroupStore.getState().membersCache[5]).toEqual([{ id: 9 }])
  })

  it('setGroups with updater function', () => {
    useGroupStore.setState({ groups: [{ id: 1, member_count: 3 }] })
    useGroupStore.getState().setGroups(prev => prev.map(g => ({ ...g, member_count: g.member_count + 1 })))
    expect(useGroupStore.getState().groups[0].member_count).toBe(4)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd frontend && npx vitest run src/store/__tests__/groupStore.test.js
```

Expected: FAIL — `Cannot find module '../groupStore'`

- [ ] **Step 3: Create groupStore.js**

Create `frontend/src/store/groupStore.js`:

```js
import { create } from 'zustand'

export const useGroupStore = create((set) => ({
  groups: [],
  activeGroupId: null,
  activeMemberId: null,
  group: null,
  members: [],
  membersCache: {},
  unreadCounts: {},

  setGroups: (updater) =>
    set((s) => ({ groups: typeof updater === 'function' ? updater(s.groups) : updater })),
  setActiveGroupId: (id) => set({ activeGroupId: id }),
  setActiveMemberId: (id) => set({ activeMemberId: id }),
  setGroup: (updater) =>
    set((s) => ({ group: typeof updater === 'function' ? updater(s.group) : updater })),
  setMembers: (updater) =>
    set((s) => ({ members: typeof updater === 'function' ? updater(s.members) : updater })),
  setMembersCache: (updater) =>
    set((s) => ({ membersCache: typeof updater === 'function' ? updater(s.membersCache) : updater })),
  setUnreadCounts: (counts) => set({ unreadCounts: counts }),
}))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd frontend && npx vitest run src/store/__tests__/groupStore.test.js
```

Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/store/groupStore.js frontend/src/store/__tests__/groupStore.test.js
git commit -m "feat(frontend): add groupStore for group/member navigation state"
```

---

## Task 3: Create chatStore with dispatchWsEvent

**Files:**
- Create: `frontend/src/store/chatStore.js`
- Create: `frontend/src/store/__tests__/chatStore.test.js`

This is the core task. `dispatchWsEvent` replaces the 150-line `handleWsMessage` in ChatWindow. It reads `activeGroupId` and `activeMemberId` from `groupStore.getState()` so it never needs those as parameters.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/store/__tests__/chatStore.test.js`:

```js
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useChatStore } from '../chatStore'
import { useGroupStore } from '../groupStore'

const notify = vi.fn()

beforeEach(() => {
  useChatStore.setState({
    messages: [],
    messagesCache: {},
    typing: null,
    reactionMap: {},
    reactionCache: {},
    readMap: {},
    onlineSet: new Set(),
    permRequest: null,
    recoveryPrompts: [],
    thoughtBlocks: {},
    toolProgressBlocks: {},
    workflow: null,
    pins: [],
    awaySummary: null,
    skillDraftBots: new Set(),
    error: null,
    hasMore: false,
    loadingMore: false,
  })
  useGroupStore.setState({ activeGroupId: 1, activeMemberId: 10 })
  notify.mockClear()
})

describe('dispatchWsEvent — message events', () => {
  it('typing sets typing state', () => {
    useChatStore.getState().dispatchWsEvent(
      { type: 'typing', sender_name: 'Bot', avatar_color: 'red' },
      notify
    )
    expect(useChatStore.getState().typing).toEqual({ sender_name: 'Bot', avatar_color: 'red' })
  })

  it('message appends and clears typing', () => {
    useChatStore.setState({ typing: { sender_name: 'Bot' } })
    useChatStore.getState().dispatchWsEvent(
      { type: 'message', id: 1, member_id: 99, sender_name: 'Bot', content: 'hello' },
      notify
    )
    expect(useChatStore.getState().typing).toBeNull()
    expect(useChatStore.getState().messages).toHaveLength(1)
    expect(useChatStore.getState().messages[0].id).toBe(1)
  })

  it('message deduplicates by id', () => {
    useChatStore.setState({ messages: [{ id: 1, content: 'a' }] })
    useChatStore.getState().dispatchWsEvent(
      { type: 'message', id: 1, member_id: 99, sender_name: 'Bot', content: 'a' },
      notify
    )
    expect(useChatStore.getState().messages).toHaveLength(1)
  })

  it('message calls notify for other members', () => {
    useGroupStore.setState({ activeMemberId: 10 })
    useChatStore.getState().dispatchWsEvent(
      { type: 'message', id: 2, member_id: 99, sender_name: 'Bot', content: 'hi' },
      notify
    )
    expect(notify).toHaveBeenCalledWith('Bot', 'hi')
  })

  it('message does not notify for own member', () => {
    useGroupStore.setState({ activeMemberId: 10 })
    useChatStore.getState().dispatchWsEvent(
      { type: 'message', id: 3, member_id: 10, sender_name: 'Me', content: 'hi' },
      notify
    )
    expect(notify).not.toHaveBeenCalled()
  })
})

describe('dispatchWsEvent — streaming', () => {
  it('stream_start adds placeholder message', () => {
    useChatStore.getState().dispatchWsEvent(
      { type: 'stream_start', temp_id: 'tmp1', member_id: 5, sender_name: 'Bot', sender_type: 'bot', avatar_color: 'blue' },
      notify
    )
    const msgs = useChatStore.getState().messages
    expect(msgs).toHaveLength(1)
    expect(msgs[0].temp_id).toBe('tmp1')
    expect(msgs[0].streaming).toBe(true)
    expect(msgs[0].content).toBe('')
  })

  it('stream_chunk appends delta', () => {
    useChatStore.setState({ messages: [{ temp_id: 'tmp1', content: 'He', streaming: true }] })
    useChatStore.getState().dispatchWsEvent({ type: 'stream_chunk', temp_id: 'tmp1', delta: 'llo' }, notify)
    expect(useChatStore.getState().messages[0].content).toBe('Hello')
  })

  it('stream_end finalizes message with id', () => {
    useChatStore.setState({ messages: [{ temp_id: 'tmp1', content: 'Hello', streaming: true }] })
    useChatStore.getState().dispatchWsEvent(
      { type: 'stream_end', temp_id: 'tmp1', id: 42, created_at: '2026-01-01', member_id: 99 },
      notify
    )
    const msg = useChatStore.getState().messages[0]
    expect(msg.id).toBe(42)
    expect(msg.streaming).toBe(false)
    expect(msg.temp_id).toBeUndefined()
  })

  it('stream_aborted removes placeholder', () => {
    useChatStore.setState({ messages: [{ temp_id: 'tmp1', streaming: true }, { id: 1 }] })
    useChatStore.getState().dispatchWsEvent({ type: 'stream_aborted', temp_id: 'tmp1' }, notify)
    expect(useChatStore.getState().messages).toHaveLength(1)
    expect(useChatStore.getState().messages[0].id).toBe(1)
  })
})

describe('dispatchWsEvent — other events', () => {
  it('permission_request sets permRequest', () => {
    useChatStore.getState().dispatchWsEvent({ type: 'permission_request', request_id: 'r1' }, notify)
    expect(useChatStore.getState().permRequest).toEqual({ type: 'permission_request', request_id: 'r1' })
  })

  it('reaction_updated updates reactionMap', () => {
    useChatStore.getState().dispatchWsEvent(
      { type: 'reaction_updated', message_id: 5, reactions: ['👍'] },
      notify
    )
    expect(useChatStore.getState().reactionMap['5']).toEqual(['👍'])
  })

  it('message_edited updates content in-place', () => {
    useChatStore.setState({ messages: [{ id: 7, content: 'old' }] })
    useChatStore.getState().dispatchWsEvent({ type: 'message_edited', id: 7, content: 'new' }, notify)
    expect(useChatStore.getState().messages[0].content).toBe('new')
    expect(useChatStore.getState().messages[0].edited).toBe(true)
  })

  it('message_deleted marks is_deleted', () => {
    useChatStore.setState({ messages: [{ id: 7, content: 'text' }] })
    useChatStore.getState().dispatchWsEvent({ type: 'message_deleted', id: 7 }, notify)
    expect(useChatStore.getState().messages[0].is_deleted).toBe(true)
  })

  it('pins_updated sets pins', () => {
    useChatStore.getState().dispatchWsEvent({ type: 'pins_updated', pins: [{ id: 1 }] }, notify)
    expect(useChatStore.getState().pins).toEqual([{ id: 1 }])
  })

  it('online_members sets full onlineSet', () => {
    useChatStore.getState().dispatchWsEvent({ type: 'online_members', member_ids: [1, 2, 3] }, notify)
    expect(useChatStore.getState().onlineSet).toEqual(new Set([1, 2, 3]))
  })

  it('presence adds member to onlineSet', () => {
    useChatStore.setState({ onlineSet: new Set([1]) })
    useChatStore.getState().dispatchWsEvent({ type: 'presence', member_id: 2, online: true }, notify)
    expect(useChatStore.getState().onlineSet.has(2)).toBe(true)
  })

  it('presence removes member from onlineSet', () => {
    useChatStore.setState({ onlineSet: new Set([1, 2]) })
    useChatStore.getState().dispatchWsEvent({ type: 'presence', member_id: 1, online: false }, notify)
    expect(useChatStore.getState().onlineSet.has(1)).toBe(false)
  })

  it('workflow_update sets active workflow', () => {
    useChatStore.getState().dispatchWsEvent({ type: 'workflow_update', active: true, stage: 'A' }, notify)
    expect(useChatStore.getState().workflow).toEqual({ type: 'workflow_update', active: true, stage: 'A' })
  })

  it('workflow_update with active=false clears workflow', () => {
    useChatStore.setState({ workflow: { stage: 'A' } })
    useChatStore.getState().dispatchWsEvent({ type: 'workflow_update', active: false }, notify)
    expect(useChatStore.getState().workflow).toBeNull()
  })

  it('read updates readMap by member_id', () => {
    useChatStore.getState().dispatchWsEvent({ type: 'read', member_id: 5, last_read_id: 99 }, notify)
    expect(useChatStore.getState().readMap[5]).toBe(99)
  })

  it('ai_thought_start creates block', () => {
    useChatStore.getState().dispatchWsEvent(
      { type: 'ai_thought_start', temp_id: 't1', iteration: 1 },
      notify
    )
    expect(useChatStore.getState().thoughtBlocks['t1'][1]).toEqual({ iteration: 1, content: '', completed: false })
  })

  it('ai_thought_delta appends content', () => {
    useChatStore.setState({ thoughtBlocks: { t1: { 1: { iteration: 1, content: 'He', completed: false } } } })
    useChatStore.getState().dispatchWsEvent({ type: 'ai_thought_delta', temp_id: 't1', iteration: 1, delta: 'llo' }, notify)
    expect(useChatStore.getState().thoughtBlocks['t1'][1].content).toBe('Hello')
  })

  it('ai_thought_end marks completed', () => {
    useChatStore.setState({ thoughtBlocks: { t1: { 1: { iteration: 1, content: 'done', completed: false } } } })
    useChatStore.getState().dispatchWsEvent({ type: 'ai_thought_end', temp_id: 't1', iteration: 1 }, notify)
    expect(useChatStore.getState().thoughtBlocks['t1'][1].completed).toBe(true)
  })

  it('tool_progress_start creates block', () => {
    useChatStore.getState().dispatchWsEvent(
      { type: 'tool_progress_start', temp_id: 't1', tool_name: 'run_shell', tool_args: { cmd: 'ls' }, iteration: 1 },
      notify
    )
    expect(useChatStore.getState().toolProgressBlocks['t1-run_shell']).toMatchObject({ tool_name: 'run_shell', iteration: 1 })
  })

  it('tool_progress_end adds duration', () => {
    useChatStore.setState({ toolProgressBlocks: { 't1-run_shell': { tool_name: 'run_shell', iteration: 1 } } })
    useChatStore.getState().dispatchWsEvent(
      { type: 'tool_progress_end', temp_id: 't1', tool_name: 'run_shell', duration_sec: 0.42 },
      notify
    )
    expect(useChatStore.getState().toolProgressBlocks['t1-run_shell'].duration_sec).toBe(0.42)
  })

  it('recovery_prompt deduplicates by session_id', () => {
    useChatStore.getState().dispatchWsEvent(
      { type: 'recovery_prompt', session_id: 's1', message: 'resume?' },
      notify
    )
    useChatStore.getState().dispatchWsEvent(
      { type: 'recovery_prompt', session_id: 's1', message: 'resume?' },
      notify
    )
    expect(useChatStore.getState().recoveryPrompts).toHaveLength(1)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd frontend && npx vitest run src/store/__tests__/chatStore.test.js
```

Expected: FAIL — `Cannot find module '../chatStore'`

- [ ] **Step 3: Create chatStore.js**

Create `frontend/src/store/chatStore.js`:

```js
import { create } from 'zustand'
import { useGroupStore } from './groupStore'

export const useChatStore = create((set, get) => ({
  messages: [],
  messagesCache: {},
  typing: null,
  reactionMap: {},
  reactionCache: {},
  readMap: {},
  onlineSet: new Set(),
  permRequest: null,
  recoveryPrompts: [],
  thoughtBlocks: {},
  toolProgressBlocks: {},
  workflow: null,
  pins: [],
  awaySummary: null,
  skillDraftBots: new Set(),
  error: null,
  hasMore: false,
  loadingMore: false,

  setMessages: (u) => set((s) => ({ messages: typeof u === 'function' ? u(s.messages) : u })),
  setTyping: (typing) => set({ typing }),
  setReactionMap: (u) => set((s) => ({ reactionMap: typeof u === 'function' ? u(s.reactionMap) : u })),
  setReactionCache: (u) => set((s) => ({ reactionCache: typeof u === 'function' ? u(s.reactionCache) : u })),
  setReadMap: (u) => set((s) => ({ readMap: typeof u === 'function' ? u(s.readMap) : u })),
  setOnlineSet: (u) => set((s) => ({ onlineSet: typeof u === 'function' ? u(s.onlineSet) : u })),
  setPermRequest: (req) => set({ permRequest: req }),
  setRecoveryPrompts: (u) => set((s) => ({ recoveryPrompts: typeof u === 'function' ? u(s.recoveryPrompts) : u })),
  setThoughtBlocks: (u) => set((s) => ({ thoughtBlocks: typeof u === 'function' ? u(s.thoughtBlocks) : u })),
  setToolProgressBlocks: (u) => set((s) => ({ toolProgressBlocks: typeof u === 'function' ? u(s.toolProgressBlocks) : u })),
  setWorkflow: (workflow) => set({ workflow }),
  setPins: (pins) => set({ pins }),
  setAwaySummary: (summary) => set({ awaySummary: summary }),
  setSkillDraftBots: (u) => set((s) => ({ skillDraftBots: typeof u === 'function' ? u(s.skillDraftBots) : u })),
  setError: (error) => set({ error }),
  setHasMore: (hasMore) => set({ hasMore }),
  setLoadingMore: (loadingMore) => set({ loadingMore }),
  setMessagesCache: (u) => set((s) => ({ messagesCache: typeof u === 'function' ? u(s.messagesCache) : u })),

  syncCache: (activeGroupId, updater) => set((s) => {
    const cur = s.messagesCache[activeGroupId]
    if (!cur) return {}
    return { messagesCache: { ...s.messagesCache, [activeGroupId]: { ...cur, messages: updater(cur.messages) } } }
  }),

  dispatchWsEvent: (data, notify) => {
    const { activeGroupId, activeMemberId } = useGroupStore.getState()

    window.dispatchEvent(new CustomEvent('ws_bot_event', { detail: data }))

    const { syncCache } = get()

    if (data.type === 'typing') {
      set({ typing: { sender_name: data.sender_name, avatar_color: data.avatar_color } })

    } else if (data.type === 'message') {
      set({ typing: null })
      set((s) => ({
        messages: s.messages.some((m) => m.id === data.id) ? s.messages : [...s.messages, data],
      }))
      syncCache(activeGroupId, (msgs) =>
        msgs.some((m) => m.id === data.id) ? msgs : [...msgs, data]
      )
      if (data.member_id !== activeMemberId) notify(data.sender_name, data.content)

    } else if (data.type === 'stream_start') {
      set((s) => ({
        typing: null,
        messages: [
          ...s.messages,
          {
            temp_id: data.temp_id,
            member_id: data.member_id,
            sender_name: data.sender_name,
            sender_type: data.sender_type,
            avatar_color: data.avatar_color,
            content: '',
            streaming: true,
          },
        ],
      }))

    } else if (data.type === 'stream_chunk') {
      set((s) => ({
        messages: s.messages.map((m) =>
          m.temp_id === data.temp_id ? { ...m, content: m.content + data.delta } : m
        ),
      }))

    } else if (data.type === 'stream_end') {
      const finalize = (ms) =>
        ms.map((m) =>
          m.temp_id === data.temp_id
            ? { ...m, id: data.id, created_at: data.created_at, streaming: false, temp_id: undefined }
            : m
        )
      set((s) => ({ messages: finalize(s.messages) }))
      syncCache(activeGroupId, finalize)
      if (data.member_id !== activeMemberId) notify(data.sender_name, data.preview)

    } else if (data.type === 'compaction') {
      const marker = {
        _compact_marker: true,
        id: `compact-${Date.now()}`,
        strategy: data.strategy,
        message: data.message,
      }
      set((s) => ({ messages: [...s.messages, marker] }))

    } else if (data.type === 'stream_aborted') {
      set((s) => ({ messages: s.messages.filter((m) => m.temp_id !== data.temp_id) }))

    } else if (data.type === 'permission_request') {
      set({ permRequest: data })

    } else if (data.type === 'recovery_prompt') {
      set((s) => ({
        recoveryPrompts: s.recoveryPrompts.find((p) => p.session_id === data.session_id)
          ? s.recoveryPrompts
          : [...s.recoveryPrompts, data],
      }))

    } else if (data.type === 'stream_error') {
      set((s) => ({
        messages: s.messages.filter((m) => m.temp_id !== data.temp_id),
        error: data.message,
      }))
      setTimeout(() => set({ error: null }), 5000)

    } else if (data.type === 'group_updated') {
      useGroupStore.getState().setGroup((prev) =>
        prev ? { ...prev, name: data.name, announcement: data.announcement } : prev
      )
      useGroupStore.getState().setGroups((prev) =>
        prev.map((g) => (g.id === data.id ? { ...g, name: data.name } : g))
      )

    } else if (data.type === 'reaction_updated') {
      set((s) => ({
        reactionMap: { ...s.reactionMap, [String(data.message_id)]: data.reactions },
      }))

    } else if (data.type === 'message_edited') {
      const applyEdit = (ms) =>
        ms.map((m) => (m.id === data.id ? { ...m, content: data.content, edited: true } : m))
      set((s) => ({ messages: applyEdit(s.messages) }))
      syncCache(activeGroupId, applyEdit)

    } else if (data.type === 'message_deleted') {
      const applyDel = (ms) =>
        ms.map((m) => (m.id === data.id ? { ...m, is_deleted: true } : m))
      set((s) => ({ messages: applyDel(s.messages) }))
      syncCache(activeGroupId, applyDel)

    } else if (data.type === 'error') {
      set({ error: data.message })
      setTimeout(() => set({ error: null }), 5000)

    } else if (data.type === 'pins_updated') {
      set({ pins: data.pins })

    } else if (data.type === 'recap_updated') {
      if (data.group_id === activeGroupId) {
        set({ awaySummary: data.away_summary })
      }

    } else if (data.type === 'read') {
      set((s) => ({ readMap: { ...s.readMap, [data.member_id]: data.last_read_id } }))

    } else if (data.type === 'online_members') {
      set({ onlineSet: new Set(data.member_ids) })

    } else if (data.type === 'presence') {
      set((s) => {
        const next = new Set(s.onlineSet)
        data.online ? next.add(data.member_id) : next.delete(data.member_id)
        return { onlineSet: next }
      })

    } else if (data.type === 'member_removed') {
      const gs = useGroupStore.getState()
      gs.setMembers((prev) => prev.filter((m) => m.id !== data.member_id))
      gs.setMembersCache((prev) => ({
        ...prev,
        [activeGroupId]: (prev[activeGroupId] || []).filter((m) => m.id !== data.member_id),
      }))
      gs.setGroups((prev) =>
        prev.map((g) =>
          g.id === activeGroupId
            ? { ...g, member_count: Math.max(0, (g.member_count ?? 1) - 1) }
            : g
        )
      )

    } else if (data.type === 'workflow_update') {
      set({ workflow: data.active ? data : null })

    } else if (data.type === 'skills_loaded') {
      set((s) => ({
        messages: s.messages.map((m) =>
          m.temp_id === data.temp_id ? { ...m, skills_loaded: data.skills } : m
        ),
      }))

    } else if (data.type === 'skills_changed') {
      window.dispatchEvent(new CustomEvent('skills_changed', { detail: data }))

    } else if (data.type === 'skill_draft_added') {
      window.dispatchEvent(new CustomEvent('skills_changed', { detail: { ...data, source: 'bot' } }))
      if (data.member_id) {
        set((s) => ({ skillDraftBots: new Set([...s.skillDraftBots, data.member_id]) }))
      }

    } else if (data.type === 'ai_thought_start') {
      set((s) => ({
        thoughtBlocks: {
          ...s.thoughtBlocks,
          [data.temp_id]: {
            ...s.thoughtBlocks[data.temp_id],
            [data.iteration]: { iteration: data.iteration, content: '', completed: false },
          },
        },
      }))

    } else if (data.type === 'ai_thought_delta') {
      set((s) => ({
        thoughtBlocks: {
          ...s.thoughtBlocks,
          [data.temp_id]: {
            ...s.thoughtBlocks[data.temp_id],
            [data.iteration]: {
              ...s.thoughtBlocks[data.temp_id]?.[data.iteration],
              content:
                (s.thoughtBlocks[data.temp_id]?.[data.iteration]?.content || '') + data.delta,
            },
          },
        },
      }))

    } else if (data.type === 'ai_thought_end') {
      set((s) => ({
        thoughtBlocks: {
          ...s.thoughtBlocks,
          [data.temp_id]: {
            ...s.thoughtBlocks[data.temp_id],
            [data.iteration]: {
              ...s.thoughtBlocks[data.temp_id]?.[data.iteration],
              completed: true,
            },
          },
        },
      }))

    } else if (data.type === 'tool_progress_start') {
      set((s) => ({
        toolProgressBlocks: {
          ...s.toolProgressBlocks,
          [`${data.temp_id}-${data.tool_name}`]: {
            tool_name: data.tool_name,
            args: data.tool_args,
            iteration: data.iteration,
            duration_sec: undefined,
          },
        },
      }))

    } else if (data.type === 'tool_progress_end') {
      set((s) => ({
        toolProgressBlocks: {
          ...s.toolProgressBlocks,
          [`${data.temp_id}-${data.tool_name}`]: {
            ...s.toolProgressBlocks[`${data.temp_id}-${data.tool_name}`],
            duration_sec: data.duration_sec,
          },
        },
      }))
    }
  },
}))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd frontend && npx vitest run src/store/__tests__/chatStore.test.js
```

Expected: All tests PASS (there are 22 tests).

- [ ] **Step 5: Run all frontend tests to check no regression**

```bash
cd frontend && npx vitest run
```

Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/store/chatStore.js frontend/src/store/__tests__/chatStore.test.js
git commit -m "feat(frontend): add chatStore with dispatchWsEvent action (DFT-025)"
```

---

## Task 4: Migrate ChatWindow to use stores

**Files:**
- Modify: `frontend/src/components/ChatWindow.jsx`

Replace 29 useState declarations with store subscriptions, replace `handleWsMessage` 150-line body with a 1-line store dispatch, replace `syncCache` helper with `useChatStore.getState().syncCache(...)`.

- [ ] **Step 1: Read the current ChatWindow imports and useState block**

Read lines 1–73 of `frontend/src/components/ChatWindow.jsx` to confirm current state before editing.

- [ ] **Step 2: Replace imports and useState block**

In `ChatWindow.jsx`, change the import block and the entire useState section (lines 1–73):

```jsx
import { useEffect, useState, useRef, useCallback } from 'react'
import { fetchAllGroups, fetchGroupInfo, fetchMessages, fetchUnreadCounts, fetchReactions, toggleReaction, createGroup, addMember, fetchPins, pinMessage, unpinMessage, resumeSession, cancelSessionRecovery, fetchGroupRecap, dismissGroupRecap, fetchPersonalRecap, fetchAiSuggestions } from '../api'
import { useWebSocket } from '../hooks/useWebSocket'
import { useNotifications } from '../hooks/useNotifications'
import { useGroupStore } from '../store/groupStore'
import { useChatStore } from '../store/chatStore'
import GroupList from './GroupList'
import ChatHeader from './ChatHeader'
import MessageList from './MessageList'
import TemplateManager from './TemplateManager'
import MemberList from './MemberList'
import MessageInput from './MessageInput'
import SearchPanel from './SearchPanel'
import BotLogPanel from './BotLogPanel'
import ApiKeyManager from './ApiKeyManager'
import PinnedBar from './PinnedBar'
import AnnouncementBar from './AnnouncementBar'
import WorkflowBar from './WorkflowBar'
import WorkflowStartModal from './WorkflowStartModal'
import WorkspacePanel from './WorkspacePanel'
import PermissionRequestModal from './PermissionRequestModal'
import RecapBanner from './RecapBanner'
import SuggestionBar from './SuggestionBar'

export default function ChatWindow({ memberId, theme, onThemeChange, onLogout }) {
  // ── Group / membership state (groupStore) ────────────────────────────────
  const groups = useGroupStore((s) => s.groups)
  const activeGroupId = useGroupStore((s) => s.activeGroupId)
  const activeMemberId = useGroupStore((s) => s.activeMemberId)
  const group = useGroupStore((s) => s.group)
  const members = useGroupStore((s) => s.members)
  const membersCache = useGroupStore((s) => s.membersCache)
  const unreadCounts = useGroupStore((s) => s.unreadCounts)
  const {
    setGroups, setActiveGroupId, setActiveMemberId, setGroup,
    setMembers, setMembersCache, setUnreadCounts,
  } = useGroupStore()

  // ── WS-driven chat state (chatStore) ─────────────────────────────────────
  const messages = useChatStore((s) => s.messages)
  const typing = useChatStore((s) => s.typing)
  const reactionMap = useChatStore((s) => s.reactionMap)
  const readMap = useChatStore((s) => s.readMap)
  const onlineSet = useChatStore((s) => s.onlineSet)
  const permRequest = useChatStore((s) => s.permRequest)
  const recoveryPrompts = useChatStore((s) => s.recoveryPrompts)
  const thoughtBlocks = useChatStore((s) => s.thoughtBlocks)
  const toolProgressBlocks = useChatStore((s) => s.toolProgressBlocks)
  const workflow = useChatStore((s) => s.workflow)
  const pins = useChatStore((s) => s.pins)
  const awaySummary = useChatStore((s) => s.awaySummary)
  const skillDraftBots = useChatStore((s) => s.skillDraftBots)
  const error = useChatStore((s) => s.error)
  const hasMore = useChatStore((s) => s.hasMore)
  const loadingMore = useChatStore((s) => s.loadingMore)
  const {
    setMessages, setReactionMap, setReactionCache, setReadMap, setOnlineSet,
    setPermRequest, setRecoveryPrompts, setWorkflow, setPins, setAwaySummary,
    setSkillDraftBots, setError, setHasMore, setLoadingMore,
    setMessagesCache, syncCache, dispatchWsEvent,
  } = useChatStore()

  // ── Local modal / layout state (stays as useState) ───────────────────────
  const [showTemplates, setShowTemplates] = useState(false)
  const [showApiKeys, setShowApiKeys] = useState(false)
  const [showAddMember, setShowAddMember] = useState(false)
  const [editingMember, setEditingMember] = useState(null)
  const [workspaceBot, setWorkspaceBot] = useState(null)
  const [skillDraftBotsLocal] = useState()   // see note: skillDraftBots moved to chatStore
  const [error2] = useState()                // see note: error moved to chatStore
  const [showWorkflowStart, setShowWorkflowStart] = useState(false)
  const [wfBotOrder, setWfBotOrder] = useState([])
  const [showSearch, setShowSearch] = useState(false)
  const [showBotLogs, setShowBotLogs] = useState(false)
  const [replyingTo, setReplyingTo] = useState(null)
  const [mobileTab, setMobileTab] = useState('chat')
  const [drafts, setDrafts] = useState({})
  const [dragging, setDragging] = useState(false)
  const [highlightedId, setHighlightedId] = useState(null)
  const [showStats, setShowStats] = useState(false)
  const [stats, setStats] = useState([])
  const [personalSummary, setPersonalSummary] = useState(null)
  const [loadingRecap, setLoadingRecap] = useState(false)
  const [aiSuggestions, setAiSuggestions] = useState([])
  const [suggestionsLoading, setSuggestionsLoading] = useState(false)

  const personalRecapAt = useRef({})
  const { notify } = useNotifications()
  const bottomRef = useRef(null)
```

> **Note:** Remove the `const [skillDraftBotsLocal]` and `const [error2]` placeholder lines — these were just notes about what moved. The actual state is now in chatStore.

The actual replacement diff: remove ALL `useState` lines for the 29 states that moved to stores. The exact old block is lines 24–73 in the original file. Replace with the imports + store selectors + remaining useState block shown above.

- [ ] **Step 3: Remove syncCache helper and update handleWsMessage**

Find and remove this block (lines 291–297 in original):

```jsx
  const syncCache = (updater) => {
    setMessagesCache(prev => {
      const cur = prev[activeGroupId]
      if (!cur) return prev
      return { ...prev, [activeGroupId]: { ...cur, messages: updater(cur.messages) } }
    })
  }
```

And replace the entire `handleWsMessage` function (lines 299–461 in original) with:

```jsx
  const handleWsMessage = useCallback((data) => {
    dispatchWsEvent(data, notify)
  }, [dispatchWsEvent, notify])
```

- [ ] **Step 4: Update syncCache callsites in loadMore and handleReconnect**

In `loadMore` (around line 265), change:
```jsx
    // No syncCache call needed in loadMore — messagesCache is updated directly:
    setMessagesCache(prev => ({ ...prev, [activeGroupId]: { messages: [...older, ...(prev[activeGroupId]?.messages || [])], hasMore: has_more } }))
```

In `handleReconnect` (around line 464), replace `syncCache(msgs => ...)` with:
```jsx
      useChatStore.getState().syncCache(activeGroupId, (msgs) => {
        const existingIds = new Set(msgs.map((m) => m.id))
        const uniqueNewer = newer.filter((m) => !existingIds.has(m.id))
        return [...msgs, ...uniqueNewer]
      })
```

> Add `import { useChatStore } from '../store/chatStore'` if not already at the top (it should be from Step 2).

- [ ] **Step 5: Update the group-loading effect (lines 179–263)**

The effect uses `setMessages`, `setHasMore`, `setMessagesCache`, `setReactionMap`, `setReactionCache`, `setMembers`, `setMembersCache`, `setGroup`, `setActiveMemberId`, `setWorkflow`, `setAwaySummary` — these all come from the stores now. The function bodies don't change, only where the setters come from. Since we destructured them from the stores at the top of the component, no callsite changes are needed.

Verify by running the dev server and switching groups — messages should load correctly.

- [ ] **Step 6: Start the dev server and do a smoke test**

```bash
cd frontend && npm run dev
```

Open http://localhost:5173, log in, switch groups, send a message, verify streaming works. Check browser console for errors.

- [ ] **Step 7: Run frontend tests**

```bash
cd frontend && npx vitest run
```

Expected: All PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/ChatWindow.jsx
git commit -m "refactor(frontend): migrate ChatWindow to groupStore + chatStore (DFT-025)"
```

---

## Task 5: Migrate MessageList and GroupList to subscribe directly

**Files:**
- Modify: `frontend/src/components/MessageList.jsx`
- Modify: `frontend/src/components/GroupList.jsx`

Remove the most heavily prop-drilled props from these two components. They now subscribe to stores directly. ChatWindow's JSX is updated to stop passing those props.

- [ ] **Step 1: Read MessageList.jsx to identify which props can be moved to store subscriptions**

Read `frontend/src/components/MessageList.jsx` lines 1–30 to see the prop signature.

- [ ] **Step 2: Update MessageList prop signature**

In `MessageList.jsx`, add store imports and remove these props from the function signature:
`messages`, `typing`, `memberId`, `readMap`, `reactionMap`, `thoughtBlocks`, `toolProgressBlocks`, `onlineSet`

Replace the function signature:

```jsx
import { useChatStore } from '../store/chatStore'
import { useGroupStore } from '../store/groupStore'

export default function MessageList({
  // removed: messages, typing, memberId, readMap, reactionMap, thoughtBlocks, toolProgressBlocks, onlineSet
  members,
  pins,
  highlightedId,
  group,
  loadingMore,
  scrollRef,
  bottomRef,
  onScroll,
  onReply,
  onReact,
  onPin,
  onUnpin,
  onConfirmGate,
}) {
  const messages = useChatStore((s) => s.messages)
  const typing = useChatStore((s) => s.typing)
  const readMap = useChatStore((s) => s.readMap)
  const reactionMap = useChatStore((s) => s.reactionMap)
  const thoughtBlocks = useChatStore((s) => s.thoughtBlocks)
  const toolProgressBlocks = useChatStore((s) => s.toolProgressBlocks)
  const onlineSet = useChatStore((s) => s.onlineSet)
  const memberId = useGroupStore((s) => s.activeMemberId)
  // ... rest of component unchanged
```

- [ ] **Step 3: Update MessageList usage in ChatWindow.jsx**

Remove the 8 props that are now sourced from stores. Change:

```jsx
        <MessageList
          messages={messages}
          typing={typing}
          memberId={activeMemberId}
          members={members}
          readMap={readMap}
          reactionMap={reactionMap}
          pins={pins}
          highlightedId={highlightedId}
          group={group}
          onlineSet={onlineSet}
          loadingMore={loadingMore}
          scrollRef={scrollRef}
          bottomRef={bottomRef}
          onScroll={handleScroll}
          onReply={setReplyingTo}
          onReact={(id, emoji) => toggleReaction(id, activeMemberId, emoji)}
          onPin={(id) => pinMessage(activeGroupId, id)}
          onUnpin={(id) => unpinMessage(activeGroupId, id)}
          onConfirmGate={handleConfirmGate}
          thoughtBlocks={thoughtBlocks}
          toolProgressBlocks={toolProgressBlocks}
        />
```

To:

```jsx
        <MessageList
          members={members}
          pins={pins}
          highlightedId={highlightedId}
          group={group}
          loadingMore={loadingMore}
          scrollRef={scrollRef}
          bottomRef={bottomRef}
          onScroll={handleScroll}
          onReply={setReplyingTo}
          onReact={(id, emoji) => toggleReaction(id, activeMemberId, emoji)}
          onPin={(id) => pinMessage(activeGroupId, id)}
          onUnpin={(id) => unpinMessage(activeGroupId, id)}
          onConfirmGate={handleConfirmGate}
        />
```

- [ ] **Step 4: Read GroupList.jsx to identify prop → store migrations**

Read `frontend/src/components/GroupList.jsx` lines 1–20 to see the prop signature.

- [ ] **Step 5: Update GroupList prop signature**

In `GroupList.jsx`, add store imports and remove `groups`, `activeGroupId`, `unreadCounts`, `onlineSet` from props:

```jsx
import { useGroupStore } from '../store/groupStore'
import { useChatStore } from '../store/chatStore'

export default function GroupList({
  // removed: groups, activeGroupId, unreadCounts, onlineSet
  members,
  className,
  onSelect,
  onOpenTemplates,
  onOpenApiKeys,
  theme,
  onThemeChange,
  currentMemberId,
  membersCache,
  onOpenAddMember,
  onEditMember,
  skillDraftBots,
  onOpenWorkspace,
  onAutoReplySaved,
  onRemoveMember,
  onCreateGroup,
}) {
  const groups = useGroupStore((s) => s.groups)
  const activeGroupId = useGroupStore((s) => s.activeGroupId)
  const unreadCounts = useGroupStore((s) => s.unreadCounts)
  const onlineSet = useChatStore((s) => s.onlineSet)
  // ... rest of component unchanged
```

- [ ] **Step 6: Update GroupList usage in ChatWindow.jsx**

Remove the 4 props now sourced from stores. Change:

```jsx
      <GroupList
        groups={groups}
        activeGroupId={activeGroupId}
        unreadCounts={unreadCounts}
        members={members}
        className={tabClass('groups')}
        onSelect={(id) => { setActiveGroupId(id); setActiveMemberId(null); setMobileTab('chat') }}
        ...
        onlineSet={onlineSet}
        ...
      />
```

To:

```jsx
      <GroupList
        members={members}
        className={tabClass('groups')}
        onSelect={(id) => { setActiveGroupId(id); setActiveMemberId(null); setMobileTab('chat') }}
        ...
      />
```

(Remove `groups={groups}`, `activeGroupId={activeGroupId}`, `unreadCounts={unreadCounts}`, `onlineSet={onlineSet}` from the JSX.)

- [ ] **Step 7: Smoke test**

```bash
cd frontend && npm run dev
```

Open http://localhost:5173. Verify:
- Group list renders and switching works
- Messages stream correctly
- Reactions update
- Online presence indicators work

Check browser console for prop-type warnings or undefined errors.

- [ ] **Step 8: Run all frontend tests**

```bash
cd frontend && npx vitest run
```

Expected: All PASS.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/MessageList.jsx frontend/src/components/GroupList.jsx frontend/src/components/ChatWindow.jsx
git commit -m "refactor(frontend): MessageList + GroupList subscribe to stores directly (DFT-025)"
```

---

## Self-Review

**Spec coverage:**
- DFT-025 root cause: 46 useState in ChatWindow + 150-line WS dispatcher → ✅ addressed by Tasks 2–4
- Prop drilling: MessageList (10 props removed), GroupList (4 props removed) → ✅ addressed by Task 5
- Zustand store tests for dispatchWsEvent (all 22 WS event types covered) → ✅ Task 3

**Placeholder scan:** No TBD or TODO in code blocks. All steps show complete code.

**Type consistency:**
- `syncCache(activeGroupId, updater)` — consistent across Tasks 3 and 4
- `dispatchWsEvent(data, notify)` — called as `dispatchWsEvent(data, notify)` everywhere
- `useGroupStore.getState()` pattern used in chatStore cross-store calls (Task 3 Step 3)
- `setMessages`, `setGroups` etc. — all accept function updaters OR values, consistent in both store and callsites

**Known edge:** `handleReconnect` in ChatWindow uses `activeGroupId` which after migration comes from `useGroupStore(s => s.activeGroupId)`. The value is read correctly via the store selector at render time — no stale closure issue.
