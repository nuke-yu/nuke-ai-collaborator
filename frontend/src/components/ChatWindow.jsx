import { useEffect, useState, useRef, useCallback } from 'react'
import { fetchAllGroups, fetchGroupInfo, fetchMessages, fetchUnreadCounts, fetchReactions, toggleReaction, createGroup, addMember, fetchPins, pinMessage, unpinMessage, resumeSession, cancelSessionRecovery } from '../api'
import { useWebSocket } from '../hooks/useWebSocket'
import { useNotifications } from '../hooks/useNotifications'
import GroupList from './GroupList'
import ChatHeader from './ChatHeader'
import MessageList from './MessageList'
import TemplateManager from './TemplateManager'
import MemberList from './MemberList'
import MessageInput from './MessageInput'
import SearchPanel from './SearchPanel'
import ApiKeyManager from './ApiKeyManager'
import PinnedBar from './PinnedBar'
import AnnouncementBar from './AnnouncementBar'
import WorkflowBar from './WorkflowBar'
import WorkflowStartModal from './WorkflowStartModal'
import WorkspacePanel from './WorkspacePanel'
import PermissionRequestModal from './PermissionRequestModal'

export default function ChatWindow({ memberId, isDark, onToggleTheme }) {
  const [groups, setGroups] = useState([])
  const [activeGroupId, setActiveGroupId] = useState(null)
  const [group, setGroup] = useState(null)
  const [members, setMembers] = useState([])
  const [membersCache, setMembersCache] = useState({})
  const [messages, setMessages] = useState([])
  const [messagesCache, setMessagesCache] = useState({})
  const [reactionCache, setReactionCache] = useState({})
  const [typing, setTyping] = useState(null)
  const [showTemplates, setShowTemplates] = useState(false)
  const [showApiKeys, setShowApiKeys] = useState(false)
  const [showAddMember, setShowAddMember] = useState(false)
  const [editingMember, setEditingMember] = useState(null)
  const [workspaceBot, setWorkspaceBot] = useState(null)
  const [skillDraftBots, setSkillDraftBots] = useState(new Set()) // bot IDs with pending draft skills
  const [error, setError] = useState(null)
  const [workflow, setWorkflow] = useState(null)
  const [showWorkflowStart, setShowWorkflowStart] = useState(false)
  const [wfBotOrder, setWfBotOrder] = useState([])
  const [readMap, setReadMap] = useState({})
  const [hasMore, setHasMore] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [unreadCounts, setUnreadCounts] = useState({})
  const [showSearch, setShowSearch] = useState(false)
  const [replyingTo, setReplyingTo] = useState(null)
  const [reactionMap, setReactionMap] = useState({})
  const [mobileTab, setMobileTab] = useState('chat')
  const [drafts, setDrafts] = useState({})
  const [pins, setPins] = useState([])
  const [dragging, setDragging] = useState(false)
  const [highlightedId, setHighlightedId] = useState(null)
  const [onlineSet, setOnlineSet] = useState(new Set())
  const [showStats, setShowStats] = useState(false)
  const [stats, setStats] = useState([])
  const [permRequest, setPermRequest] = useState(null)
  const [recoveryPrompts, setRecoveryPrompts] = useState([]) // Array of {session_id, bot_name, message, ...}
  const { notify } = useNotifications()
  const bottomRef = useRef(null)

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
    let active = true
    setTyping(null)
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
    fetchGroupInfo(activeGroupId).then(({ group, members }) => {
      if (active) {
        setGroup(group)
        setMembers(members)
        setMembersCache(prev => ({ ...prev, [activeGroupId]: members }))
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
    fetch(`/api/groups/${activeGroupId}/workflow`).then(r => r.json()).then(data => {
      if (active) setWorkflow(data)
    })

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

  const syncCache = (updater) => {
    setMessagesCache(prev => {
      const cur = prev[activeGroupId]
      if (!cur) return prev
      return { ...prev, [activeGroupId]: { ...cur, messages: updater(cur.messages) } }
    })
  }

  const handleWsMessage = (data) => {
    if (data.type === 'typing') {
      setTyping({ sender_name: data.sender_name, avatar_color: data.avatar_color })
    } else if (data.type === 'message') {
      setTyping(null)
      setMessages((prev) => [...prev, data])
      syncCache(msgs => [...msgs, data])
      if (data.member_id !== memberId) notify(data.sender_name, data.content)
    } else if (data.type === 'stream_start') {
      setTyping(null)
      setMessages(prev => [...prev, {
        temp_id: data.temp_id,
        member_id: data.member_id,
        sender_name: data.sender_name,
        sender_type: data.sender_type,
        avatar_color: data.avatar_color,
        content: '',
        streaming: true,
      }])
    } else if (data.type === 'stream_chunk') {
      setMessages(prev => prev.map(m =>
        m.temp_id === data.temp_id ? { ...m, content: m.content + data.delta } : m
      ))
    } else if (data.type === 'stream_end') {
      const finalize = ms => ms.map(m =>
        m.temp_id === data.temp_id
          ? { ...m, id: data.id, created_at: data.created_at, streaming: false, temp_id: undefined }
          : m
      )
      setMessages(finalize)
      syncCache(finalize)
      if (data.member_id !== memberId) notify(data.sender_name, data.preview)
    } else if (data.type === 'compaction') {
      const marker = {
        _compact_marker: true,
        id: `compact-${Date.now()}`,
        strategy: data.strategy,
        message: data.message,
      }
      setMessages(prev => [...prev, marker])
    } else if (data.type === 'stream_aborted') {
      setMessages(prev => prev.filter(m => m.temp_id !== data.temp_id))
    } else if (data.type === 'permission_request') {
      setPermRequest(data)
    } else if (data.type === 'recovery_prompt') {
      setRecoveryPrompts(prev => {
        if (prev.find(p => p.session_id === data.session_id)) return prev
        return [...prev, data]
      })
    } else if (data.type === 'stream_error') {
      setMessages(prev => prev.filter(m => m.temp_id !== data.temp_id))
      setError(data.message)
      setTimeout(() => setError(null), 5000)
    } else if (data.type === 'group_updated') {
      setGroup(prev => prev ? { ...prev, name: data.name, announcement: data.announcement } : prev)
      setGroups(prev => prev.map(g => g.id === data.id ? { ...g, name: data.name } : g))
    } else if (data.type === 'reaction_updated') {
      setReactionMap(prev => ({ ...prev, [String(data.message_id)]: data.reactions }))
    } else if (data.type === 'message_edited') {
      const applyEdit = ms => ms.map(m => m.id === data.id ? { ...m, content: data.content, edited: true } : m)
      setMessages(applyEdit)
      syncCache(applyEdit)
    } else if (data.type === 'message_deleted') {
      const applyDel = ms => ms.map(m => m.id === data.id ? { ...m, is_deleted: true } : m)
      setMessages(applyDel)
      syncCache(applyDel)
    } else if (data.type === 'error') {
      setError(data.message)
      setTimeout(() => setError(null), 5000)
    } else if (data.type === 'pins_updated') {
      setPins(data.pins)
    } else if (data.type === 'read') {
      setReadMap((prev) => ({ ...prev, [data.member_id]: data.last_read_id }))
    } else if (data.type === 'online_members') {
      setOnlineSet(new Set(data.member_ids))
    } else if (data.type === 'presence') {
      setOnlineSet(prev => {
        const next = new Set(prev)
        data.online ? next.add(data.member_id) : next.delete(data.member_id)
        return next
      })
    } else if (data.type === 'workflow_update') {
      setWorkflow(data.active ? data : null)
    } else if (data.type === 'skills_loaded') {
      setMessages(prev => prev.map(m =>
        m.temp_id === data.temp_id ? { ...m, skills_loaded: data.skills } : m
      ))
    } else if (data.type === 'skills_changed') {
      window.dispatchEvent(new CustomEvent('skills_changed', { detail: data }))
    } else if (data.type === 'skill_draft_added') {
      window.dispatchEvent(new CustomEvent('skills_changed', { detail: { ...data, source: 'bot' } }))
      if (data.member_id) {
        setSkillDraftBots(prev => new Set([...prev, data.member_id]))
      }
    }
  }


  const handleReconnect = useCallback(async () => {
    if (!activeGroupId || messages.length === 0) return
    const lastId = messages[messages.length - 1]?.id
    if (!lastId || typeof lastId !== 'number') return

    try {
      const { messages: newer } = await fetchMessages(activeGroupId, { afterId: lastId })
      if (newer && newer.length > 0) {
        setMessages(prev => {
          // Filter out any duplicates just in case
          const existingIds = new Set(prev.map(m => m.id))
          const uniqueNewer = newer.filter(m => !existingIds.has(m.id))
          return [...prev, ...uniqueNewer]
        })
        syncCache(msgs => {
          const existingIds = new Set(msgs.map(m => m.id))
          const uniqueNewer = newer.filter(m => !existingIds.has(m.id))
          return [...msgs, ...uniqueNewer]
        })
      }
    } catch (e) {
      console.error('Failed to catch up messages after reconnect:', e)
    }
  }, [activeGroupId, messages])

  const { send, sendRaw, connected, reconnecting } = useWebSocket(activeGroupId, memberId, handleWsMessage, handleReconnect, localStorage.getItem('token'))
  const isStreaming = messages.some(m => m.streaming)
  const handleAbort = () => sendRaw({ type: 'abort', group_id: activeGroupId })
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
      await resumeSession(sessionId)
      setRecoveryPrompts(prev => prev.filter(p => p.session_id !== sessionId))
    } catch (e) {
      setError('无法恢复会话：' + e.message)
    }
  }

  const handleCancelRecovery = async (sessionId) => {
    try {
      await cancelSessionRecovery(sessionId)
      setRecoveryPrompts(prev => prev.filter(p => p.session_id !== sessionId))
    } catch (e) {
      setError('无法取消恢复：' + e.message)
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
              <span className="font-semibold text-gray-200 text-sm">📊 使用统计 · {group?.name}</span>
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
              {stats.length === 0 && <p className="text-xs text-gray-500 text-center py-4">暂无数据</p>}
            </div>
            <div className="px-5 py-3 border-t border-gray-700 text-xs text-gray-600 text-right">
              共 {stats.reduce((s, m) => s + m.count, 0)} 条消息
            </div>
          </div>
        </div>
      )}

      <GroupList
        groups={groups}
        activeGroupId={activeGroupId}
        unreadCounts={unreadCounts}
        members={members}
        className={tabClass('groups')}
        onSelect={(id) => { setActiveGroupId(id); setMobileTab('chat') }}
        onOpenTemplates={() => setShowTemplates(true)}
        onOpenApiKeys={() => setShowApiKeys(true)}
        isDark={isDark}
        onToggleTheme={onToggleTheme}
        onlineSet={onlineSet}
        currentMemberId={memberId}
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
          await fetch(`/api/groups/${activeGroupId}/members/${id}`, { method: 'DELETE' })
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
          setMobileTab('chat')
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
        <WorkspacePanel bot={workspaceBot} onClose={() => setWorkspaceBot(null)} />
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
          onShowSearch={() => setShowSearch(s => !s)}
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
            <span className="text-indigo-300 text-base font-medium">释放以上传文件</span>
          </div>
        )}

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
            stages={wfBotOrder}
            onChangeStages={setWfBotOrder}
            onClose={() => setShowWorkflowStart(false)}
            onStart={async () => {
              const stages = wfBotOrder.map(s => {
                if (s.stage_type === 'pool') {
                  return { pool: s.bots.map(b => ({ bot_id: b.id })), done_keyword: s.done_keyword || '完毕' }
                }
                return { bot_id: s.id, done_keyword: s.done_keyword || '完毕' }
              })
              await fetch(`/api/groups/${activeGroupId}/workflow/start`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ stages }),
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
              <span className="font-semibold text-indigo-300">任务恢复提示</span>
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
                继续执行
              </button>
              <button
                onClick={() => handleCancelRecovery(p.session_id)}
                className="px-3 py-1 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded text-xs transition-colors"
              >
                放弃任务
              </button>
            </div>
          </div>
        ))}

        <MessageList
          messages={messages}
          typing={typing}
          memberId={memberId}
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
          onReact={(id, emoji) => toggleReaction(id, memberId, emoji)}
          onPin={(id) => pinMessage(activeGroupId, id)}
          onUnpin={(id) => unpinMessage(activeGroupId, id)}
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
              停止生成
            </button>
          </div>
        )}
        <MessageInput
          ref={messageInputRef}
          key={activeGroupId}
          groupId={activeGroupId}
          defaultValue={drafts[activeGroupId] || ''}
          onDraftSave={(gid, text) => setDrafts(prev => ({ ...prev, [gid]: text }))}
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
      </div>

      {/* 移动端底部导航 */}
      <div className="flex md:hidden fixed bottom-0 inset-x-0 z-50 bg-gray-900 border-t border-gray-700">
        {[
          { tab: 'groups', icon: '☰', label: '群组' },
          { tab: 'chat',   icon: '💬', label: '聊天' },
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
