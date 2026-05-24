import { useEffect, useState, useRef, useCallback, Fragment } from 'react'
import { fetchAllGroups, fetchGroupInfo, fetchMessages, fetchUnreadCounts, fetchReactions, toggleReaction, createGroup, addMember, fetchPins, pinMessage, unpinMessage, fetchGroupStats, exportGroupUrl } from '../api'
import { useWebSocket } from '../hooks/useWebSocket'
import { useNotifications } from '../hooks/useNotifications'
import GroupList from './GroupList'
import TemplateManager from './TemplateManager'
import MemberList from './MemberList'
import MessageBubble from './MessageBubble'
import MessageInput from './MessageInput'
import SearchPanel from './SearchPanel'
import ApiKeyManager from './ApiKeyManager'
import PinnedBar from './PinnedBar'
import AnnouncementBar from './AnnouncementBar'
import WorkflowBar from './WorkflowBar'
import WorkflowStartModal from './WorkflowStartModal'
import WorkspacePanel from './WorkspacePanel'

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
  const [editingGroupName, setEditingGroupName] = useState(false)
  const [groupNameDraft, setGroupNameDraft] = useState('')
  const [mobileTab, setMobileTab] = useState('chat')
  const [drafts, setDrafts] = useState({})
  const [pins, setPins] = useState([])
  const [dragging, setDragging] = useState(false)
  const [highlightedId, setHighlightedId] = useState(null)
  const [onlineSet, setOnlineSet] = useState(new Set())
  const [showStats, setShowStats] = useState(false)
  const [stats, setStats] = useState([])
  const [showExportMenu, setShowExportMenu] = useState(false)
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

    fetchPins(activeGroupId).then(setPins)
    fetchGroupInfo(activeGroupId).then(({ group, members }) => {
      setGroup(group)
      setMembers(members)
      setMembersCache(prev => ({ ...prev, [activeGroupId]: members }))
    })
    fetchReactions(activeGroupId).then(data => {
      setReactionMap(data)
      setReactionCache(prev => ({ ...prev, [activeGroupId]: data }))
    })
    fetchMessages(activeGroupId).then(({ messages, has_more }) => {
      setMessages(messages)
      setHasMore(has_more)
      setMessagesCache(prev => ({ ...prev, [activeGroupId]: { messages, hasMore: has_more } }))
      setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: 'instant' }), 0)
    })
    fetch(`/api/groups/${activeGroupId}/workflow`).then(r => r.json()).then(setWorkflow)
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
    }
  }

  const { send, connected, reconnecting } = useWebSocket(activeGroupId, memberId, handleWsMessage)

  const saveGroupName = async () => {
    const name = groupNameDraft.trim()
    if (!name || name === group?.name) { setEditingGroupName(false); return }
    await fetch(`/api/groups/${activeGroupId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    })
    setEditingGroupName(false)
  }

  const saveAnnouncement = async (text) => {
    await fetch(`/api/groups/${activeGroupId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ announcement: text }),
    })
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
        onOpenWorkspace={(m) => setWorkspaceBot(m)}
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
            const result = await addMember(activeGroupId, form.name, form.type, form.role, form.system_prompt, form.avatar_color, form.model_provider, form.model_name)
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
        <div className="h-14 bg-gray-900 border-b border-gray-700 flex items-center px-4 gap-2 flex-shrink-0">
          {editingGroupName ? (
            <input
              autoFocus
              value={groupNameDraft}
              onChange={e => setGroupNameDraft(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && !e.nativeEvent.isComposing) saveGroupName()
                if (e.key === 'Escape') setEditingGroupName(false)
              }}
              onBlur={saveGroupName}
              className="bg-gray-800 text-gray-100 font-semibold text-sm rounded px-2 py-0.5 outline-none focus:ring-1 focus:ring-indigo-500 w-40"
            />
          ) : (
            <button
              onClick={() => { setGroupNameDraft(group?.name || ''); setEditingGroupName(true) }}
              className="text-gray-300 font-semibold hover:text-white transition-colors"
              title="点击重命名"
            >
              # {group?.name || '选择群组'}
            </button>
          )}
          <span className="text-xs text-gray-500 ml-2">· {members.length} 名成员</span>
          {reconnecting && (
            <span className="text-xs text-yellow-400 animate-pulse">⚠ 连接断开，正在重连...</span>
          )}
          <div className="ml-auto flex items-center gap-1">
            {members.some(m => m.type === 'bot') && !workflow?.active && (
              <button
                onClick={() => {
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
                className="text-sm px-2 py-1 rounded text-gray-500 hover:text-indigo-400 transition-colors"
                title="启动工作流"
              >⚡</button>
            )}
            <button
              onClick={async () => { const s = await fetchGroupStats(activeGroupId); setStats(s); setShowStats(true) }}
              className="text-sm px-2 py-1 rounded text-gray-500 hover:text-gray-300 transition-colors"
              title="使用统计"
            >📊</button>
            <div className="relative">
              <button
                onClick={() => setShowExportMenu(m => !m)}
                className={`text-sm px-2 py-1 rounded transition-colors ${showExportMenu ? 'text-indigo-400 bg-indigo-950/50' : 'text-gray-500 hover:text-gray-300'}`}
                title="导出聊天记录"
              >⬇️</button>
              {showExportMenu && (
                <div className="absolute right-0 top-full mt-1 bg-gray-800 border border-gray-700 rounded-lg shadow-xl overflow-hidden z-50 w-36">
                  <a href={exportGroupUrl(activeGroupId, 'markdown')} download onClick={() => setShowExportMenu(false)}
                    className="flex items-center gap-2 px-3 py-2 text-xs text-gray-300 hover:bg-gray-700 transition-colors no-underline">
                    <span>📝</span> 导出 Markdown
                  </a>
                  <a href={exportGroupUrl(activeGroupId, 'json')} download onClick={() => setShowExportMenu(false)}
                    className="flex items-center gap-2 px-3 py-2 text-xs text-gray-300 hover:bg-gray-700 transition-colors no-underline">
                    <span>📋</span> 导出 JSON
                  </a>
                </div>
              )}
            </div>
            <button
              onClick={() => setShowSearch(s => !s)}
              className={`text-sm px-2 py-1 rounded transition-colors ${showSearch ? 'text-indigo-400 bg-indigo-950/50' : 'text-gray-500 hover:text-gray-300'}`}
              title="搜索消息 (⌘K)"
            >🔍</button>
          </div>
        </div>

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

        <div
          ref={scrollRef}
          className="flex-1 overflow-y-auto px-4 py-4 space-y-1 pb-14 md:pb-4"
          onScroll={handleScroll}
        >
          {loadingMore && (
            <div className="text-center py-3">
              <span className="inline-block w-4 h-4 border-2 border-gray-600 border-t-gray-400 rounded-full animate-spin" />
            </div>
          )}
          {messages.length === 0 && !loadingMore && group && (
            <div className="flex-1 flex flex-col items-center justify-center gap-5 py-16 text-center select-none">
              <div className="text-5xl">💬</div>
              <div>
                <h3 className="text-gray-200 font-semibold text-base mb-1"># {group.name}</h3>
                <p className="text-gray-500 text-sm">这是 <span className="text-indigo-400 font-medium">{group.name}</span> 的开始</p>
              </div>
              {members.length > 0 && (
                <div>
                  <p className="text-xs text-gray-600 mb-3">群组成员</p>
                  <div className="flex gap-3 justify-center flex-wrap">
                    {members.map(m => (
                      <div key={m.id} className="flex flex-col items-center gap-1.5">
                        <div className="relative">
                          <div className="w-9 h-9 rounded-full flex items-center justify-center text-sm font-bold text-white" style={{ backgroundColor: m.avatar_color }}>
                            {m.name[0]}
                          </div>
                          {onlineSet.has(m.id) && <span className="absolute bottom-0 right-0 w-2.5 h-2.5 bg-green-400 rounded-full border-2 border-gray-900" />}
                        </div>
                        <span className="text-xs text-gray-500">{m.name}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <p className="text-xs text-gray-600 mt-2">发送消息开始对话 👇</p>
            </div>
          )}
          {messages.map((msg, i) => {
            const msgDay = msg.created_at ? new Date(msg.created_at).toDateString() : null
            const prevDay = i > 0 && messages[i - 1].created_at ? new Date(messages[i - 1].created_at).toDateString() : null
            const showDate = msgDay && msgDay !== prevDay
            let dateLabel = ''
            if (showDate) {
              const today = new Date().toDateString()
              const yesterday = new Date(Date.now() - 86400000).toDateString()
              if (msgDay === today) dateLabel = '今天'
              else if (msgDay === yesterday) dateLabel = '昨天'
              else dateLabel = new Date(msg.created_at).toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' })
            }
            return (
              <Fragment key={msg.id ?? msg.temp_id}>
                {showDate && (
                  <div className="flex items-center gap-3 py-2 my-1">
                    <div className="flex-1 h-px bg-gray-800" />
                    <span className="text-xs text-gray-500 flex-shrink-0">{dateLabel}</span>
                    <div className="flex-1 h-px bg-gray-800" />
                  </div>
                )}
                <div data-msg-id={msg.id}>
                  <MessageBubble
                    msg={msg}
                    currentMemberId={memberId}
                    members={members}
                    readMap={readMap}
                    onReply={setReplyingTo}
                    reactions={reactionMap[String(msg.id)] || {}}
                    onReact={(emoji) => toggleReaction(msg.id, memberId, emoji)}
                    isPinned={pins.some(p => p.id === msg.id)}
                    onPin={(id) => pinMessage(activeGroupId, id)}
                    onUnpin={(id) => unpinMessage(activeGroupId, id)}
                    highlighted={msg.id === highlightedId}
                  />
                </div>
              </Fragment>
            )
          })}
          {typing && <MessageBubble msg={typing} isTyping />}
          <div ref={bottomRef} />
        </div>
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
