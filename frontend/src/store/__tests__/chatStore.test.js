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

  it('message updates messagesCache when entry exists', () => {
    useChatStore.setState({ messagesCache: { 1: { messages: [] } } })
    useChatStore.getState().dispatchWsEvent(
      { type: 'message', id: 10, member_id: 99, sender_name: 'Bot', content: 'hi' },
      notify
    )
    expect(useChatStore.getState().messagesCache[1].messages).toHaveLength(1)
    expect(useChatStore.getState().messagesCache[1].messages[0].id).toBe(10)
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
      { type: 'stream_end', temp_id: 'tmp1', id: 42, created_at: '2026-01-01', member_id: 99, sender_name: 'Bot', preview: 'Hello' },
      notify
    )
    const msg = useChatStore.getState().messages[0]
    expect(msg.id).toBe(42)
    expect(msg.streaming).toBe(false)
    expect(msg.temp_id).toBeUndefined()
    expect(notify).toHaveBeenCalledWith('Bot', 'Hello')
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

  it('compaction appends a compact marker', () => {
    useChatStore.getState().dispatchWsEvent(
      { type: 'compaction', strategy: 'trim', message: 'Context compacted' },
      notify
    )
    const msgs = useChatStore.getState().messages
    expect(msgs).toHaveLength(1)
    expect(msgs[0]._compact_marker).toBe(true)
    expect(msgs[0].strategy).toBe('trim')
  })

  it('stream_error removes placeholder and sets error', () => {
    useChatStore.setState({ messages: [{ temp_id: 'tmp1', streaming: true }, { id: 1 }] })
    useChatStore.getState().dispatchWsEvent({ type: 'stream_error', temp_id: 'tmp1', message: 'AI error' }, notify)
    expect(useChatStore.getState().messages).toHaveLength(1)
    expect(useChatStore.getState().error).toBe('AI error')
  })

  it('group_updated updates group and groups list via groupStore', () => {
    useGroupStore.setState({ group: { id: 1, name: 'Old' }, groups: [{ id: 1, name: 'Old' }] })
    useChatStore.getState().dispatchWsEvent({ type: 'group_updated', id: 1, name: 'New', announcement: 'Hi' }, notify)
    expect(useGroupStore.getState().group.name).toBe('New')
    expect(useGroupStore.getState().groups[0].name).toBe('New')
  })

  it('error sets error state', () => {
    useChatStore.getState().dispatchWsEvent({ type: 'error', message: 'Something failed' }, notify)
    expect(useChatStore.getState().error).toBe('Something failed')
  })

  it('recap_updated sets awaySummary for matching group', () => {
    useGroupStore.setState({ activeGroupId: 1 })
    useChatStore.getState().dispatchWsEvent({ type: 'recap_updated', group_id: 1, away_summary: 'You missed 5 msgs' }, notify)
    expect(useChatStore.getState().awaySummary).toBe('You missed 5 msgs')
  })

  it('recap_updated ignores mismatched group_id', () => {
    useGroupStore.setState({ activeGroupId: 1 })
    useChatStore.getState().dispatchWsEvent({ type: 'recap_updated', group_id: 2, away_summary: 'other group' }, notify)
    expect(useChatStore.getState().awaySummary).toBeNull()
  })

  it('member_removed removes from members and decrements count via groupStore', () => {
    useGroupStore.setState({
      activeGroupId: 1,
      members: [{ id: 5, name: 'Alice' }, { id: 6, name: 'Bob' }],
      membersCache: { 1: [{ id: 5 }, { id: 6 }] },
      groups: [{ id: 1, member_count: 2 }],
    })
    useChatStore.getState().dispatchWsEvent({ type: 'member_removed', member_id: 5 }, notify)
    expect(useGroupStore.getState().members).toHaveLength(1)
    expect(useGroupStore.getState().members[0].id).toBe(6)
    expect(useGroupStore.getState().groups[0].member_count).toBe(1)
    expect(useGroupStore.getState().membersCache[1]).toHaveLength(1)
    expect(useGroupStore.getState().membersCache[1][0].id).toBe(6)
  })

  it('skills_loaded updates skills on matching temp_id message', () => {
    useChatStore.setState({ messages: [{ temp_id: 'tmp1', content: '' }] })
    useChatStore.getState().dispatchWsEvent({ type: 'skills_loaded', temp_id: 'tmp1', skills: ['skill_a'] }, notify)
    expect(useChatStore.getState().messages[0].skills_loaded).toEqual(['skill_a'])
  })

  it('skill_draft_added adds member_id to skillDraftBots', () => {
    useChatStore.getState().dispatchWsEvent({ type: 'skill_draft_added', member_id: 42, skill: 'x' }, notify)
    expect(useChatStore.getState().skillDraftBots.has(42)).toBe(true)
  })

  it('skills_changed dispatches window CustomEvent', () => {
    const fired = []
    const handler = (e) => fired.push(e.detail)
    window.addEventListener('skills_changed', handler)
    useChatStore.getState().dispatchWsEvent({ type: 'skills_changed', skills: ['x'] }, notify)
    window.removeEventListener('skills_changed', handler)
    expect(fired).toHaveLength(1)
    expect(fired[0].skills).toEqual(['x'])
  })
})
