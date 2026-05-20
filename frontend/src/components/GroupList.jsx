import { useState, useEffect } from 'react'
import AutoReplyModal from './AutoReplyModal'

export default function GroupList({
  groups, activeGroupId, unreadCounts = {},
  onSelect, onCreateGroup, onOpenTemplates, onOpenApiKeys,
  membersCache = {}, onOpenAddMember, onRemoveMember, onEditMember,
  isDark = true, onToggleTheme,
  onlineSet = new Set(),
  currentMemberId,
  className = '',
}) {
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [expandedGroups, setExpandedGroups] = useState(new Set())
  const [autoReplyTarget, setAutoReplyTarget] = useState(null)

  useEffect(() => {
    if (activeGroupId) setExpandedGroups(prev => new Set([...prev, activeGroupId]))
  }, [activeGroupId])

  const handleCreate = async () => {
    if (!newName.trim()) return
    await onCreateGroup(newName.trim())
    setNewName('')
    setCreating(false)
  }

  return (
    <>
    <div className={`w-full md:w-56 bg-gray-900 text-white flex flex-col flex-shrink-0 ${className}`}>
      <div className="p-4 border-b border-gray-800 flex items-center justify-between flex-shrink-0">
        <h1 className="text-white font-bold text-sm">Nuke AI Collaborator</h1>
        <div className="flex items-center gap-1">
          <button onClick={onToggleTheme} className="text-gray-500 hover:text-white text-xs transition-colors" title={isDark ? '切换亮色模式' : '切换暗色模式'}>{isDark ? '☀️' : '🌙'}</button>
          <button onClick={onOpenApiKeys} className="text-gray-500 hover:text-white text-xs transition-colors" title="API Key 管理">🔑</button>
          <button onClick={onOpenTemplates} className="text-gray-500 hover:text-white text-xs transition-colors" title="角色模板">⚙️</button>
        </div>
      </div>

      <div className="flex items-center justify-between px-3 pt-3 pb-1 flex-shrink-0">
        <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">群组</span>
        <button onClick={() => setCreating(true)} className="text-gray-500 hover:text-white text-lg leading-none transition-colors" title="新建群组">+</button>
      </div>

      {creating && (
        <div className="px-2 pb-2 flex-shrink-0">
          <input
            className="w-full bg-gray-800 text-white text-sm rounded px-2 py-1.5 outline-none focus:ring-1 focus:ring-indigo-500 placeholder-gray-500"
            placeholder="群组名称"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleCreate()
              if (e.key === 'Escape') { setCreating(false); setNewName('') }
            }}
            autoFocus
          />
          <div className="flex gap-1 mt-1">
            <button onClick={handleCreate} className="flex-1 bg-indigo-600 hover:bg-indigo-500 text-white text-xs rounded py-1 transition-colors">创建</button>
            <button onClick={() => { setCreating(false); setNewName('') }} className="flex-1 bg-gray-700 hover:bg-gray-600 text-gray-300 text-xs rounded py-1 transition-colors">取消</button>
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-2 space-y-0.5">
        {groups.map((g) => (
          <div key={g.id}>
            <button
              onClick={() => {
                if (activeGroupId !== g.id) onSelect(g.id)
                setExpandedGroups(prev => {
                  const next = new Set(prev)
                  next.has(g.id) ? next.delete(g.id) : next.add(g.id)
                  return next
                })
              }}
              className={`w-full text-left flex items-center gap-2 px-2 py-1.5 rounded text-sm transition-colors ${
                activeGroupId === g.id
                  ? 'bg-indigo-600 text-white'
                  : 'text-gray-400 hover:bg-gray-800 hover:text-white'
              }`}
            >
              <span className="text-gray-500">#</span>
              <span className="truncate flex-1">{g.name}</span>
              <span className="text-gray-400 text-sm font-bold flex-shrink-0">({g.member_count ?? 0})</span>
              {unreadCounts[g.id] > 0 && activeGroupId !== g.id && (
                <span className="ml-auto bg-indigo-500 text-white text-xs font-bold rounded-full px-1.5 py-0.5 min-w-[18px] text-center leading-tight">
                  {unreadCounts[g.id] > 99 ? '99+' : unreadCounts[g.id]}
                </span>
              )}
            </button>

            {expandedGroups.has(g.id) && (
              <div className="mt-0.5 mb-1">
                {(membersCache[g.id] || []).map((m) => (
                  <div key={m.id} className="group flex items-center gap-1.5 pl-5 pr-2 py-0.5 rounded hover:bg-gray-800/50 transition-colors">
                    <div className="relative flex-shrink-0">
                      <div className="w-3.5 h-3.5 rounded-full flex items-center justify-center text-xs font-bold text-white" style={{ backgroundColor: m.avatar_color }}>
                        {m.name[0]}
                      </div>
                      {onlineSet.has(m.id) && <span className="absolute -bottom-px -right-px w-1.5 h-1.5 bg-green-400 rounded-full border border-gray-900" />}
                    </div>
                    <span className={`text-xs truncate flex-1 ${onlineSet.has(m.id) ? 'text-gray-300' : 'text-gray-500'}`}>{m.name}</span>
                    {m.type === 'bot' && (
                      <>
                        <span className="group-hover:hidden text-xs text-gray-600 flex-shrink-0">🤖</span>
                        <button
                          onClick={() => { if (activeGroupId !== g.id) onSelect(g.id); onEditMember?.(m) }}
                          className="hidden group-hover:flex items-center justify-center w-5 h-5 rounded hover:bg-indigo-500/20 text-gray-500 hover:text-indigo-400 flex-shrink-0 transition-colors"
                          title="编辑 Bot"
                        >
                          <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                        </button>
                      </>
                    )}
                    {m.id === currentMemberId && (
                      <>
                        {m.auto_reply && <span className="group-hover:hidden text-xs text-indigo-600 flex-shrink-0" title="已开启自动回复">↩</span>}
                        <button
                          onClick={() => setAutoReplyTarget(m)}
                          className={`hidden group-hover:block text-xs flex-shrink-0 transition-colors ${m.auto_reply ? 'text-indigo-400 hover:text-indigo-300' : 'text-gray-700 hover:text-indigo-400'}`}
                          title="设置自动回复"
                        >↩</button>
                      </>
                    )}
                    {m.id !== currentMemberId && m.type !== 'bot' && (
                      <button
                        onClick={() => onRemoveMember(m.id)}
                        className="hidden group-hover:block text-gray-700 hover:text-red-400 text-xs flex-shrink-0 transition-colors"
                      >✕</button>
                    )}
                    {m.id !== currentMemberId && m.type === 'bot' && (
                      <button
                        onClick={() => onRemoveMember(m.id)}
                        className="hidden group-hover:block text-gray-700 hover:text-red-400 text-xs flex-shrink-0 transition-colors ml-0.5"
                      >✕</button>
                    )}
                  </div>
                ))}
                <button
                  onClick={() => { if (activeGroupId !== g.id) onSelect(g.id); onOpenAddMember() }}
                  className="flex items-center gap-1.5 pl-5 pr-2 py-0.5 w-full text-left text-xs text-gray-700 hover:text-gray-500 transition-colors"
                >
                  <span>+</span>
                  <span>添加成员</span>
                </button>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
    {autoReplyTarget && (
      <AutoReplyModal
        member={autoReplyTarget}
        onClose={() => setAutoReplyTarget(null)}
        onSaved={(text) => {
          autoReplyTarget.auto_reply = text
          setAutoReplyTarget(null)
        }}
      />
    )}
    </>
  )
}
