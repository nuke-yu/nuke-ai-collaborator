import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { K } from '../i18n/keys'

export default function PinnedBar({ pins, onUnpin }) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)

  if (!pins || pins.length === 0) return null

  return (
    <div className="border-b border-gray-700 bg-gray-900/80 flex-shrink-0">
      {/* 收起状态：单行摘要 */}
      <button
        onClick={() => setExpanded(e => !e)}
        className="w-full flex items-center gap-2 px-4 py-2 hover:bg-gray-800/50 transition-colors text-left"
      >
        <span className="text-sm flex-shrink-0">📌</span>
        <span className="text-xs text-gray-400 flex-shrink-0">{t(K.pinnedBar.title)} ({pins.length})</span>
        {!expanded && (
          <span className="text-xs text-gray-500 truncate flex-1 ml-1">
            {pins[0].sender_name}：{pins[0].content?.slice(0, 60) || '[文件]'}
          </span>
        )}
        <span className="text-gray-600 text-xs ml-auto flex-shrink-0">{expanded ? '▲' : '▼'}</span>
      </button>

      {/* 展开状态：完整列表 */}
      {expanded && (
        <div className="divide-y divide-gray-800 max-h-48 overflow-y-auto">
          {pins.map(msg => (
            <div key={msg.id} className="flex items-start gap-3 px-4 py-2.5 hover:bg-gray-800/30 transition-colors group">
              <div
                className="w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold text-white flex-shrink-0 mt-0.5"
                style={{ backgroundColor: msg.avatar_color }}
              >
                {msg.sender_name?.[0]}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5 mb-0.5">
                  <span className="text-xs font-medium text-gray-300">{msg.sender_name}</span>
                  {msg.sender_type === 'bot' && <span className="text-xs text-gray-600">🤖</span>}
                </div>
                <p className="text-xs text-gray-400 leading-relaxed line-clamp-2">
                  {msg.is_deleted ? t(K.message.withdrawn) : (msg.content || t(K.message.fileAttachment))}
                </p>
              </div>
              <button
                onClick={() => onUnpin(msg.id)}
                className="hidden group-hover:block text-gray-600 hover:text-red-400 text-xs flex-shrink-0 transition-colors mt-0.5"
                title={t(K.pinnedBar.unpin)}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
