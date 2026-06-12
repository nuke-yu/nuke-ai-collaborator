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
  recapDismissed: false,
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
  dismissRecap: () => set({ awaySummary: null, recapDismissed: true }),
  resetRecapDismissed: () => set({ recapDismissed: false }),
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
      set((s) => ({
        typing: null,
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
            thought_id: data.temp_id,   // stable ref so thinking survives stream_end (temp_id is cleared there)
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
      if (data.group_id === activeGroupId && !get().recapDismissed) {
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
      set((s) => {
        const blocks = s.thoughtBlocks[data.temp_id] || {}
        // Resilience: if a delta arrives without iteration, route it to the active
        // (highest-numbered) block instead of a phantom [undefined] key. Object key
        // order is numeric-ascending, so the last key is the current iteration.
        let iter = data.iteration
        if (iter === undefined || iter === null) {
          const keys = Object.keys(blocks)
          iter = keys.length ? keys[keys.length - 1] : 1
        }
        const prev = blocks[iter]
        return {
          thoughtBlocks: {
            ...s.thoughtBlocks,
            [data.temp_id]: {
              ...blocks,
              [iter]: {
                iteration: prev?.iteration ?? Number(iter),
                content: (prev?.content || '') + data.delta,
                completed: prev?.completed ?? false,
              },
            },
          },
        }
      })

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
      const startKey = `${data.temp_id}-${data.call_id}`
      set((s) => ({
        toolProgressBlocks: {
          ...s.toolProgressBlocks,
          [startKey]: {
            tool_name: data.tool_name,
            args: data.tool_args,
            iteration: data.iteration,
            duration_sec: undefined,
            result: undefined,
          },
        },
      }))

    } else if (data.type === 'tool_progress_end') {
      const endKey = `${data.temp_id}-${data.call_id}`
      set((s) => ({
        toolProgressBlocks: {
          ...s.toolProgressBlocks,
          [endKey]: {
            ...s.toolProgressBlocks[endKey],
            duration_sec: data.duration_sec,
            result: data.result,
            is_error: data.is_error,
          },
        },
      }))
    }
  },
}))
