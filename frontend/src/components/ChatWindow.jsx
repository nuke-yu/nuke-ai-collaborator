import { useEffect, useState, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { K } from '../i18n/keys'
import { fetchAllGroups, fetchGroupInfo, fetchMessages, fetchUnreadCounts, fetchReactions, toggleReaction, createGroup, deleteGroup, addMember, fetchPins, pinMessage, unpinMessage, resumeSession, cancelSessionRecovery, fetchGroupRecap, fetchPersonalRecap, ackPersonalRecap, fetchAiSuggestions } from '../api'
import { useWebSocket } from '../hooks/useWebSocket'
import { useNotifications } from '../hooks/useNotifications'
import { useGroupStore } from '../store/groupStore'
import { useChatStore } from '../store/chatStore'
import { useShallow } from 'zustand/react/shallow'
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
  const { t } = useTranslation()

  // ── Group / membership state (groupStore) ────────────────────────────────
  const activeGroupId = useGroupStore((s) => s.activeGroupId)
  const activeMemberId = useGroupStore((s) => s.activeMemberId)
  const group = useGroupStore((s) => s.group)
  const members = useGroupStore((s) => s.members)
  const membersCache = useGroupStore((s) => s.membersCache)
  const {
    setGroups, setActiveGroupId, setActiveMemberId, setGroup,
    setMembers, setMembersCache, setUnreadCounts,
  } = useGroupStore(
    useShallow((s) => ({
      setGroups: s.setGroups,
      setActiveGroupId: s.setActiveGroupId,
      setActiveMemberId: s.setActiveMemberId,
      setGroup: s.setGroup,
      setMembers: s.setMembers,
      setMembersCache: s.setMembersCache,
      setUnreadCounts: s.setUnreadCounts,
    }))
  )

  // ── WS-driven chat state (chatStore) ─────────────────────────────────────
  const messages = useChatStore((s) => s.messages)
  const typing = useChatStore((s) => s.typing)
  const permRequest = useChatStore((s) => s.permRequest)
  const recoveryPrompts = useChatStore((s) => s.recoveryPrompts)
  const workflow = useChatStore((s) => s.workflow)
  const pins = useChatStore((s) => s.pins)
  const awaySummary = useChatStore((s) => s.awaySummary)
  const skillDraftBots = useChatStore((s) => s.skillDraftBots)
  const error = useChatStore((s) => s.error)
  const messagesCache = useChatStore((s) => s.messagesCache)
  const reactionCache = useChatStore((s) => s.reactionCache)
  const hasMore = useChatStore((s) => s.hasMore)
  const loadingMore = useChatStore((s) => s.loadingMore)
  const {
    setMessages, setTyping, setReactionMap, setReactionCache,
    setMessagesCache, setPermRequest, setRecoveryPrompts, setWorkflow,
    dismissRecap, resetRecapDismissed,
    setPins, setAwaySummary, setSkillDraftBots, setError,
    setHasMore, setLoadingMore, dispatchWsEvent,
  } = useChatStore(
    useShallow((s) => ({
      setMessages: s.setMessages,
      setTyping: s.setTyping,
      setReactionMap: s.setReactionMap,
      setReactionCache: s.setReactionCache,
      setMessagesCache: s.setMessagesCache,
      setPermRequest: s.setPermRequest,
      setRecoveryPrompts: s.setRecoveryPrompts,
      setWorkflow: s.setWorkflow,
      setPins: s.setPins,
      setAwaySummary: s.setAwaySummary,
      dismissRecap: s.dismissRecap,
      resetRecapDismissed: s.resetRecapDismissed,
      setSkillDraftBots: s.setSkillDraftBots,
      setError: s.setError,
      setHasMore: s.setHasMore,
      setLoadingMore: s.setLoadingMore,
      dispatchWsEvent: s.dispatchWsEvent,
    }))
  )

  // ── Local modal / layout state (stays as useState) ───────────────────────
  const [showTemplates, setShowTemplates] = useState(false)
  const [showApiKeys, setShowApiKeys] = useState(false)
  const [showAddMember, setShowAddMember] = useState(false)
  const [editingMember, setEditingMember] = useState(null)
  const [workspaceBot, setWorkspaceBot] = useState(null)
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
  // 每群「当前这条 personal recap 实际覆盖到的最新消息 id」——点 ✕ ack 时回传，
  // 让水位线钉在 recap 覆盖点而非点击时刻的 MAX(id)（TOCTOU）。
  const personalCoveredId = useRef({})
  const { notify } = useNotifications()
  const bottomRef = useRef(null)

  const handleFetchAiSuggestions = async () => {
    if (!activeGroupId || suggestionsLoading) return
    setSuggestionsLoading(true)
    try {
      const data = await fetchAiSuggestions(activeGroupId, workflow?.awaiting_confirm || null)
      setAiSuggestions(data.suggestions || [])
    } catch (err) {
      console.error('Failed to fetch AI suggestions:', err)
      notify(t(K.chat.errors.fetchSuggestionsFailed), 'error')
    } finally {
      setSuggestionsLoading(false)
    }
  }

  // Auto-clear AI suggestions on message addition or workflow transition
  useEffect(() => {
    setAiSuggestions([])
  }, [activeGroupId, messages.length, workflow?.active, workflow?.awaiting_confirm])

  const loadRecap = useCallback(async (groupId) => {
    if (!groupId) return
    if (useChatStore.getState().recapDismissed) return
    try {
      const data = await fetchGroupRecap(groupId)
      if (!useChatStore.getState().recapDismissed) {
        setAwaySummary(data?.away_summary || null)
      }
    } catch (err) {
      console.error('Failed to fetch group recap:', err)
    }
  }, [])

  // 方案 1：按需拉「我」错过的 per-user recap；10s 内不重复触发（每次都现算 LLM）。
  const loadPersonalRecap = useCallback(async (groupId) => {
    if (!groupId || !memberId) return
    const now = Date.now()
    if (now - (personalRecapAt.current[groupId] || 0) < 10000) return
    personalRecapAt.current[groupId] = now
    try {
      const data = await fetchPersonalRecap(groupId, memberId)
      setPersonalSummary(data?.unread_count > 0 ? (data.summary || null) : null)
      personalCoveredId.current[groupId] = data?.covered_through_id || 0
    } catch (err) {
      console.error('Failed to fetch personal recap:', err)
    }
  }, [memberId])

  const handleDismissRecap = async () => {
    if (!activeGroupId) return
    const prevSummary = personalSummary   // ack 失败时回滚用
    setPersonalSummary(null)
    dismissRecap()   // 本地立即隐藏（awaySummary=null + recapDismissed=true）
    try {
      // 持久化「我看过了」：推进个人水位线到「这条 recap 覆盖到的消息 id」（非点击时刻 MAX(id)），
      // 服务端这批不再返回（重连/切群也不再弹），仅清自己的；停留期间到达的新消息仍会再弹。
      if (memberId) await ackPersonalRecap(activeGroupId, memberId, personalCoveredId.current[activeGroupId])
    } catch (err) {
      console.error('Failed to ack personal recap:', err)
      // ack 没落库就别假装已读：恢复横幅，避免"看似已读、刷新又回来"的错觉。
      setPersonalSummary(prevSummary)
    }
  }

  const handleRegenerateRecap = async () => {
    if (!activeGroupId) return
    setLoadingRecap(true)
    try {
      const res = await fetch(`/api/groups/${activeGroupId}/recap/trigger`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: 'Bearer ' + localStorage.getItem('token')
        }
      })
      const data = await res.json()
      if (data.ok) {
        setAwaySummary(data.away_summary)
      }
    } catch (err) {
      console.error('Failed to trigger recap regeneration:', err)
    } finally {
      setLoadingRecap(false)
    }
  }

  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible' && activeGroupId) {
        resetRecapDismissed()   // user came back from being away — allow recap to show
        loadRecap(activeGroupId)
        loadPersonalRecap(activeGroupId)
      }
    }
    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [activeGroupId, loadRecap, loadPersonalRecap])

  const scrollRef = useRef(null)
  const messageInputRef = useRef(null)
  const dragCounter = useRef(0)

  useEffect(() => {
    fetchAllGroups().then((gs) => {
      setGroups(gs)
      if (gs.length > 0) setActiveGroupId(gs[0].id)
    })
    fetchUnreadCounts(memberId).then(setUnreadCounts)
  }, [])

  useEffect(() => {
    const handler = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        setShowSearch(s => !s)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  useEffect(() => {
    if (!activeGroupId) return
    resetRecapDismissed()   // switching group = new context, allow recap to show again
    let active = true
    useChatStore.getState().setThoughtBlocks({})
    useChatStore.getState().setToolProgressBlocks({})
    setTyping(null)
    setActiveMemberId(null) // Reset activeMemberId to prevent connection with old member ID
    setUnreadCounts(prev => ({ ...prev, [activeGroupId]: 0 }))

    // 有缓存时立即显示，无缓存时清空等待
    const cachedMsgs = messagesCache[activeGroupId]
    if (cachedMsgs) {
      setMessages(cachedMsgs.messages)
      setHasMore(cachedMsgs.hasMore)
      setReactionMap(reactionCache[activeGroupId] || {})
      setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: 'instant' }), 0)
    } else {
      setMessages([])
      setHasMore(false)
    }

    fetchPins(activeGroupId).then(data => {
      if (active) setPins(data)
    })
    fetchGroupInfo(activeGroupId).then(async ({ group, members }) => {
      if (!active) return
      const currentUser = JSON.parse(localStorage.getItem('user'))
      const currentUsername = currentUser?.username || 'Guest'
      let userMember = members.find(m => m.type === 'human' && m.name === currentUsername)
      let finalMembers = members

      if (!userMember) {
        // Automatically join the group if the user is not a member yet
        try {
          const newMember = await addMember(activeGroupId, currentUsername)
          userMember = newMember
          finalMembers = [...members, { ...newMember, avatar_color: '#f59e0b' }]
        } catch (e) {
          console.error('Failed to auto-join group:', e)
        }
      }

      if (active) {
        setGroup(group)
        setMembers(finalMembers)
        setMembersCache(prev => ({ ...prev, [activeGroupId]: finalMembers }))
        setGroups(prev => prev.map(g =>
          g.id === activeGroupId ? { ...g, member_count: finalMembers.length } : g
        ))
        if (userMember) {
          setActiveMemberId(userMember.id)
        } else {
          setActiveMemberId(memberId) // Fallback
        }
      }
    })
    fetchReactions(activeGroupId).then(data => {
      if (active) {
        setReactionMap(data)
        setReactionCache(prev => ({ ...prev, [activeGroupId]: data }))
      }
    })
    fetchMessages(activeGroupId).then(({ messages, has_more }) => {
      if (active) {
        setMessages(messages)
        setHasMore(has_more)
        setMessagesCache(prev => ({ ...prev, [activeGroupId]: { messages, hasMore: has_more } }))
        setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: 'instant' }), 0)
      }
    })
    fetch(`/api/groups/${activeGroupId}/workflow`, {
      headers: { Authorization: 'Bearer ' + localStorage.getItem('token') },
    }).then(r => r.json()).then(data => {
      if (active) setWorkflow(data)
    })
    loadRecap(activeGroupId)
    loadPersonalRecap(activeGroupId)

    return () => {
      active = false
    }
  }, [activeGroupId])

  const loadMore = useCallback(async () => {
    if (loadingMore || !hasMore || messages.length === 0) return
    setLoadingMore(true)
    const oldestId = messages[0].id
    const container = scrollRef.current
    const prevScrollHeight = container?.scrollHeight ?? 0

    const { messages: older, has_more } = await fetchMessages(activeGroupId, { beforeId: oldestId })
    setMessages(prev => [...older, ...prev])
    setHasMore(has_more)
    setLoadingMore(false)

    // 保持滚动位置：新增内容在上方，补偿高度差
    requestAnimationFrame(() => {
      if (container) {
        container.scrollTop = container.scrollHeight - prevScrollHeight
      }
    })
  }, [loadingMore, hasMore, messages, activeGroupId])

  const handleScroll = useCallback(() => {
    if ((scrollRef.current?.scrollTop ?? 1) < 80) {
      loadMore()
    }
  }, [loadMore])

  const handleWsMessage = useCallback((data) => {
    dispatchWsEvent(data, notify)
  }, [dispatchWsEvent, notify])

  const handleReconnect = useCallback(async () => {
    if (!activeGroupId || messages.length === 0) return
    const lastId = messages[messages.length - 1]?.id
    if (!lastId || typeof lastId !== 'number') return

    loadRecap(activeGroupId)

    try {
      const { messages: newer } = await fetchMessages(activeGroupId, { afterId: lastId })
      if (newer && newer.length > 0) {
        setMessages(prev => {
          // Filter out any duplicates just in case
          const existingIds = new Set(prev.map(m => m.id))
          const uniqueNewer = newer.filter(m => !existingIds.has(m.id))
          return [...prev, ...uniqueNewer]
        })
        useChatStore.getState().syncCache(activeGroupId, (msgs) => {
          const existingIds = new Set(msgs.map(m => m.id))
          const uniqueNewer = newer.filter(m => !existingIds.has(m.id))
          return [...msgs, ...uniqueNewer]
        })
      }
    } catch (e) {
      console.error('Failed to catch up messages after reconnect:', e)
    }
  }, [activeGroupId, messages])

  // Stable ref so MessageInput's [groupId, onDraftSave] effect doesn't re-run (and
  // re-fire its draft-save cleanup → setDrafts → re-render) on every render — that
  // was an infinite "Maximum update depth exceeded" loop.
  const handleDraftSave = useCallback((gid, text) => {
    setDrafts(prev => ({ ...prev, [gid]: text }))
  }, [])

  const { send, sendRaw, connected, reconnecting } = useWebSocket(activeGroupId, activeMemberId, handleWsMessage, handleReconnect, localStorage.getItem('token'), onLogout)
  const isStreaming = messages.some(m => m.streaming)
  const handleAbort = () => sendRaw({ type: 'abort', group_id: activeGroupId })
  const handleConfirmGate = (gateId) => sendRaw({ type: 'confirm', group_id: activeGroupId, gate_id: gateId })
  const handlePermResponse = (requestId, approved, persistence) => {
    sendRaw({ type: 'permission_response', request_id: requestId, approved, persistence })
    setPermRequest(null)
  }


  const saveAnnouncement = (text) => {
    fetch(`/api/groups/${activeGroupId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ announcement: text }),
    })
  }

  const handleResume = async (sessionId) => {
    try {
      await resumeSession(sessionId, activeGroupId)
      setRecoveryPrompts(prev => prev.filter(p => p.session_id !== sessionId))
    } catch (e) {
      setError(t(K.chat.errors.resumeFailed, { message: e.message }))
    }
  }

  const handleCancelRecovery = async (sessionId) => {
    try {
      await cancelSessionRecovery(sessionId, activeGroupId)
      setRecoveryPrompts(prev => prev.filter(p => p.session_id !== sessionId))
    } catch (e) {
      setError(t(K.chat.errors.cancelRecoveryFailed, { message: e.message }))
    }
  }


  const handleJump = useCallback((msgId) => {
    setShowSearch(false)
    setTimeout(() => {
      const el = document.querySelector(`[data-msg-id="${msgId}"]`)
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
        setHighlightedId(msgId)
        setTimeout(() => setHighlightedId(null), 2000)
      }
    }, 100)
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, typing])

  const tabClass = (tab) =>
    mobileTab === tab ? '' : 'hidden md:flex'

  return (
    <div className="flex h-screen bg-gray-900 relative">
      {showTemplates && (
        <TemplateManager
          onClose={() => setShowTemplates(false)}
          groupId={activeGroupId}
          onAdded={async () => {
            const { members: updated } = await fetchGroupInfo(activeGroupId)
            setMembers(updated)
            setMembersCache(prev => ({ ...prev, [activeGroupId]: updated }))
            setGroups(prev => prev.map(g =>
              g.id === activeGroupId ? { ...g, member_count: updated.length } : g
            ))
          }}
        />
      )}
      {showApiKeys && <ApiKeyManager onClose={() => setShowApiKeys(false)} />}
      {showStats && (
        <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center" onClick={() => setShowStats(false)}>
          <div className="bg-gray-800 border border-gray-700 rounded-2xl shadow-2xl w-80 overflow-hidden" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700">
              <span className="font-semibold text-gray-200 text-sm">📊 {t(K.chat.stats.title)} · {group?.name}</span>
              <button onClick={() => setShowStats(false)} className="text-gray-500 hover:text-gray-300 text-lg leading-none">✕</button>
            </div>
            <div className="p-4 space-y-2">
              {stats.map((s, i) => (
                <div key={i} className="flex items-center gap-3">
                  <span className="text-xs text-gray-500 w-5 text-right">{i + 1}</span>
                  <span className="text-xs flex-shrink-0">{s.type === 'bot' ? '🤖' : '👤'}</span>
                  <span className="text-sm text-gray-200 flex-1 truncate">{s.name}</span>
                  <div className="flex items-center gap-2">
                    <div className="h-1.5 bg-indigo-500 rounded-full" style={{ width: `${Math.max(4, (s.count / (stats[0]?.count || 1)) * 80)}px` }} />
                    <span className="text-xs text-gray-400 w-8 text-right">{s.count}</span>
                  </div>
                </div>
              ))}
              {stats.length === 0 && <p className="text-xs text-gray-500 text-center py-4">{t(K.chat.stats.noData)}</p>}
            </div>
            <div className="px-5 py-3 border-t border-gray-700 text-xs text-gray-600 text-right">
              {t(K.chat.stats.totalMessages, { count: stats.reduce((s, m) => s + m.count, 0) })}
            </div>
          </div>
        </div>
      )}

      <GroupList
        className={tabClass('groups')}
        onSelect={(id) => { setActiveGroupId(id); setActiveMemberId(null); setMobileTab('chat') }}
        onOpenTemplates={() => setShowTemplates(true)}
        onOpenApiKeys={() => setShowApiKeys(true)}
        theme={theme}
        onThemeChange={onThemeChange}
        currentMemberId={activeMemberId}
        membersCache={membersCache}
        onOpenAddMember={() => setShowAddMember(true)}
        onEditMember={(m) => setEditingMember(m)}
        skillDraftBots={skillDraftBots}
        onOpenWorkspace={(m) => {
          setWorkspaceBot(m)
          setSkillDraftBots(prev => { const next = new Set(prev); next.delete(m.id); return next })
        }}
        onAutoReplySaved={(memberId, text) => {
          setMembers(prev => prev.map(m => m.id === memberId ? { ...m, auto_reply: text } : m))
          setMembersCache(prev => ({
            ...prev,
            [activeGroupId]: (prev[activeGroupId] || []).map(m =>
              m.id === memberId ? { ...m, auto_reply: text } : m
            )
          }))
        }}
        onRemoveMember={async (id) => {
          const res = await fetch(`/api/groups/${activeGroupId}/members/${id}`, { method: 'DELETE' })
          if (!res.ok) {
            setError(t(K.chat.errors.removeMemberFailed, { status: res.status }))
            setTimeout(() => setError(null), 5000)
            return
          }
          setMembers(prev => prev.filter(m => m.id !== id))
          setMembersCache(prev => ({
            ...prev,
            [activeGroupId]: (prev[activeGroupId] || []).filter(m => m.id !== id)
          }))
          setGroups(prev => prev.map(g =>
            g.id === activeGroupId ? { ...g, member_count: (g.member_count ?? 1) - 1 } : g
          ))
        }}
        onCreateGroup={async (name) => {
          const g = await createGroup(name)
          setGroups((prev) => [...prev, g])
          setActiveGroupId(g.id)
          setActiveMemberId(null)
          setMobileTab('chat')
        }}
        onDeleteGroup={async (groupId) => {
          await deleteGroup(groupId)
          setGroups((prev) => {
            const remaining = prev.filter(g => g.id !== groupId)
            if (activeGroupId === groupId) {
              setActiveGroupId(remaining.length > 0 ? remaining[0].id : null)
              setActiveMemberId(null)
            }
            return remaining
          })
        }}
      />
      {showAddMember && (
        <MemberList
          onAddMember={async (form) => {
            const result = await addMember(activeGroupId, form)
            const { members: updated } = await fetchGroupInfo(activeGroupId)
            setMembers(updated)
            setMembersCache(prev => ({ ...prev, [activeGroupId]: updated }))
            setGroups(prev => prev.map(g =>
              g.id === activeGroupId ? { ...g, member_count: updated.length } : g
            ))
            return result
          }}
          onClose={() => setShowAddMember(false)}
        />
      )}
      {editingMember && (
        <MemberList
          initialData={editingMember}
          onEditMember={async (id, form) => {
            await fetch(`/api/members/${id}`, {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(form),
            })
            const { members: updated } = await fetchGroupInfo(activeGroupId)
            setMembers(updated)
            setMembersCache(prev => ({ ...prev, [activeGroupId]: updated }))
          }}
          onClose={() => setEditingMember(null)}
        />
      )}
      {workspaceBot && (
        <WorkspacePanel bot={workspaceBot} groupId={activeGroupId} onClose={() => setWorkspaceBot(null)} />
      )}
      <div className={`${tabClass('chat')} flex-1 min-w-0 flex flex-col md:flex-row`}>
      <div
        className={`flex flex-col min-w-0 ${showSearch ? 'hidden md:flex' : 'flex'} flex-1 relative`}
        onDragEnter={(e) => { e.preventDefault(); dragCounter.current++; if (e.dataTransfer.types.includes('Files')) setDragging(true) }}
        onDragLeave={(e) => { e.preventDefault(); dragCounter.current--; if (dragCounter.current === 0) setDragging(false) }}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => { e.preventDefault(); dragCounter.current = 0; setDragging(false); const f = e.dataTransfer.files[0]; if (f) messageInputRef.current?.uploadFile(f) }}
      >
        <ChatHeader onLogout={onLogout}
          activeGroupId={activeGroupId}
          group={group}
          members={members}
          reconnecting={reconnecting}
          workflow={workflow}
          onShowSearch={() => { setShowSearch(s => !s); setShowBotLogs(false); }}
          onShowBotLogs={() => { setShowBotLogs(s => !s); setShowSearch(false); }}
          onShowStats={(s) => { setStats(s); setShowStats(true); }}
          onShowWorkflowStart={() => {
            const defaultKeyword = (m) => {
              if (m.done_keyword) return m.done_keyword
              const role = m.role || m.name
              if (role.includes('需求')) return '需求确认完毕'
              if (role.includes('架构')) return '架构设计完毕'
              if (role.includes('前端')) return '前端开发完毕'
              if (role.includes('后端') || role.includes('开发') || role.includes('工程师')) return '开发完毕'
              if (role.includes('测试')) return '测试完成'
              if (role.includes('运维')) return '运维完毕'
              return `${m.name}完毕`
            }
            const isDevBot = (m) => {
              const t = (m.role || m.name || '').toLowerCase()
              return t.includes('开发') || t.includes('工程师') || t.includes('developer') || t.includes('engineer')
            }
            const bots = members.filter(m => m.type === 'bot')
            const devBots = bots.filter(isDevBot)
            const stages = []
            let poolAdded = false
            for (const m of bots) {
              if (isDevBot(m)) {
                if (!poolAdded) {
                  if (devBots.length > 1) {
                    stages.push({ stage_type: 'pool', bots: devBots.map(b => ({...b})), done_keyword: '开发完毕' })
                  } else {
                    stages.push({ stage_type: 'single', ...m, done_keyword: defaultKeyword(m) })
                  }
                  poolAdded = true
                }
              } else {
                stages.push({ stage_type: 'single', ...m, done_keyword: defaultKeyword(m) })
              }
            }
            setWfBotOrder(stages)
            setShowWorkflowStart(true)
          }}
        />

        {dragging && (
          <div className="absolute inset-0 z-40 bg-indigo-500/10 border-2 border-dashed border-indigo-400 rounded-lg flex items-center justify-center pointer-events-none">
            <span className="text-indigo-300 text-base font-medium">{t(K.chat.dragDrop.releaseToUpload)}</span>
          </div>
        )}

        {/* 只看每用户的「你错过的」recap：已被服务端水位线 gate —— 点 ✕ 后这批不再显示，
            重连/切群也不再弹；有全新活动才再弹。群级 away_summary 不再驱动横幅显示。 */}
        <RecapBanner
          summary={personalSummary}
          loading={loadingRecap}
          onDismiss={handleDismissRecap}
          onRegenerate={handleRegenerateRecap}
        />

        <AnnouncementBar
          announcement={group?.announcement || null}
          onSave={saveAnnouncement}
        />
        <WorkflowBar
          workflow={workflow}
          onNext={async () => {
            await fetch(`/api/groups/${activeGroupId}/workflow/next`, { method: 'POST' })
          }}
          onEnd={async () => {
            await fetch(`/api/groups/${activeGroupId}/workflow`, { method: 'DELETE' })
          }}
        />
        <PinnedBar
          pins={pins}
          onUnpin={(msgId) => unpinMessage(activeGroupId, msgId)}
        />
        {showWorkflowStart && (
          <WorkflowStartModal
            groupBots={members.filter(m => m.type === 'bot')}
            onClose={() => setShowWorkflowStart(false)}
            onStart={async (payload) => {
              await fetch(`/api/groups/${activeGroupId}/workflow/start`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
              })
              setShowWorkflowStart(false)
            }}
          />
        )}

        {error && (
          <div className="mx-4 mt-2 px-4 py-2 bg-red-900/80 border border-red-700 rounded-lg text-sm text-red-200 flex items-center justify-between">
            <span>⚠ {error}</span>
            <button onClick={() => setError(null)} className="text-red-400 hover:text-red-200 ml-4">✕</button>
          </div>
        )}

        {recoveryPrompts.map(p => (
          <div key={p.session_id} className="mx-4 mt-2 px-4 py-3 bg-indigo-950/80 border border-indigo-700 rounded-lg text-sm text-indigo-100 flex flex-col gap-2 shadow-lg animate-in slide-in-from-top duration-300">
            <div className="flex items-center justify-between">
              <span className="font-semibold text-indigo-300">{t(K.chat.recovery.title)}</span>
              <button onClick={() => handleCancelRecovery(p.session_id)} className="text-indigo-400 hover:text-indigo-200">✕</button>
            </div>
            <p>{p.message}</p>
            {p.user_message && (
              <p className="text-xs text-indigo-400 bg-black/30 p-2 rounded italic">“{p.user_message}”</p>
            )}
            <div className="flex gap-2 mt-1">
              <button
                onClick={() => handleResume(p.session_id)}
                className="px-3 py-1 bg-indigo-600 hover:bg-indigo-500 text-white rounded text-xs transition-colors"
              >
                {t(K.chat.recovery.continueExec)}
              </button>
              <button
                onClick={() => handleCancelRecovery(p.session_id)}
                className="px-3 py-1 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded text-xs transition-colors"
              >
                {t(K.chat.recovery.abandonTask)}
              </button>
            </div>
          </div>
        ))}

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
        {isStreaming && (
          <div className="px-4 py-1 flex justify-center">
            <button
              onClick={handleAbort}
              className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-red-400 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-full px-3 py-1 transition-colors"
            >
              <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 24 24">
                <rect x="6" y="6" width="12" height="12" rx="1" />
              </svg>
              {t(K.chat.stopGenerate)}
            </button>
          </div>
        )}
        <SuggestionBar
          workflow={workflow}
          isStreaming={isStreaming}
          awaySummary={awaySummary}
          messages={messages}
          members={members}
          aiSuggestions={aiSuggestions}
          loading={suggestionsLoading}
          onFetch={handleFetchAiSuggestions}
          onSelect={(text, action) => {
            if (action === 'confirm') {
              if (workflow?.awaiting_confirm) {
                handleConfirmGate(workflow.awaiting_confirm);
              }
            } else if (action === 'start') {
              sendRaw({ type: 'start_workflow', group_id: activeGroupId, lang: localStorage.getItem('lang') || 'zh' });
            } else if (action === 'abort') {
              handleAbort();
            } else if (text) {
              messageInputRef.current?.setInputText(text);
            }
          }}
        />
        <MessageInput
          ref={messageInputRef}
          key={activeGroupId}
          groupId={activeGroupId}
          defaultValue={drafts[activeGroupId] || ''}
          onDraftSave={handleDraftSave}
          onSend={(content, fileData) => { send(content, replyingTo?.id ?? null, fileData); setReplyingTo(null) }}
          members={members}
          disabled={!connected}
          replyingTo={replyingTo}
          onCancelReply={() => setReplyingTo(null)}
        />
        {permRequest && (
          <PermissionRequestModal request={permRequest} onRespond={handlePermResponse} />
        )}
      </div>
      {showSearch && activeGroupId && (
        <SearchPanel groupId={activeGroupId} onClose={() => setShowSearch(false)} onJump={handleJump} />
      )}
      {showBotLogs && activeGroupId && (
        <BotLogPanel groupId={activeGroupId} onClose={() => setShowBotLogs(false)} />
      )}
      </div>

      {/* 移动端底部导航 */}
      <div className="flex md:hidden fixed bottom-0 inset-x-0 z-50 bg-gray-900 border-t border-gray-700">
        {[
          { tab: 'groups', icon: '☰', label: t(K.chat.tabs.groups) },
          { tab: 'chat',   icon: '💬', label: t(K.chat.tabs.chat) },
        ].map(({ tab, icon, label }) => (
          <button
            key={tab}
            onClick={() => setMobileTab(tab)}
            className={`flex-1 flex flex-col items-center gap-0.5 py-2 text-xs transition-colors ${
              mobileTab === tab ? 'text-indigo-400' : 'text-gray-500'
            }`}
          >
            <span className="text-lg leading-none">{icon}</span>
            <span>{label}</span>
          </button>
        ))}
      </div>
    </div>
  )
}
