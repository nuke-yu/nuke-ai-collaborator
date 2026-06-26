import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { K } from '../i18n/keys'
import { fetchTemplateRoles, fetchScopeSkills } from '../skillsApi'

const COLORS = ['#6366f1', '#8b5cf6', '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#ec4899']

const empty = { name: '', role: '', system_prompt: '', avatar_color: '#6366f1' }

export default function TemplateManager({ onClose, groupId, onAdded }) {
  const { t } = useTranslation()
  const [templates, setTemplates] = useState([])
  const [editing, setEditing] = useState(null) // null | 'new' | template object
  const [form, setForm] = useState(empty)
  const [addedIds, setAddedIds] = useState(new Set())

  // File-based role catalog state
  const [lang, setLang] = useState('zh')
  const [tplRoles, setTplRoles] = useState([])
  const [selRole, setSelRole] = useState(null)
  const [roleSkills, setRoleSkills] = useState([])

  useEffect(() => { loadTemplates() }, [])

  useEffect(() => {
    fetchTemplateRoles(lang).then((d) => setTplRoles(d.roles || [])).catch(() => setTplRoles([]))
  }, [lang])

  useEffect(() => {
    if (!selRole) return
    fetchScopeSkills(`template:${lang}:${selRole}`).then((d) => setRoleSkills(d.skills || [])).catch(() => setRoleSkills([]))
  }, [selRole, lang])

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

  const handleAddToGroup = async (tmpl) => {
    if (!groupId) return
    await fetch(`/api/groups/${groupId}/members`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: tmpl.name, type: 'bot', role: tmpl.role,
        system_prompt: tmpl.system_prompt, avatar_color: tmpl.avatar_color,
        model_provider: 'deepseek', model_name: 'deepseek-chat',
      }),
    })
    setAddedIds(prev => new Set([...prev, tmpl.id]))
    setTimeout(() => setAddedIds(prev => { const s = new Set(prev); s.delete(tmpl.id); return s }), 2000)
    onAdded?.()
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-gray-800 rounded-2xl w-[640px] max-h-[80vh] flex flex-col shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-700">
          <h2 className="text-white font-semibold">{t(K.template.titleManage)}</h2>
          <div className="flex gap-2">
            <button onClick={startNew} className="bg-indigo-600 hover:bg-indigo-500 text-white text-sm px-3 py-1.5 rounded-lg transition-colors">{t(K.template.newTemplate)}</button>
            <button onClick={onClose} className="text-gray-400 hover:text-white text-xl leading-none transition-colors">×</button>
          </div>
        </div>

        <div className="flex flex-1 min-h-0">
          {/* Template list */}
          <div className="w-48 border-r border-gray-700 overflow-y-auto py-2">
            {templates.map(tmpl => (
              <div
                key={tmpl.id}
                onClick={() => startEdit(tmpl)}
                className={`group flex items-center gap-2 px-3 py-2 cursor-pointer transition-colors ${editing?.id === tmpl.id ? 'bg-gray-700' : 'hover:bg-gray-700'}`}
              >
                <div className="w-6 h-6 rounded-full flex-shrink-0 flex items-center justify-center text-xs font-bold text-white" style={{ backgroundColor: tmpl.avatar_color }}>
                  {tmpl.name[0]}
                </div>
                <span className="text-sm text-gray-200 truncate flex-1">{tmpl.name}</span>
                {groupId && (
                  <button
                    onClick={e => { e.stopPropagation(); handleAddToGroup(tmpl) }}
                    title={t(K.template.addToGroup)}
                    className={`flex-shrink-0 text-xs px-1.5 py-0.5 rounded transition-all
                      ${addedIds.has(tmpl.id)
                        ? 'bg-green-600 text-white opacity-100'
                        : 'bg-indigo-600 hover:bg-indigo-500 text-white opacity-0 group-hover:opacity-100'
                      }`}
                  >
                    {addedIds.has(tmpl.id) ? '✓' : '+'}
                  </button>
                )}
              </div>
            ))}
            {templates.length === 0 && (
              <div className="text-gray-500 text-sm text-center py-8">{t(K.template.noTemplates)}</div>
            )}
          </div>

          {/* Edit area */}
          <div className="flex-1 p-5 overflow-y-auto">
            {editing ? (
              <div className="space-y-4">
                <div>
                  <label className="text-xs text-gray-400 mb-1 block">{t(K.template.nameLabel)}</label>
                  <input className="w-full bg-gray-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                    value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder={t(K.template.nameLabel)} autoFocus />
                </div>
                <div>
                  <label className="text-xs text-gray-400 mb-1 block">{t(K.template.roleLabel)}</label>
                  <input className="w-full bg-gray-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                    value={form.role} onChange={e => setForm({ ...form, role: e.target.value })} placeholder={t(K.member.rolePlaceholder2)} />
                </div>
                <div>
                  <label className="text-xs text-gray-400 mb-1 block">{t(K.member.systemPromptLabel)}</label>
                  <textarea className="w-full bg-gray-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500 resize-none"
                    rows={6} value={form.system_prompt} onChange={e => setForm({ ...form, system_prompt: e.target.value })}
                    placeholder={t(K.member.systemPromptPlaceholder2)} />
                </div>
                <div>
                  <label className="text-xs text-gray-400 mb-1 block">{t(K.template.avatarColor)}</label>
                  <div className="flex gap-2">
                    {COLORS.map(c => (
                      <button key={c} onClick={() => setForm({ ...form, avatar_color: c })}
                        className={`w-6 h-6 rounded-full transition-transform ${form.avatar_color === c ? 'scale-125 ring-2 ring-white' : ''}`}
                        style={{ backgroundColor: c }} />
                    ))}
                  </div>
                </div>
                <div className="flex gap-2 pt-2">
                  <button onClick={handleSave} className="flex-1 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg py-2 text-sm font-medium transition-colors">{t(K.template.save)}</button>
                  {editing !== 'new' && groupId && (
                    <button
                      onClick={() => handleAddToGroup(editing)}
                      className={`px-3 py-2 rounded-lg text-sm font-medium transition-colors ${addedIds.has(editing.id) ? 'bg-green-600 text-white' : 'bg-emerald-600 hover:bg-emerald-500 text-white'}`}
                    >
                      {addedIds.has(editing.id) ? t(K.template.added) : t(K.template.addToGroup)}
                    </button>
                  )}
                  {editing !== 'new' && (
                    <button onClick={() => handleDelete(editing.id)} className="bg-red-600/20 hover:bg-red-600/40 text-red-400 rounded-lg px-4 py-2 text-sm transition-colors">{t(K.template.delete)}</button>
                  )}
                  <button onClick={() => { setEditing(null); setForm(empty) }} className="bg-gray-700 hover:bg-gray-600 text-gray-300 rounded-lg px-4 py-2 text-sm transition-colors">{t(K.template.cancel)}</button>
                </div>
              </div>
            ) : (
              <div className="flex items-center justify-center h-full text-gray-500 text-sm">{t(K.template.selectOrCreate)}</div>
            )}
          </div>
        </div>

        {/* File-based role catalog section */}
        <div className="border-t border-gray-700 px-6 py-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-white text-sm font-semibold">{t(K.template.fileRolesTitle)}</h3>
            <div className="flex items-center gap-2 text-xs text-gray-400">
              <span>{t(K.template.lang)}</span>
              <button
                onClick={() => setLang('zh')}
                className={`px-2 py-0.5 rounded transition-colors ${lang === 'zh' ? 'bg-indigo-600 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'}`}
              >zh</button>
              <button
                onClick={() => setLang('en')}
                className={`px-2 py-0.5 rounded transition-colors ${lang === 'en' ? 'bg-indigo-600 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'}`}
              >en</button>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {tplRoles.map((r) => (
              <button
                key={r.role}
                onClick={() => setSelRole(r.role)}
                className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-colors ${selRole === r.role ? 'bg-indigo-700 text-white' : 'bg-gray-700 hover:bg-gray-600 text-gray-200'}`}
              >
                <span className="w-4 h-4 rounded-full flex-shrink-0 inline-block" style={{ backgroundColor: r.avatar_color }} />
                <span>{r.display_name} ({r.skill_count})</span>
              </button>
            ))}
          </div>
          {selRole && roleSkills.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1">
              {roleSkills.map((s) => (
                <span key={s.name} className="text-xs bg-gray-700 text-gray-300 px-2 py-0.5 rounded">{s.name}</span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
