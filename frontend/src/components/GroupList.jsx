import { useState, useEffect } from 'react'
import AutoReplyModal from './AutoReplyModal'
import { useGroupStore } from '../store/groupStore'
import { useChatStore } from '../store/chatStore'

const THEMES = [
  { id: 'default-dark', name: '默认暗黑', icon: '🌙' },
  { id: 'elegant-light', name: '极简明亮', icon: '☀️' },
  { id: 'elevenlabs', name: 'ElevenLabs', icon: '🎙️' },
  { id: 'hsbc', name: 'HSBC 商务', icon: '🏦' },
  { id: 'cyberpunk', name: '赛博霓虹', icon: '👾' },
  { id: 'glass', name: '毛玻璃幻彩', icon: '🔮' }
]

export default function GroupList({
  onSelect, onCreateGroup, onDeleteGroup, onOpenTemplates, onOpenApiKeys,
  membersCache = {}, onOpenAddMember, onRemoveMember, onEditMember, onOpenWorkspace,
  theme = 'default-dark', onThemeChange,
  currentMemberId,
  onAutoReplySaved,
  skillDraftBots = new Set(),
  className = '',
}) {
  const groups = useGroupStore((s) => s.groups)
  const activeGroupId = useGroupStore((s) => s.activeGroupId)
  const unreadCounts = useGroupStore((s) => s.unreadCounts)
  const onlineSet = useChatStore((s) => s.onlineSet)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [expandedGroups, setExpandedGroups] = useState(new Set())
  const [autoReplyTarget, setAutoReplyTarget] = useState(null)
  const [showThemeMenu, setShowThemeMenu] = useState(false)
  const [confirmDeleteGroupId, setConfirmDeleteGroupId] = useState(null)

  useEffect(() => {
    if (activeGroupId) setExpandedGroups(prev => new Set([...prev, activeGroupId]))
  }, [activeGroupId])

  useEffect(() => {
    if (!showThemeMenu) return
    const handleClose = () => setShowThemeMenu(false)
    window.addEventListener('click', handleClose)
    return () => window.removeEventListener('click', handleClose)
  }, [showThemeMenu])

  const handleCreate = async () => {
    if (!newName.trim()) return
    await onCreateGroup(newName.trim())
    setNewName('')
    setCreating(false)
  }

  return (
    <>
    <div className={`w-full md:w-56 bg-gray-900 text-white flex flex-col flex-shrink-0 ${className}`}>
      <div className="p-4 border-b border-gray-800 flex-shrink-0">
        <h1 className="text-white font-bold text-sm mb-2">Nuke AI Collaborator</h1>
        <div className="flex items-center gap-2">
          <div className="relative">
            <button
              onClick={(e) => { e.stopPropagation(); setShowThemeMenu(prev => !prev) }}
              className="text-gray-500 hover:text-white text-xs transition-colors flex items-center gap-0.5"
              title="选择主题皮肤"
            >
              🎨 {THEMES.find(t => t.id === theme)?.name || '主题'}
            </button>
            {showThemeMenu && (
              <div className="absolute left-0 top-full mt-1.5 bg-gray-800 border border-gray-700 rounded-lg shadow-xl overflow-hidden z-50 w-36 py-1 animate-scale-up">
                {THEMES.map((t) => (
                  <button
                    key={t.id}
                    onClick={() => {
                      onThemeChange(t.id)
                      setShowThemeMenu(false)
                    }}
                    className={`w-full text-left px-3 py-1.5 text-xs flex items-center gap-1.5 transition-colors ${
                      theme === t.id
                        ? 'bg-indigo-600/30 text-indigo-400 font-semibold'
                        : 'text-gray-300 hover:bg-gray-700 hover:text-white'
                    }`}
                  >
                    <span>{t.icon}</span>
                    <span>{t.name}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <span className="text-gray-700 select-none">|</span>
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
            <div className={`group/grp flex items-center gap-1 rounded text-sm transition-colors ${
              activeGroupId === g.id ? 'bg-indigo-600' : 'hover:bg-gray-800'
            }`}>
              <button
                onClick={() => {
                  if (activeGroupId !== g.id) onSelect(g.id)
                  setExpandedGroups(prev => {
                    const next = new Set(prev)
                    next.has(g.id) ? next.delete(g.id) : next.add(g.id)
                    return next
                  })
                }}
                className={`flex-1 text-left flex items-center gap-2 px-2 py-1.5 min-w-0 ${
                  activeGroupId === g.id ? 'text-white' : 'text-gray-400 hover:text-white'
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
              {confirmDeleteGroupId === g.id ? (
                <div className="flex items-center gap-1 pr-1 flex-shrink-0">
                  <button onClick={() => { onDeleteGroup?.(g.id); setConfirmDeleteGroupId(null) }}
                    className="text-xs bg-red-600 hover:bg-red-500 text-white px-1.5 py-0.5 rounded transition-colors">删除</button>
                  <button onClick={() => setConfirmDeleteGroupId(null)}
                    className="text-xs text-gray-400 hover:text-white px-1 py-0.5 rounded transition-colors">取消</button>
                </div>
              ) : (
                <button
                  onClick={(e) => { e.stopPropagation(); setConfirmDeleteGroupId(g.id) }}
                  className="hidden group-hover/grp:flex items-center justify-center w-5 h-5 mr-1 rounded hover:bg-red-500/20 text-gray-600 hover:text-red-400 flex-shrink-0 transition-colors"
                  title="删除群组"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
                </button>
              )}
            </div>

            {expandedGroups.has(g.id) && (
              <div className="mt-0.5 mb-1">
                {(membersCache[g.id] || []).map((m) => (
                  <div key={m.id} className="group flex items-center gap-1.5 pl-5 pr-2 py-0.5 rounded hover:bg-gray-800/50 transition-colors">
                    <div className="relative flex-shrink-0">
                      <div className="w-3.5 h-3.5 rounded-full flex items-center justify-center text-xs font-bold text-white" style={{ backgroundColor: m.avatar_color }}>
                        {m.name[0]}
                      </div>
                      {(onlineSet.has(m.id) || m.type === 'bot') && <span className="absolute -bottom-px -right-px w-1.5 h-1.5 bg-green-400 rounded-full border border-gray-900" />}
                    </div>
                    <span className={`text-xs truncate flex-1 ${(onlineSet.has(m.id) || m.type === 'bot') ? 'text-gray-300' : 'text-gray-500'}`}>{m.name}</span>
                    {m.type === 'bot' && (
                      <>
                        {skillDraftBots.has(m.id)
                          ? <span className="group-hover:hidden text-xs text-yellow-400 flex-shrink-0" title="有待审批的 Skill">⚡</span>
                          : <span className="group-hover:hidden text-xs text-gray-600 flex-shrink-0">🤖</span>
                        }
                        <button
                          onClick={() => { if (activeGroupId !== g.id) onSelect(g.id); onEditMember?.(m) }}
                          className="hidden group-hover:flex items-center justify-center w-5 h-5 rounded hover:bg-indigo-500/20 text-gray-500 hover:text-indigo-400 flex-shrink-0 transition-colors"
                          title="编辑 Bot"
                        >
                          <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                        </button>
                        <button
                          onClick={() => { if (activeGroupId !== g.id) onSelect(g.id); onOpenWorkspace?.(m) }}
                          className="hidden group-hover:flex items-center justify-center w-5 h-5 rounded hover:bg-emerald-500/20 text-gray-500 hover:text-emerald-400 flex-shrink-0 transition-colors relative"
                          title={skillDraftBots.has(m.id) ? '有待审批的 Skill' : '工作区文件'}
                        >
                          <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M3 3h18v18H3z"/><path d="M3 9h18M9 21V9"/></svg>
                          {skillDraftBots.has(m.id) && (
                            <span className="absolute -top-0.5 -right-0.5 w-2 h-2 bg-yellow-400 rounded-full" />
                          )}
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
          onAutoReplySaved?.(autoReplyTarget.id, text)
          setAutoReplyTarget(null)
        }}
      />
    )}
    </>
  )
}
