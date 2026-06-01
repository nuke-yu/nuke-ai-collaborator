import { useState } from 'react'
import { updateMember } from '../api'

export default function AutoReplyModal({ member, onClose, onSaved }) {
  const [enabled, setEnabled] = useState(!!member?.auto_reply)
  const [text, setText] = useState(member?.auto_reply || '')
  const [saving, setSaving] = useState(false)

  const handleSave = async () => {
    setSaving(true)
    await updateMember(member.id, { auto_reply: enabled ? text.trim() : null })
    setSaving(false)
    onSaved?.(enabled ? text.trim() : null)
    onClose()
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px] flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-gray-900/85 backdrop-blur-xl border border-white/10 rounded-2xl shadow-[0_20px_50px_rgba(0,0,0,0.5)] w-full max-w-md overflow-hidden animate-in zoom-in-95 duration-200" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/5 bg-white/5">
          <span className="font-semibold text-indigo-300 text-sm tracking-tight flex items-center gap-2">
            <span className="text-lg">💬</span> 自动回复设置
          </span>
          <button onClick={onClose} className="text-gray-500 hover:text-white transition-colors">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="p-5 space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-gray-200">开启自动回复</p>
              <p className="text-xs text-gray-500 mt-0.5">有人 @ 你且你不在线时自动发送</p>
            </div>
            <button
              onClick={() => setEnabled(e => !e)}
              className={`relative w-10 h-5.5 rounded-full transition-colors flex-shrink-0 ${enabled ? 'bg-indigo-600' : 'bg-gray-600'}`}
              style={{ height: '22px', width: '40px' }}
            >
              <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${enabled ? 'translate-x-5' : 'translate-x-0.5'}`} />
            </button>
          </div>

          {enabled && (
            <div>
              <label className="text-xs text-gray-400 mb-1.5 block">回复内容</label>
              <textarea
                autoFocus
                value={text}
                onChange={e => setText(e.target.value)}
                placeholder="例：我现在不在，稍后回复你～"
                rows={3}
                className="w-full bg-gray-700 text-gray-100 text-sm rounded-lg px-3 py-2 resize-none outline-none focus:ring-1 focus:ring-indigo-500 placeholder-gray-500"
              />
            </div>
          )}

          <div className="flex gap-2 justify-end pt-1">
            <button onClick={onClose} className="px-4 py-1.5 text-sm text-gray-400 hover:text-gray-200 transition-colors">取消</button>
            <button
              onClick={handleSave}
              disabled={saving || (enabled && !text.trim())}
              className="px-4 py-1.5 text-sm bg-indigo-600 hover:bg-indigo-500 disabled:bg-gray-700 disabled:text-gray-500 text-white rounded-lg transition-colors"
            >
              {saving ? '保存中...' : '保存'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
