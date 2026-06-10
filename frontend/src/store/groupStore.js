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
