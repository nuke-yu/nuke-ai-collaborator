import { useState } from 'react'
import { fetchGroupStats, exportGroupUrl } from '../api'

export default function ChatHeader({
  activeGroupId,
  group,
  members,
  reconnecting,
  workflow,
  onShowSearch,
  onShowWorkflowStart,
  onStartRequirement,
  onShowStats, onLogout,
  onShowBotLogs,
}) {
  const [editingGroupName, setEditingGroupName] = useState(false)
  const [groupNameDraft, setGroupNameDraft] = useState('')
  const [showExportMenu, setShowExportMenu] = useState(false)

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

  return (
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
          <>
            <button
              onClick={() => onStartRequirement?.()}
              className="text-xs px-2 py-1 rounded text-indigo-300 hover:text-white hover:bg-indigo-600/40 transition-colors"
              title="开始需求流程（BA→Dev→QA，每步人确认）"
            >📋 开始需求</button>
            <button
              onClick={() => onShowWorkflowStart()}
              className="text-sm px-2 py-1 rounded text-gray-500 hover:text-indigo-400 transition-colors"
              title="启动自定义工作流"
            >⚡</button>
          </>
        )}
        <button
          onClick={async () => { const s = await fetchGroupStats(activeGroupId); onShowStats(s); }}
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
          onClick={onLogout}
          className="text-sm px-2 py-1 rounded text-gray-500 hover:text-red-400 transition-colors"
          title="退出登录"
        >🚪</button>
        <button
          onClick={() => onShowSearch()}
          className="text-sm px-2 py-1 rounded text-gray-500 hover:text-gray-300 transition-colors"
          title="搜索消息 (⌘K)"
        >🔍</button>
        <button
          onClick={() => onShowBotLogs()}
          className="text-sm px-2 py-1 rounded text-gray-500 hover:text-gray-300 transition-colors"
          title="Bot 运行日志"
        >📜</button>
      </div>
    </div>
  )
}
