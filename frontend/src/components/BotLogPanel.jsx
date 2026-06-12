import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { K } from '../i18n/keys'

export default function BotLogPanel({ groupId, onClose }) {
  const { t } = useTranslation()
  const [sessions, setSessions] = useState([])
  const [selectedSessionId, setSelectedSessionId] = useState(null)
  const [sessionDetail, setSessionDetail] = useState(null)
  const [events, setEvents] = useState([])
  const [loadingList, setLoadingList] = useState(false)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [activeTab, setActiveTab] = useState('messages') // 'messages' | 'events'
  const [expandedItems, setExpandedItems] = useState(new Set()) // for collapsing tool results
  const pollIntervalRef = useRef(null)
  const debounceTimerRef = useRef(null)
  const selectedSessionIdRef = useRef(null)
  selectedSessionIdRef.current = selectedSessionId

  // Fetch recent sessions
  const fetchSessionsList = async (showLoading = true) => {
    if (showLoading) setLoadingList(true)
    try {
      const res = await fetch(`/api/sessions/group/${groupId}`)
      if (res.ok) {
        const data = await res.json()
        setSessions(data)
        // Auto-select the first running session if none is selected yet
        if (!selectedSessionId) {
          const running = data.find(s => s.status === 'running')
          if (running) {
            setSelectedSessionId(running.id)
            fetchSessionData(running.id, showLoading)
          }
        }
      }
    } catch (e) {
      console.error('Failed to fetch sessions:', e)
    } finally {
      if (showLoading) setLoadingList(false)
    }
  }

  // Fetch single session details and events
  const fetchSessionData = async (sid, showLoading = true) => {
    if (showLoading) setLoadingDetail(true)
    try {
      const [resDetail, resEvents] = await Promise.all([
        fetch(`/api/sessions/${sid}?group_id=${groupId}`),
        fetch(`/api/sessions/${sid}/events?group_id=${groupId}`)
      ])
      if (resDetail.ok) {
        const detail = await resDetail.json()
        setSessionDetail(detail)
      }
      if (resEvents.ok) {
        const evs = await resEvents.json()
        setEvents(evs)
      }
    } catch (e) {
      console.error('Failed to fetch session detail:', e)
    } finally {
      if (showLoading) setLoadingDetail(false)
    }
  }

  // Reload the current open session / list
  const handleRefresh = () => {
    if (selectedSessionId) {
      fetchSessionData(selectedSessionId)
    } else {
      fetchSessionsList()
    }
  }

  // Handle panel open & group change
  useEffect(() => {
    fetchSessionsList()
    setSelectedSessionId(null)
    setSessionDetail(null)
    setEvents([])
  }, [groupId])

  // Poll current session or session list if any session is 'running'
  useEffect(() => {
    const hasRunningSession = sessions.some(s => s.status === 'running') || (sessionDetail && sessionDetail.status === 'running')

    if (hasRunningSession) {
      pollIntervalRef.current = setInterval(() => {
        fetchSessionsList(false)
        if (selectedSessionId) {
          fetchSessionData(selectedSessionId, false)
        }
      }, 3000)
    } else {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current)
    }

    return () => {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current)
    }
  }, [sessions, sessionDetail, selectedSessionId])

  // Listen to WebSocket events in real-time for instant update
  useEffect(() => {
    const triggerDebouncedRefresh = (targetSessionId) => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current)
      }
      debounceTimerRef.current = setTimeout(() => {
        fetchSessionsList(false)
        if (selectedSessionIdRef.current && (!targetSessionId || targetSessionId === selectedSessionIdRef.current)) {
          fetchSessionData(selectedSessionIdRef.current, false)
        }
      }, 250)
    }

    const handleWsEvent = (e) => {
      const data = e.detail

      // 1. Handle session updated event from database writes
      if (data.type === 'session_updated') {
        triggerDebouncedRefresh(data.session_id)
        return
      }

      // 2. Handle stream start synchronously
      if (data.type === 'stream_start') {
        const isNew = data.session_id && data.session_id !== selectedSessionIdRef.current
        if (isNew) {
          selectedSessionIdRef.current = data.session_id
          setSelectedSessionId(data.session_id)
        }

        // Immediately set/update session detail placeholder so subsequent stream_chunks are not dropped
        setSessionDetail(prev => {
          if (prev && prev.id === data.session_id) {
            return {
              ...prev,
              status: 'running'
            }
          }
          return {
            id: data.session_id,
            bot_id: data.member_id,
            bot_name: data.sender_name,
            bot_avatar_color: data.avatar_color,
            status: 'running',
            last_snapshot: []
          }
        })

        setEvents(prev => {
          // Prevent duplicates
          if (prev.some(evt => evt.id === `start-${data.session_id}`)) return prev
          return [
            {
              id: `start-${data.session_id}`,
              event_type: 'session_start',
              payload: { user_content: '' },
              created_at: new Date().toISOString()
            }
          ]
        })

        fetchSessionData(data.session_id, false)
        fetchSessionsList(false)
        return
      }

      // 3. Handle stream chunks in real-time
      if (data.type === 'stream_chunk') {
        const activeSid = selectedSessionIdRef.current
        if (activeSid && data.session_id === activeSid) {
          setSessionDetail(prev => {
            const currentDetail = prev && prev.id === data.session_id ? prev : {
              id: data.session_id,
              status: 'running',
              last_snapshot: []
            }
            const snapshot = [...(currentDetail.last_snapshot || [])]
            const lastMsg = snapshot[snapshot.length - 1]
            if (lastMsg && lastMsg.role === 'assistant' && lastMsg.streaming) {
              snapshot[snapshot.length - 1] = {
                ...lastMsg,
                content: (lastMsg.content || '') + data.delta
              }
            } else {
              snapshot.push({
                role: 'assistant',
                content: data.delta,
                streaming: true
              })
            }
            return { ...currentDetail, last_snapshot: snapshot }
          })
        }
        return
      }

      // 4. Handle stream end
      if (data.type === 'stream_end') {
        const activeSid = selectedSessionIdRef.current
        if (activeSid && data.session_id === activeSid) {
          setSessionDetail(prev => {
            if (!prev || prev.id !== data.session_id) return prev
            const snapshot = [...(prev.last_snapshot || [])]
            const lastMsg = snapshot[snapshot.length - 1]
            if (lastMsg && lastMsg.role === 'assistant') {
              snapshot[snapshot.length - 1] = {
                ...lastMsg,
                streaming: false
              }
            }
            return { ...prev, status: 'completed', last_snapshot: snapshot }
          })
          triggerDebouncedRefresh(data.session_id)
        }
        return
      }

      // 5. Handle tool call in real-time
      if (data.type === 'tool_call') {
        const activeSid = selectedSessionIdRef.current
        if (activeSid && data.session_id === activeSid) {
          setEvents(prev => {
            const tempId = `temp-call-${data.temp_id}-${data.tool}`
            if (prev.some(evt => evt.id === tempId || evt.payload?.tool_call_id === data.tool_call_id)) {
              return prev
            }
            return [
              ...prev,
              {
                id: tempId,
                event_type: 'tool_call',
                payload: {
                  tool_name: data.tool,
                  arguments: data.args
                },
                created_at: new Date().toISOString()
              }
            ]
          })
        }
        return
      }

      // 6. Handle tool result in real-time
      if (data.type === 'tool_result') {
        const activeSid = selectedSessionIdRef.current
        if (activeSid && data.session_id === activeSid) {
          setEvents(prev => {
            const tempId = `temp-res-${data.temp_id}-${data.tool}`
            if (prev.some(evt => evt.id === tempId || evt.payload?.tool_call_id === data.tool_call_id)) {
              return prev
            }
            return [
              ...prev,
              {
                id: tempId,
                event_type: 'tool_result',
                payload: {
                  tool_name: data.tool,
                  result: data.result,
                  is_error: data.is_error || false
                },
                created_at: new Date().toISOString()
              }
            ]
          })
        }
        return
      }

      // 7. Handle compaction in real-time
      if (data.type === 'compaction') {
        const activeSid = selectedSessionIdRef.current
        if (activeSid && data.session_id === activeSid) {
          setEvents(prev => {
            const compId = `compaction-${Date.now()}`
            return [
              ...prev,
              {
                id: compId,
                event_type: 'compaction',
                payload: {
                  strategy: data.strategy,
                  message: data.message
                },
                created_at: new Date().toISOString()
              }
            ]
          })
        }
        return
      }

      // 8. Other fallback events that trigger debounced/instant refresh
      const relevantTypes = ['skills_loaded', 'recovery_prompt']
      if (relevantTypes.includes(data.type)) {
        if (data.session_id && data.session_id !== selectedSessionIdRef.current) {
          selectedSessionIdRef.current = data.session_id
          setSelectedSessionId(data.session_id)
          fetchSessionData(data.session_id, false)
          fetchSessionsList(false)
        } else {
          triggerDebouncedRefresh(data.session_id || selectedSessionIdRef.current)
        }
      }
    }

    window.addEventListener('ws_bot_event', handleWsEvent)
    return () => {
      window.removeEventListener('ws_bot_event', handleWsEvent)
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current)
      }
    }
  }, [groupId])

  const toggleExpand = (id) => {
    setExpandedItems(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const formatTime = (timeStr) => {
    if (!timeStr) return ''
    return new Date(timeStr).toLocaleString('zh-CN', {
      month: 'numeric',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    })
  }

  const getStatusBadge = (status) => {
    switch (status) {
      case 'running':
        return <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-green-500/20 text-green-400 border border-green-500/30 animate-pulse">{t(K.botLog.statusRunning)}</span>
      case 'finished':
      case 'completed':
        return <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-indigo-500/20 text-indigo-400 border border-indigo-500/30">{t(K.botLog.statusCompleted)}</span>
      case 'failed':
        return <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-red-500/20 text-red-400 border border-red-500/30">{t(K.botLog.statusFailed)}</span>
      default:
        return <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-gray-500/20 text-gray-400 border border-gray-500/30">{status}</span>
    }
  }

  return (
    <div className="w-full md:w-[480px] flex-shrink-0 bg-gray-900 border-l border-gray-700 flex flex-col h-full shadow-2xl relative z-20 animate-in slide-in-from-right duration-250">
      {/* Header */}
      <div className="h-14 flex items-center justify-between px-4 border-b border-gray-700 flex-shrink-0 bg-gray-900/90 backdrop-blur-md">
        <div className="flex items-center gap-2">
          {selectedSessionId && (
            <button
              onClick={() => {
                setSelectedSessionId(null)
                setSessionDetail(null)
                setEvents([])
                fetchSessionsList()
              }}
              className="p-1 rounded text-gray-400 hover:text-white hover:bg-gray-800 transition-colors"
              title={t(K.botLog.backToList)}
            >
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><line x1="19" y1="12" x2="5" y2="12"></line><polyline points="12 19 5 12 12 5"></polyline></svg>
            </button>
          )}
          <span className="text-gray-200 font-semibold text-sm">
            {selectedSessionId ? t(K.botLog.sessionDetail) : t(K.botLog.runLogs)}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <button
            onClick={handleRefresh}
            className="p-1 rounded text-gray-400 hover:text-white hover:bg-gray-800 transition-colors"
            title={t(K.botLog.refresh)}
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="23 4 23 10 17 10"></polyline><polyline points="1 20 1 14 7 14"></polyline><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path></svg>
          </button>
          <button onClick={onClose} className="p-1 rounded text-gray-400 hover:text-white hover:bg-gray-800 transition-colors" title={t(K.botLog.closePanel)}>
            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
          </button>
        </div>
      </div>

      {/* Main Content Area */}
      <div className="flex-1 overflow-y-auto min-h-0 bg-gray-950/40">
        {!selectedSessionId ? (
          /* List View */
          <div className="flex flex-col h-full">
            {loadingList && sessions.length === 0 ? (
              <div className="flex-1 flex items-center justify-center text-xs text-gray-500">{t(K.botLog.loadingList)}</div>
            ) : sessions.length === 0 ? (
              <div className="flex-1 flex flex-col items-center justify-center gap-2 p-8 text-center">
                <span className="text-3xl">📜</span>
                <p className="text-xs text-gray-500">{t(K.botLog.noSessions)}</p>
                <p className="text-[11px] text-gray-600">{t(K.botLog.noSessionsHint)}</p>
              </div>
            ) : (
              <div className="divide-y divide-gray-800">
                {sessions.map(s => (
                  <div
                    key={s.id}
                    onClick={() => {
                      setSelectedSessionId(s.id)
                      fetchSessionData(s.id)
                    }}
                    className="p-4 hover:bg-gray-800/30 transition-colors cursor-pointer flex flex-col gap-2 group"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <div
                          className="w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold text-white flex-shrink-0"
                          style={{ backgroundColor: s.bot_avatar_color }}
                        >
                          {s.bot_name?.[0] || 'B'}
                        </div>
                        <span className="text-xs font-semibold text-gray-200 group-hover:text-indigo-400 transition-colors">{s.bot_name}</span>
                        <span className="text-[10px] text-gray-600 font-mono">ID: {s.id.slice(0, 8)}</span>
                      </div>
                      {getStatusBadge(s.status)}
                    </div>
                    {s.user_message && (
                      <p className="text-xs text-gray-400 line-clamp-2 bg-gray-900/60 p-2 rounded border border-gray-800 italic">
                        "{s.user_message}"
                      </p>
                    )}
                    <div className="flex items-center justify-between text-[10px] text-gray-500 font-mono">
                      <span>{formatTime(s.created_at)}</span>
                      <div className="flex gap-2">
                        <span>Tokens: {(s.input_tokens || 0) + (s.output_tokens || 0)}</span>
                        {s.cost_usd > 0 && <span className="text-emerald-500">${s.cost_usd.toFixed(4)}</span>}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : (
          /* Detail View */
          <div className="flex flex-col h-full">
            {/* Session Card Info */}
            {sessionDetail && (
              <div className="bg-gray-900/60 border-b border-gray-800 p-4 space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div
                      className="w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold text-white"
                      style={{ backgroundColor: sessionDetail.bot_avatar_color }}
                    >
                      {sessionDetail.bot_name?.[0] || 'B'}
                    </div>
                    <div>
                      <h4 className="text-xs font-bold text-gray-200">{sessionDetail.bot_name}</h4>
                      <p className="text-[10px] text-gray-500 font-mono">{t(K.botLog.sessionId)}: {sessionDetail.id}</p>
                    </div>
                  </div>
                  {getStatusBadge(sessionDetail.status)}
                </div>
                <div className="grid grid-cols-3 gap-2 text-[10px] font-mono text-gray-400 bg-gray-900/40 p-2 rounded">
                  <div>
                    <span className="text-gray-600 block">{t(K.botLog.modelProvider)}</span>
                    <span className="text-gray-300 truncate block">{sessionDetail.config?.provider || 'default'}</span>
                  </div>
                  <div>
                    <span className="text-gray-600 block">{t(K.botLog.modelName)}</span>
                    <span className="text-gray-300 truncate block">{sessionDetail.config?.model_name || 'default'}</span>
                  </div>
                  <div>
                    <span className="text-gray-600 block">{t(K.botLog.costEstimate)}</span>
                    <span className="text-emerald-500 block">${sessionDetail.cost_usd?.toFixed(4) || '0.0000'}</span>
                  </div>
                </div>
              </div>
            )}

            {/* Tab Controls */}
            <div className="flex border-b border-gray-800 bg-gray-900/30 flex-shrink-0">
              <button
                onClick={() => setActiveTab('messages')}
                className={`flex-1 py-2.5 text-center text-xs font-medium border-b-2 transition-colors ${
                  activeTab === 'messages' ? 'text-indigo-400 border-indigo-500 bg-indigo-500/5' : 'text-gray-500 border-transparent hover:text-gray-300'
                }`}
              >
                {t(K.botLog.tabMessages)} ({sessionDetail?.last_snapshot?.length || 0})
              </button>
              <button
                onClick={() => setActiveTab('events')}
                className={`flex-1 py-2.5 text-center text-xs font-medium border-b-2 transition-colors ${
                  activeTab === 'events' ? 'text-indigo-400 border-indigo-500 bg-indigo-500/5' : 'text-gray-500 border-transparent hover:text-gray-300'
                }`}
              >
                {t(K.botLog.tabEvents)} ({events.length})
              </button>
            </div>

            {/* Tab Panels */}
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {loadingDetail && (
                <div className="text-center text-xs text-gray-500 py-12">{t(K.botLog.loadingDetail)}</div>
              )}

              {!loadingDetail && activeTab === 'messages' && (
                <div className="space-y-4">
                  {(!sessionDetail?.last_snapshot || sessionDetail.last_snapshot.length === 0) ? (
                    <div className="text-center text-xs text-gray-600 py-12">{t(K.botLog.noMessages)}</div>
                  ) : (
                    sessionDetail.last_snapshot.map((msg, idx) => {
                      const isUser = msg.role === 'user'
                      const isTool = msg.role === 'tool'
                      const isAssistant = msg.role === 'assistant'

                      let roleLabel = msg.role
                      let roleClass = 'bg-gray-800 text-gray-300 border-gray-700'
                      if (isUser) {
                        roleLabel = t(K.botLog.roleUser)
                        roleClass = 'bg-indigo-950/40 text-indigo-200 border-indigo-900/50'
                      } else if (isAssistant) {
                        roleLabel = msg.streaming ? t(K.botLog.streaming) : t(K.botLog.roleLlm)
                        roleClass = 'bg-emerald-950/20 text-emerald-300 border-emerald-900/40'
                      } else if (isTool) {
                        roleLabel = t(K.botLog.roleToolResult, { name: msg.name })
                        roleClass = 'bg-slate-900 text-slate-300 border-slate-800 font-mono text-[11px]'
                      }

                      const isExpanded = expandedItems.has(`msg-${idx}`)

                      return (
                        <div key={idx} className={`border rounded-lg overflow-hidden ${roleClass}`}>
                          {/* Label Bar */}
                          <div className="px-3 py-1.5 flex items-center justify-between border-b border-inherit bg-black/10 text-[10px] font-semibold">
                            <span>{roleLabel}</span>
                            {(isTool || (isAssistant && msg.tool_calls)) && (
                              <button
                                onClick={() => toggleExpand(`msg-${idx}`)}
                                className="text-indigo-400 hover:text-indigo-300 text-[10px]"
                              >
                                {isExpanded ? t(K.botLog.collapseBtn) : t(K.botLog.expandBtn)}
                              </button>
                            )}
                          </div>

                          {/* Content Body */}
                          <div className="p-3 text-xs leading-relaxed whitespace-pre-wrap select-text">
                            {isAssistant && msg.content && (
                              <div className="text-gray-300">{msg.content}</div>
                            )}

                            {isAssistant && msg.tool_calls && (
                              <div className="mt-2 space-y-2">
                                <p className="text-[10px] text-emerald-500 font-semibold">{t(K.botLog.toolsPending)}</p>
                                {msg.tool_calls.map((tc, tcIdx) => (
                                  <div key={tcIdx} className="bg-black/35 p-2 rounded font-mono text-[11px] text-gray-400 space-y-1">
                                    <div className="text-indigo-400 font-bold">{tc.function?.name}</div>
                                    <pre className="text-[10px] overflow-x-auto text-gray-500">
                                      {JSON.stringify(JSON.parse(tc.function?.arguments || '{}'), null, 2)}
                                    </pre>
                                  </div>
                                ))}
                              </div>
                            )}

                            {isTool && (
                              <div>
                                {isExpanded ? (
                                  <pre className="overflow-x-auto text-[10px] bg-black/30 p-2 rounded">{msg.content}</pre>
                                ) : (
                                  <div className="text-gray-500 italic">
                                    {msg.content?.slice(0, 150) || t(K.botLog.emptyCollapsed)}
                                    {msg.content?.length > 150 && t(K.botLog.collapsedSuffix)}
                                  </div>
                                )}
                              </div>
                            )}

                            {isUser && (
                              <div className="text-indigo-200">{msg.content}</div>
                            )}
                          </div>
                        </div>
                      )
                    })
                  )}
                </div>
              )}

              {!loadingDetail && activeTab === 'events' && (
                <div className="relative border-l border-gray-800 ml-2.5 pl-4 space-y-5 py-2">
                  {events.length === 0 ? (
                    <div className="text-center text-xs text-gray-600 py-12 -ml-4">{t(K.botLog.noEvents)}</div>
                  ) : (
                    events.map((ev, idx) => {
                      const isStart = ev.event_type === 'session_start'
                      const isCall = ev.event_type === 'tool_call'
                      const isResult = ev.event_type === 'tool_result'
                      const isFork = ev.event_type === 'child_fork'
                      const isJoin = ev.event_type === 'child_join'

                      let icon = '•'
                      let color = 'bg-gray-700'
                      let title = ev.event_type
                      if (isStart) { icon = '🏁'; color = 'bg-blue-600'; title = t(K.botLog.eventSessionStart) }
                      else if (isCall) { icon = '🔧'; color = 'bg-amber-600'; title = t(K.botLog.eventToolCall, { name: ev.payload?.tool_name }) }
                      else if (isResult) { icon = '✅'; color = ev.payload?.is_error ? 'bg-red-600' : 'bg-green-600'; title = t(K.botLog.eventToolResult, { name: ev.payload?.tool_name }) }
                      else if (isFork) { icon = '🔱'; color = 'bg-purple-600'; title = t(K.botLog.eventFork, { name: ev.payload?.skill_name }) }
                      else if (isJoin) { icon = '🤝'; color = 'bg-indigo-600'; title = t(K.botLog.eventJoin, { name: ev.payload?.skill_name }) }

                      const isExpanded = expandedItems.has(`ev-${ev.id}`)

                      return (
                        <div key={ev.id} className="relative group">
                          {/* Dot marker on line */}
                          <div className={`absolute -left-[22.5px] top-0.5 w-4 h-4 rounded-full flex items-center justify-center text-[9px] text-white font-bold ${color}`}>
                            {icon}
                          </div>

                          {/* Event info block */}
                          <div className="space-y-1">
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-xs font-semibold text-gray-200">{title}</span>
                              <span className="text-[9px] text-gray-600 font-mono">{formatTime(ev.created_at)}</span>
                            </div>

                            {/* Start prompt payload */}
                            {isStart && ev.payload?.user_content && (
                              <div className="bg-gray-900/60 p-2 rounded text-xs text-gray-400 border border-gray-800">
                                {ev.payload.user_content}
                              </div>
                            )}

                            {/* Tool arguments payload */}
                            {isCall && ev.payload?.arguments && (
                              <div className="bg-gray-900/60 p-2 rounded border border-gray-800 font-mono text-[10px] text-gray-500 space-y-1">
                                <div className="text-gray-600 font-semibold">{t(K.botLog.eventsArguments)}</div>
                                <pre className="overflow-x-auto text-gray-400">
                                  {JSON.stringify(ev.payload.arguments, null, 2)}
                                </pre>
                              </div>
                            )}

                            {/* Tool result payload */}
                            {isResult && ev.payload && (
                              <div className="bg-gray-900/60 p-2 rounded border border-gray-800 space-y-1 text-[11px]">
                                <div className="flex justify-between items-center">
                                  <span className={ev.payload.is_error ? 'text-red-400 font-semibold' : 'text-green-400 font-semibold'}>
                                    {ev.payload.is_error ? t(K.botLog.eventsFailed) : t(K.botLog.eventsSuccess)}
                                  </span>
                                  <button
                                    onClick={() => toggleExpand(`ev-${ev.id}`)}
                                    className="text-indigo-400 hover:text-indigo-300 text-[10px]"
                                  >
                                    {isExpanded ? t(K.botLog.collapseResult) : t(K.botLog.expandResult)}
                                  </button>
                                </div>
                                {isExpanded ? (
                                  <pre className="overflow-x-auto text-[10px] font-mono text-gray-400 bg-black/20 p-2 rounded mt-1">
                                    {ev.payload.result}
                                  </pre>
                                ) : (
                                  <p className="text-xs text-gray-500 italic font-mono truncate">
                                    {ev.payload.result || t(K.botLog.empty)}
                                  </p>
                                )}
                              </div>
                            )}

                            {/* Fork/Join details */}
                            {(isFork || isJoin) && ev.payload?.child_session_id && (
                              <div className="bg-gray-900/60 p-2 rounded border border-gray-800 text-[10px] text-gray-500 font-mono">
                                <div>{t(K.botLog.childSessionId, { id: ev.payload.child_session_id })}</div>
                                {isJoin && ev.payload.result && (
                                  <div className="mt-1">
                                    <div className="text-gray-600">{t(K.botLog.resultSummary)}</div>
                                    <div className="text-gray-400 italic line-clamp-1">{ev.payload.result}</div>
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        </div>
                      )
                    })
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
