import { useState, useRef, useEffect, forwardRef, useImperativeHandle } from 'react'
import { useTranslation } from 'react-i18next'
import { uploadFile } from '../api'
import EmojiPicker from './EmojiPicker'
import { K } from '../i18n/keys'

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

const MessageInput = forwardRef(function MessageInput({ onSend, members = [], disabled = false, replyingTo = null, onCancelReply, groupId, defaultValue = '', onDraftSave }, ref) {
  const { t } = useTranslation()
  const [text, setText] = useState(defaultValue)
  const [mention, setMention] = useState(null)
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [sentHistory, setSentHistory] = useState([])
  const [historyIndex, setHistoryIndex] = useState(-1)
  const [draft, setDraft] = useState('')
  const [pendingFile, setPendingFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState(null)
  const [showEmojiPicker, setShowEmojiPicker] = useState(false)
  const textareaRef = useRef(null)
  const fileInputRef = useRef(null)
  const textRef = useRef(text)

  useEffect(() => { textRef.current = text }, [text])

  useImperativeHandle(ref, () => ({
    uploadFile: handleUpload,
    setInputText: (val) => {
      setText(prev => prev ? `${prev}\n${val}` : val)
      setTimeout(() => {
        textareaRef.current?.focus()
      }, 0)
    }
  }))

  // 同步重置输入框内容（切换群组加载新草稿）
  useEffect(() => {
    setText(defaultValue)
  }, [groupId, defaultValue])

  // 切换群组/卸载组件时，保存当前群组的草稿
  useEffect(() => {
    return () => {
      onDraftSave?.(groupId, textRef.current)
    }
  }, [groupId, onDraftSave])

  useEffect(() => {
    if (replyingTo) textareaRef.current?.focus()
  }, [replyingTo])

  const handleChange = (e) => {
    const val = e.target.value
    setText(val)
    const cursor = e.target.selectionStart
    const before = val.slice(0, cursor)
    const match = before.match(/@(\S*)$/)
    if (match) {
      setMention({ query: match[1], start: before.lastIndexOf('@') })
      setSelectedIndex(0)
    } else {
      setMention(null)
    }
  }

  const handleSelect = (name) => {
    if (!mention) return
    const before = text.slice(0, mention.start)
    const after = text.slice(mention.start + 1 + mention.query.length)
    const newText = `${before}@${name} ${after}`
    setText(newText)
    setMention(null)
    setTimeout(() => {
      const pos = mention.start + name.length + 2
      textareaRef.current?.setSelectionRange(pos, pos)
      textareaRef.current?.focus()
    }, 0)
  }

  const handleUpload = async (file) => {
    setUploadError(null)
    setUploading(true)
    try {
      const data = await uploadFile(file, groupId)
      setPendingFile(data)
    } catch (err) {
      setUploadError(err.message)
      setTimeout(() => setUploadError(null), 4000)
    } finally {
      setUploading(false)
    }
  }

  const handleFileSelect = (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    fileInputRef.current.value = ''
    handleUpload(file)
  }

  const handlePaste = (e) => {
    const items = Array.from(e.clipboardData?.items || [])
    const imageItem = items.find(item => item.type.startsWith('image/'))
    if (!imageItem) return
    e.preventDefault()
    const file = imageItem.getAsFile()
    if (file) handleUpload(file)
  }

  const insertEmoji = (emoji) => {
    const el = textareaRef.current
    if (!el) { setText(prev => prev + emoji); return }
    const start = el.selectionStart
    const end = el.selectionEnd
    const newText = text.slice(0, start) + emoji + text.slice(end)
    setText(newText)
    setTimeout(() => { el.setSelectionRange(start + emoji.length, start + emoji.length); el.focus() }, 0)
  }

  const allOption = { id: '__all__', name: 'all', type: 'all', avatar_color: '#6366f1' }
  const filtered = mention
    ? [allOption, ...members].filter(m =>
        m.name.toLowerCase().includes(mention.query.toLowerCase())
      )
    : []

  const handleSend = () => {
    if (!text.trim() && !pendingFile) return
    onSend(text.trim(), pendingFile ?? null)
    setSentHistory(prev => text.trim() ? [text.trim(), ...prev].slice(0, 50) : prev)
    setHistoryIndex(-1)
    setDraft('')
    setText('')
    setMention(null)
    setPendingFile(null)
  }

  return (
    <div className="p-4 border-t border-gray-700 relative">
      {showEmojiPicker && (
        <div className="absolute bottom-full right-4 mb-1 z-50">
          <EmojiPicker onSelect={insertEmoji} onClose={() => setShowEmojiPicker(false)} />
        </div>
      )}
      {/* @ 提及弹窗 */}
      {mention && filtered.length > 0 && (
        <div className="absolute bottom-full left-4 mb-1 bg-gray-800 border border-gray-700 rounded-xl shadow-xl overflow-hidden w-48">
          {filtered.map((m, i) => (
            <button
              key={m.id}
              onMouseDown={(e) => { e.preventDefault(); handleSelect(m.name) }}
              onMouseEnter={() => setSelectedIndex(i)}
              className={`w-full flex items-center gap-2 px-3 py-2 transition-colors text-left ${i === selectedIndex ? 'bg-indigo-600' : 'hover:bg-gray-700'}`}
            >
              <div
                className="w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold text-white flex-shrink-0"
                style={{ backgroundColor: m.avatar_color }}
              >
                {m.type === 'all' ? '✦' : m.name[0]}
              </div>
              <span className="text-sm text-gray-200">{m.name}</span>
              {m.type === 'all' && <span className="text-xs text-gray-400 ml-auto">{t(K.chat.input.mentionAll)}</span>}
              {m.type === 'bot' && <span className="text-xs text-gray-500 ml-auto">🤖</span>}
            </button>
          ))}
        </div>
      )}

      <input
        ref={fileInputRef}
        type="file"
        accept="image/*,application/pdf,text/plain,application/json,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,.doc,.docx,.xls,.xlsx"
        className="hidden"
        onChange={handleFileSelect}
      />

      <div className="bg-gray-800 rounded-xl overflow-hidden">
        {replyingTo && (
          <div className="flex items-center gap-2 px-4 pt-2.5 pb-2 border-b border-gray-700 text-xs">
            <span className="text-gray-500">{t(K.message.reply)}</span>
            <span className="text-indigo-400 font-medium">{replyingTo.sender_name}</span>
            <span className="text-gray-500 truncate flex-1">{replyingTo.content.slice(0, 60)}{replyingTo.content.length > 60 ? '...' : ''}</span>
            <button onClick={onCancelReply} className="text-gray-500 hover:text-gray-300 flex-shrink-0">✕</button>
          </div>
        )}

        {/* 文件预览 */}
        {pendingFile && (
          <div className="flex items-center gap-2 px-4 pt-2.5 pb-2 border-b border-gray-700">
            {pendingFile.type?.startsWith('image/') ? (
              <img src={pendingFile.preview_url || pendingFile.url} alt={pendingFile.name} className="h-16 w-16 object-cover rounded-lg flex-shrink-0" />
            ) : (
              <div className="flex items-center gap-2 bg-gray-700 rounded-lg px-3 py-2">
                <span className="text-lg">📄</span>
                <div className="min-w-0">
                  <div className="text-xs text-gray-200 truncate max-w-[160px]">{pendingFile.name}</div>
                  <div className="text-xs text-gray-500">{formatSize(pendingFile.size)}</div>
                </div>
              </div>
            )}
            <button onClick={() => setPendingFile(null)} className="text-gray-500 hover:text-red-400 text-sm ml-auto flex-shrink-0">✕</button>
          </div>
        )}

        {uploadError && (
          <div className="px-4 py-2 text-xs text-red-400 border-b border-gray-700">{uploadError}</div>
        )}

        <div className="flex gap-2 px-4 py-2 items-end">
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={disabled || uploading}
            className="text-gray-500 hover:text-gray-300 disabled:text-gray-700 transition-colors flex-shrink-0 pb-1"
            title={t(K.chat.input.uploadFile)}
          >
            {uploading ? (
              <span className="text-xs text-indigo-400">{t(K.chat.input.uploading)}</span>
            ) : (
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
              </svg>
            )}
          </button>
          <textarea
            ref={textareaRef}
            className="flex-1 bg-transparent text-gray-100 text-sm resize-none outline-none placeholder-gray-500 max-h-32 disabled:opacity-50 disabled:cursor-not-allowed"
            rows={1}
            placeholder={disabled ? t(K.chat.input.disabledPlaceholder) : t(K.chat.input.placeholder)}
            value={text}
            disabled={disabled}
            onChange={handleChange}
            onPaste={handlePaste}
            onKeyDown={(e) => {
              if (mention && filtered.length > 0) {
                if (e.key === 'ArrowDown') { e.preventDefault(); setSelectedIndex(i => (i + 1) % filtered.length); return }
                if (e.key === 'ArrowUp') { e.preventDefault(); setSelectedIndex(i => (i - 1 + filtered.length) % filtered.length); return }
                if (e.key === 'Enter') { e.preventDefault(); handleSelect(filtered[selectedIndex].name); return }
              }
              if (e.key === 'Escape') setMention(null)
              if (e.key === 'ArrowUp' && !mention && sentHistory.length > 0) {
                e.preventDefault()
                const nextIndex = historyIndex + 1
                if (nextIndex < sentHistory.length) {
                  if (historyIndex === -1) setDraft(text)
                  setHistoryIndex(nextIndex)
                  setText(sentHistory[nextIndex])
                }
                return
              }
              if (e.key === 'ArrowDown' && !mention && historyIndex >= 0) {
                e.preventDefault()
                const nextIndex = historyIndex - 1
                setHistoryIndex(nextIndex)
                setText(nextIndex === -1 ? draft : sentHistory[nextIndex])
                return
              }
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                handleSend()
              }
            }}
          />
          <button
            onClick={() => setShowEmojiPicker(p => !p)}
            onMouseDown={(e) => e.stopPropagation()}
            disabled={disabled}
            className={`text-lg leading-none flex-shrink-0 transition-colors disabled:opacity-30 ${showEmojiPicker ? 'text-yellow-400' : 'text-gray-500 hover:text-gray-300'}`}
            title={t(K.message.emoji)}
          >
            😊
          </button>
          <button
            onClick={handleSend}
            disabled={disabled || (!text.trim() && !pendingFile)}
            className="text-indigo-400 hover:text-indigo-300 disabled:text-gray-600 text-sm font-medium transition-colors flex-shrink-0"
          >
            {t(K.chat.input.send)}
          </button>
        </div>
      </div>
      <div className="text-xs text-gray-600 mt-1 px-1">{t(K.chat.input.hintFull)}</div>
    </div>
  )
})

export default MessageInput
