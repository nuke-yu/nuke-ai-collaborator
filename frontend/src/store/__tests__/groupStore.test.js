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
