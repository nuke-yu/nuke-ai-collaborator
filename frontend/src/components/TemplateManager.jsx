import { useState, useEffect } from 'react'

const COLORS = ['#6366f1', '#8b5cf6', '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#ec4899']

const empty = { name: '', role: '', system_prompt: '', avatar_color: '#6366f1' }

export default function TemplateManager({ onClose, groupId, onAdded }) {
  const [templates, setTemplates] = useState([])
  const [editing, setEditing] = useState(null) // null | 'new' | template object
  const [form, setForm] = useState(empty)
  const [addedIds, setAddedIds] = useState(new Set())

  useEffect(() => { loadTemplates() }, [])

  const loadTemplates = () =>
    fetch('/api/templates').then(r => r.json()).then(setTemplates)

  const handleSave = async () => {
    if (!form.name.trim() || !form.role.trim() || !form.system_prompt.trim()) return
    if (editing === 'new') {
      await fetch('/api/templates', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form)
      })
    } else {
      await fetch(`/api/templates/${editing.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form)
      })
    }
    setEditing(null)
    setForm(empty)
    loadTemplates()
  }

  const handleDelete = async (id) => {
    await fetch(`/api/templates/${id}`, { method: 'DELETE' })
    loadTemplates()
  }

  const startEdit = (t) => { setEditing(t); setForm(t) }
  const startNew = () => { setEditing('new'); setForm(empty) }

  const handleAddToGroup = async (t) => {
    if (!groupId) return
    await fetch(`/api/groups/${groupId}/members`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: t.name, type: 'bot', role: t.role,
        system_prompt: t.system_prompt, avatar_color: t.avatar_color,
        model_provider: 'deepseek', model_name: 'deepseek-chat',
      }),
    })
    setAddedIds(prev => new Set([...prev, t.id]))
    setTimeout(() => setAddedIds(prev => { const s = new Set(prev); s.delete(t.id); return s }), 2000)
    onAdded?.()
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-gray-800 rounded-2xl w-[640px] max-h-[80vh] flex flex-col shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-700">
          <h2 className="text-white font-semibold">角色模板管理</h2>
          <div className="flex gap-2">
            <button onClick={startNew} className="bg-indigo-600 hover:bg-indigo-500 text-white text-sm px-3 py-1.5 rounded-lg transition-colors">+ 新建模板</button>
            <button onClick={onClose} className="text-gray-400 hover:text-white text-xl leading-none transition-colors">×</button>
          </div>
        </div>

        <div className="flex flex-1 min-h-0">
          {/* 模板列表 */}
          <div className="w-48 border-r border-gray-700 overflow-y-auto py-2">
            {templates.map(t => (
              <div
                key={t.id}
                onClick={() => startEdit(t)}
                className={`group flex items-center gap-2 px-3 py-2 cursor-pointer transition-colors ${editing?.id === t.id ? 'bg-gray-700' : 'hover:bg-gray-700'}`}
              >
                <div className="w-6 h-6 rounded-full flex-shrink-0 flex items-center justify-center text-xs font-bold text-white" style={{ backgroundColor: t.avatar_color }}>
                  {t.name[0]}
                </div>
                <span className="text-sm text-gray-200 truncate flex-1">{t.name}</span>
                {groupId && (
                  <button
                    onClick={e => { e.stopPropagation(); handleAddToGroup(t) }}
                    title="添加到当前群组"
                    className={`flex-shrink-0 text-xs px-1.5 py-0.5 rounded transition-all
                      ${addedIds.has(t.id)
                        ? 'bg-green-600 text-white opacity-100'
                        : 'bg-indigo-600 hover:bg-indigo-500 text-white opacity-0 group-hover:opacity-100'
                      }`}
                  >
                    {addedIds.has(t.id) ? '✓' : '+'}
                  </button>
                )}
              </div>
            ))}
            {templates.length === 0 && (
              <div className="text-gray-500 text-sm text-center py-8">暂无模板</div>
            )}
          </div>

          {/* 编辑区 */}
          <div className="flex-1 p-5 overflow-y-auto">
            {editing ? (
              <div className="space-y-4">
                <div>
                  <label className="text-xs text-gray-400 mb-1 block">名字</label>
                  <input className="w-full bg-gray-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                    value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="角色名字" autoFocus />
                </div>
                <div>
                  <label className="text-xs text-gray-400 mb-1 block">角色职位</label>
                  <input className="w-full bg-gray-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                    value={form.role} onChange={e => setForm({ ...form, role: e.target.value })} placeholder="如：产品经理、架构师..." />
                </div>
                <div>
                  <label className="text-xs text-gray-400 mb-1 block">角色描述（System Prompt）</label>
                  <textarea className="w-full bg-gray-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500 resize-none"
                    rows={6} value={form.system_prompt} onChange={e => setForm({ ...form, system_prompt: e.target.value })}
                    placeholder="描述这个 AI 角色的职责、行为方式和回答风格..." />
                </div>
                <div>
                  <label className="text-xs text-gray-400 mb-1 block">头像颜色</label>
                  <div className="flex gap-2">
                    {COLORS.map(c => (
                      <button key={c} onClick={() => setForm({ ...form, avatar_color: c })}
                        className={`w-6 h-6 rounded-full transition-transform ${form.avatar_color === c ? 'scale-125 ring-2 ring-white' : ''}`}
                        style={{ backgroundColor: c }} />
                    ))}
                  </div>
                </div>
                <div className="flex gap-2 pt-2">
                  <button onClick={handleSave} className="flex-1 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg py-2 text-sm font-medium transition-colors">保存</button>
                  {editing !== 'new' && groupId && (
                    <button
                      onClick={() => handleAddToGroup(editing)}
                      className={`px-3 py-2 rounded-lg text-sm font-medium transition-colors ${addedIds.has(editing.id) ? 'bg-green-600 text-white' : 'bg-emerald-600 hover:bg-emerald-500 text-white'}`}
                    >
                      {addedIds.has(editing.id) ? '✓ 已添加' : '+ 添加到群组'}
                    </button>
                  )}
                  {editing !== 'new' && (
                    <button onClick={() => handleDelete(editing.id)} className="bg-red-600/20 hover:bg-red-600/40 text-red-400 rounded-lg px-4 py-2 text-sm transition-colors">删除</button>
                  )}
                  <button onClick={() => { setEditing(null); setForm(empty) }} className="bg-gray-700 hover:bg-gray-600 text-gray-300 rounded-lg px-4 py-2 text-sm transition-colors">取消</button>
                </div>
              </div>
            ) : (
              <div className="flex items-center justify-center h-full text-gray-500 text-sm">选择一个模板编辑，或新建模板</div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
