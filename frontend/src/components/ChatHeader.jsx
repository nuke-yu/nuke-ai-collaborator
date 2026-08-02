import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { fetchGroupStats, exportGroupUrl } from '../api'
import { K } from '../i18n/keys'

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
  onShowTimeline,
}) {
  const { t } = useTranslation()
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
          placeholder={t(K.chat.header.renamePlaceholder)}
          className="bg-gray-800 text-gray-100 font-semibold text-sm rounded px-2 py-0.5 outline-none focus:ring-1 focus:ring-indigo-500 w-40"
        />
      ) : (
        <button
          onClick={() => { setGroupNameDraft(group?.name || ''); setEditingGroupName(true) }}
          className="text-gray-300 font-semibold hover:text-white transition-colors"
          title={t(K.chat.header.renameTitle)}
        >
          # {group?.name || t(K.chat.header.selectGroup)}
        </button>
      )}
      <span className="text-xs text-gray-500 ml-2">· {t(K.chat.header.members, { count: members.length })}</span>
      {reconnecting && (
        <span className="text-xs text-yellow-400 animate-pulse">⚠ {t(K.chat.header.reconnecting)}</span>
      )}
      <div className="ml-auto flex items-center gap-1">
        {members.some(m => m.type === 'bot') && !workflow?.active && (
          <button
            onClick={() => onShowWorkflowStart()}
            className="text-xs px-2 py-1 rounded text-indigo-300 hover:text-white hover:bg-indigo-600/40 transition-colors"
            title={t(K.chat.header.startWorkflowTitle)}
          >⚡ {t(K.workflow.titleLabel) || '工作流'}</button>
        )}
        <button
          onClick={async () => { const s = await fetchGroupStats(activeGroupId); onShowStats(s); }}
          className="text-sm px-2 py-1 rounded text-gray-500 hover:text-gray-300 transition-colors"
          title={t(K.chat.header.statsTitle)}
        >📊</button>
        <div className="relative">
          <button
            onClick={() => setShowExportMenu(m => !m)}
            className={`text-sm px-2 py-1 rounded transition-colors ${showExportMenu ? 'text-indigo-400 bg-indigo-950/50' : 'text-gray-500 hover:text-gray-300'}`}
            title={t(K.chat.header.exportTitle)}
          >⬇️</button>
          {showExportMenu && (
            <div className="absolute right-0 top-full mt-1 bg-gray-800 border border-gray-700 rounded-lg shadow-xl overflow-hidden z-50 w-36">
              <a href={exportGroupUrl(activeGroupId, 'markdown')} download onClick={() => setShowExportMenu(false)}
                className="flex items-center gap-2 px-3 py-2 text-xs text-gray-300 hover:bg-gray-700 transition-colors no-underline">
                <span>📝</span> {t(K.chat.header.exportMarkdown)}
              </a>
              <a href={exportGroupUrl(activeGroupId, 'json')} download onClick={() => setShowExportMenu(false)}
                className="flex items-center gap-2 px-3 py-2 text-xs text-gray-300 hover:bg-gray-700 transition-colors no-underline">
                <span>📋</span> {t(K.chat.header.exportJson)}
              </a>
            </div>
          )}
        </div>
        <button
          onClick={onLogout}
          className="text-sm px-2 py-1 rounded text-gray-500 hover:text-red-400 transition-colors"
          title={t(K.chat.header.logout)}
        >🚪</button>
        <button
          onClick={() => onShowSearch()}
          className="text-sm px-2 py-1 rounded text-gray-500 hover:text-gray-300 transition-colors"
          title={`${t(K.chat.header.search)} (⌘K)`}
        >🔍</button>
        <button
          onClick={() => onShowBotLogs()}
          className="text-sm px-2 py-1 rounded text-gray-500 hover:text-gray-300 transition-colors"
          title={t(K.chat.header.botLogsTitle)}
        >📜</button>
        <button
          onClick={() => onShowTimeline()}
          className="text-sm px-2 py-1 rounded text-gray-500 hover:text-gray-300 transition-colors"
          title="业务时间线"
        >🧭</button>
      </div>
    </div>
  )
}
