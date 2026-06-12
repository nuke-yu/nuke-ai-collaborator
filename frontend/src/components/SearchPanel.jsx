import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { searchMessages } from '../api'
import { K } from '../i18n/keys'

function highlight(text, keyword) {
  if (!keyword.trim()) return text
  const parts = text.split(new RegExp(`(${keyword.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi'))
  return parts.map((part, i) =>
    part.toLowerCase() === keyword.toLowerCase()
      ? <mark key={i} className="bg-yellow-400/30 text-yellow-200 rounded px-0.5">{part}</mark>
      : part
  )
}

function snippet(content, keyword, radius = 60) {
  const idx = content.toLowerCase().indexOf(keyword.toLowerCase())
  if (idx === -1) return content.slice(0, radius * 2)
  const start = Math.max(0, idx - radius)
  const end = Math.min(content.length, idx + keyword.length + radius)
  return (start > 0 ? '...' : '') + content.slice(start, end) + (end < content.length ? '...' : '')
}

export default function SearchPanel({ groupId, onClose, onJump }) {
  const { t } = useTranslation()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [loading, setLoading] = useState(false)
  const timerRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => { inputRef.current?.focus() }, [])

  useEffect(() => {
    setQuery('')
    setResults([])
    setLoading(false)
  }, [groupId])

  useEffect(() => {
    let active = true
    clearTimeout(timerRef.current)
    if (!query.trim()) {
      setResults([])
      setLoading(false)
      return
    }
    setLoading(true)
    timerRef.current = setTimeout(async () => {
      try {
        const data = await searchMessages(groupId, query.trim())
        if (active) {
          setResults(data)
          setLoading(false)
        }
      } catch (err) {
        if (active) {
          setLoading(false)
        }
      }
    }, 300)
    return () => {
      active = false
      clearTimeout(timerRef.current)
    }
  }, [query, groupId])

  return (
    <div className="w-full md:w-80 flex-shrink-0 bg-gray-900 border-l border-gray-700 flex flex-col">
      {/* 标题栏 */}
      <div className="h-14 flex items-center justify-between px-4 border-b border-gray-700 flex-shrink-0">
        <span className="text-gray-200 font-semibold text-sm">{t(K.search.title)}</span>
        <button onClick={onClose} title={t(K.search.close)} className="text-gray-500 hover:text-gray-300 text-lg leading-none">✕</button>
      </div>

      {/* 搜索框 */}
      <div className="px-4 py-3 border-b border-gray-700">
        <input
          ref={inputRef}
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder={t(K.search.placeholder)}
          className="w-full bg-gray-800 text-gray-100 text-sm rounded-lg px-3 py-2 outline-none focus:ring-1 focus:ring-indigo-500 placeholder-gray-500"
        />
      </div>

      {/* 结果列表 */}
      <div className="flex-1 overflow-y-auto">
        {loading && (
          <div className="text-center text-xs text-gray-500 py-6">{t(K.search.searching)}</div>
        )}
        {!loading && query && results.length === 0 && (
          <div className="text-center text-xs text-gray-500 py-6">{t(K.search.noResults)}</div>
        )}
        {!loading && !query && (
          <div className="text-center text-xs text-gray-600 py-6">{t(K.search.typeToSearch)}</div>
        )}
        {results.map(msg => (
          <div key={msg.id} onClick={() => onJump?.(msg.id)} className="px-4 py-3 border-b border-gray-800 hover:bg-gray-800/50 transition-colors cursor-pointer">
            <div className="flex items-center gap-2 mb-1">
              <div
                className="w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold text-white flex-shrink-0"
                style={{ backgroundColor: msg.avatar_color }}
              >
                {msg.sender_name[0]}
              </div>
              <span className="text-xs font-medium text-gray-300">{msg.sender_name}</span>
              {msg.sender_type === 'bot' && <span className="text-xs text-gray-600">🤖</span>}
              <span className="text-xs text-gray-600 ml-auto">
                {new Date(msg.created_at).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
              </span>
            </div>
            <p className="text-xs text-gray-400 leading-relaxed">
              {highlight(snippet(msg.content, query), query)}
            </p>
          </div>
        ))}
        {results.length > 0 && (
          <div className="text-center text-xs text-gray-600 py-2">{t(K.search.results, { count: results.length })}</div>
        )}
      </div>
    </div>
  )
}
