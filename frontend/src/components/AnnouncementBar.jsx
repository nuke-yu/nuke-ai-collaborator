import { useState, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { K } from '../i18n/keys'

export default function AnnouncementBar({ announcement, onSave }) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const textareaRef = useRef(null)

  useEffect(() => {
    if (editing) {
      setDraft(announcement || '')
      setTimeout(() => textareaRef.current?.focus(), 0)
    }
  }, [editing])

  const handleSave = () => {
    onSave(draft.trim())
    setEditing(false)
    setExpanded(false)
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); handleSave() }
    if (e.key === 'Escape') setEditing(false)
  }

  if (editing) {
    return (
      <div className="border-b border-gray-700 bg-gray-900/80 px-4 py-2 flex-shrink-0">
        <div className="flex items-center gap-2 mb-1.5">
          <span className="text-xs text-yellow-500">📢</span>
          <span className="text-xs text-gray-400 font-medium">{t(K.announcement.editTitle)}</span>
        </div>
        <textarea
          ref={textareaRef}
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={t(K.announcement.contentPlaceholder)}
          rows={3}
          className="w-full bg-gray-800 text-gray-100 text-xs rounded-lg px-3 py-2 resize-none outline-none focus:ring-1 focus:ring-yellow-500/50 placeholder-gray-600"
        />
        <div className="flex gap-2 mt-1.5 justify-end">
          <button onClick={() => setEditing(false)} className="text-xs text-gray-500 hover:text-gray-300">{t(K.announcement.cancel)}</button>
          <button onClick={handleSave} className="text-xs text-yellow-500 hover:text-yellow-400">{t(K.announcement.save)}</button>
        </div>
      </div>
    )
  }

  if (!announcement) {
    return (
      <div className="border-b border-gray-700 flex-shrink-0">
        <button
          onClick={() => setEditing(true)}
          className="w-full flex items-center gap-2 px-4 py-1.5 hover:bg-gray-800/40 transition-colors text-left"
        >
          <span className="text-xs">📢</span>
          <span className="text-xs text-gray-600 hover:text-gray-500">{t(K.announcement.addPlaceholder)}</span>
        </button>
      </div>
    )
  }

  return (
    <div className="border-b border-gray-700 bg-gray-900/80 flex-shrink-0">
      <button
        onClick={() => setExpanded(e => !e)}
        className="w-full flex items-start gap-2 px-4 py-2 hover:bg-gray-800/50 transition-colors text-left"
      >
        <span className="text-sm flex-shrink-0 mt-px">📢</span>
        <span className={`text-xs text-gray-300 flex-1 leading-relaxed ${expanded ? '' : 'line-clamp-1'}`}>
          {announcement}
        </span>
        <span className="text-gray-600 text-xs flex-shrink-0 mt-px">{expanded ? '▲' : '▼'}</span>
      </button>
      {expanded && (
        <div className="px-4 pb-2 flex justify-end">
          <button
            onClick={() => setEditing(true)}
            className="text-xs text-gray-600 hover:text-yellow-500 transition-colors"
          >{t(K.announcement.edit)}</button>
        </div>
      )}
    </div>
  )
}
