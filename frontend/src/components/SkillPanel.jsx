import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { K } from '../i18n/keys'
import { fetchScopeSkills, copyScopeSkill, fetchSkillContent, writeScopeSkill } from '../skillsApi'
import ExternalSkillPanel from './ExternalSkillPanel'

const LAYER_LABEL = {
  system:   { text: 'System',   color: 'bg-purple-900/50 text-purple-300' },
  group:    { text: 'Group',    color: 'bg-blue-900/50 text-blue-300' },
  role:     { text: 'Role',     color: 'bg-green-900/50 text-green-300' },
  personal: { text: 'Personal', color: 'bg-gray-700 text-gray-300' },
  learned:  { text: 'Learned',  color: 'bg-yellow-900/50 text-yellow-300' },
}

const INJECTED_LABEL = {
  full:     { textKey: 'full:全文注入',     color: 'text-emerald-400' },
  metadata: { textKey: 'metadata:元数据',   color: 'text-sky-400' },
  null:     { textKey: 'null:未注入',       color: 'text-gray-600' },
}

// Injected label display text (not translated — these are technical labels)
const INJECTED_TEXT = {
  full:     '全文注入',
  metadata: '元数据',
  null:     '未注入',
}

export default function SkillPanel({ bot, groupId, onClose }) {
  const { t } = useTranslation()
  const [skills, setSkills] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('all') // all / active / disabled / draft
  const [toggling, setToggling] = useState(null)
  const [testSkill, setTestSkill] = useState(null)   // skill object being tested
  const [editingSkill, setEditingSkill] = useState(null) // skill object being edited/viewed
  const [browsing, setBrowsing] = useState(false)
  const [browseScope, setBrowseScope] = useState('system')
  const [scopeSkills, setScopeSkills] = useState([])
  const [showExternal, setShowExternal] = useState(false)

  const scopeDescriptor = useCallback((kind) => {
    if (kind === 'group') return `group:${groupId}`
    if (kind === 'role') return `role:${groupId}:${bot.role}`
    return 'system'
  }, [groupId, bot.role])

  const loadScope = useCallback(async (kind) => {
    const d = await fetchScopeSkills(scopeDescriptor(kind))
    setScopeSkills(d.skills || [])
  }, [scopeDescriptor])

  useEffect(() => {
    if (browsing) loadScope(browseScope)
  }, [browsing, browseScope, loadScope])

  const load = useCallback(async () => {
    setLoading(true)
    const url = `/api/members/${bot.id}/skills${groupId ? `?group_id=${groupId}` : ''}`
    const res = await fetch(url)
    if (res.ok) {
      const data = await res.json()
      setSkills(data.skills)
    }
    setLoading(false)
  }, [bot.id, groupId])

  useEffect(() => { load() }, [load])

  // Hot-reload: re-fetch when any relevant skill file changes on disk
  useEffect(() => {
    const handler = (e) => {
      const { source, member_id } = e.detail
      const isMine = member_id === bot.id
      const isGlobal = source === 'system' || source === 'role' || source === 'group'
      if (isMine || isGlobal) load()
    }
    window.addEventListener('skills_changed', handler)
    return () => window.removeEventListener('skills_changed', handler)
  }, [bot.id, load])

  const toggle = async (skill) => {
    const newStatus = skill.status === 'active' ? 'disabled' : 'active'
    if (skill.layer === 'system') return // system skills are read-only
    setToggling(skill.name)
    await fetch(`/api/members/${bot.id}/skills/${skill.name}/status`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: newStatus }),
    })
    await load()
    setToggling(null)
  }

  const approve = async (skill) => {
    await fetch(`/api/members/${bot.id}/skills/learned/${skill.name}/approve`, { method: 'POST' })
    await load()
  }

  const reject = async (skill) => {
    if (!confirm(t(K.skill.confirmReject, { name: skill.name }))) return
    await fetch(`/api/members/${bot.id}/skills/learned/${skill.name}/reject`, { method: 'POST' })
    await load()
  }

  const filtered = skills.filter(s => {
    if (filter === 'all') return true
    if (filter === 'draft') return s.status === 'draft'
    if (filter === 'active') return s.status === 'active'
    if (filter === 'disabled') return s.status === 'disabled'
    return true
  })

  const draftCount = skills.filter(s => s.status === 'draft').length
  const layers = ['all', 'active', 'disabled', 'draft']

  const filterLabel = (f) => {
    if (f === 'all') return t(K.skill.all)
    if (f === 'active') return t(K.skill.active)
    if (f === 'disabled') return t(K.skill.disabled)
    return t(K.skill.pending)
  }

  if (showExternal) {
    return <ExternalSkillPanel bot={bot} groupId={groupId} onClose={() => setShowExternal(false)} />
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-gray-800 rounded-2xl shadow-xl flex flex-col overflow-hidden relative"
        style={{ width: '720px', maxWidth: '95vw', height: '580px', maxHeight: '90vh' }}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700 flex-shrink-0">
          <div>
            <div className="text-base font-semibold text-white">{t(K.skill.title)}</div>
            <div className="text-xs text-gray-500 mt-0.5">{bot.name} · {t(K.skill.count, { count: skills.length })}</div>
          </div>
          <div className="flex items-center gap-3">
            {/* Filter tabs */}
            <div className="flex gap-1 bg-gray-900 rounded-lg p-1">
              {layers.map(f => (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  className={`px-3 py-1 text-xs rounded-md transition-colors relative ${
                    filter === f ? 'bg-indigo-600 text-white' : 'text-gray-400 hover:text-white'
                  }`}
                >
                  {filterLabel(f)}
                  {f === 'draft' && draftCount > 0 && (
                    <span className="ml-1 bg-yellow-500 text-black text-[10px] font-bold rounded-full px-1">
                      {draftCount}
                    </span>
                  )}
                </button>
              ))}
            </div>
            <button
              data-testid="open-external-skills"
              onClick={() => setShowExternal(true)}
              className="text-xs px-3 py-1 rounded-lg bg-gray-700 text-gray-300 hover:text-white transition-colors"
            >
              {t(K.externalSkill.open)}
            </button>
            <button
              data-testid="browse-scopes-toggle"
              onClick={() => setBrowsing(b => !b)}
              className={`text-xs px-3 py-1 rounded-lg transition-colors ${browsing ? 'bg-indigo-600 text-white' : 'bg-gray-700 text-gray-300 hover:text-white'}`}
            >
              {t(K.skill.browseScopes)}
            </button>
            <button onClick={onClose} className="text-gray-500 hover:text-white text-xl leading-none">×</button>
          </div>
        </div>

        {/* Scope browser */}
        {browsing && (
          <div className="border-b border-gray-700 px-5 py-3 flex-shrink-0">
            <div className="flex items-center gap-3 mb-3">
              <select
                value={browseScope}
                onChange={e => setBrowseScope(e.target.value)}
                className="bg-gray-900 text-gray-300 text-xs rounded-lg px-2 py-1 outline-none"
              >
                <option value="group">{t(K.skill.scopeGroup)}</option>
                <option value="role">{t(K.skill.scopeRole)}</option>
                <option value="system">{t(K.skill.scopeSystem)}</option>
              </select>
            </div>
            <div className="space-y-1 max-h-48 overflow-y-auto">
              {scopeSkills.map(s => (
                <div key={s.name} className="flex items-center justify-between py-1 px-2 rounded-lg hover:bg-gray-700/50">
                  <span className="text-sm text-gray-200">{s.name}</span>
                  <button
                    data-testid={`copy-skill-${s.name}`}
                    onClick={async () => {
                      await copyScopeSkill(scopeDescriptor(browseScope), s.name, `group:${groupId}`)
                      await load()
                    }}
                    className="text-xs px-2.5 py-1 rounded-lg bg-indigo-700 hover:bg-indigo-600 text-white transition-colors flex-shrink-0 ml-3"
                  >
                    {t(K.skill.copyToGroup)}
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Skill test panel */}
        {testSkill && (
          <SkillTestPanel
            bot={bot}
            groupId={groupId}
            skill={testSkill}
            onClose={() => setTestSkill(null)}
          />
        )}

        {/* Skill editor panel */}
        {editingSkill && (
          <SkillEditorPanel
            bot={bot}
            groupId={groupId}
            skill={editingSkill}
            onClose={() => setEditingSkill(null)}
          />
        )}

        {/* Skill list */}
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center h-full text-gray-500 text-sm">{t(K.common.loading)}</div>
          ) : filtered.length === 0 ? (
            <div className="flex items-center justify-center h-full text-gray-600 text-sm">
              {filter === 'draft' ? t(K.skill.noPendingSkills) : t(K.skill.noSkills)}
            </div>
          ) : (
            <div className="divide-y divide-gray-700/50">
              {filtered.map(skill => (
                <SkillRow
                  key={`${skill.layer}-${skill.name}`}
                  skill={skill}
                  onToggle={() => toggle(skill)}
                  onApprove={() => approve(skill)}
                  onReject={() => reject(skill)}
                  onTest={() => setTestSkill(skill)}
                  onEdit={() => setEditingSkill(skill)}
                  toggling={toggling === skill.name}
                  t={t}
                />
              ))}
            </div>
          )}
        </div>

        {/* Legend */}
        <div className="px-5 py-3 border-t border-gray-700 flex items-center gap-4 flex-shrink-0">
          <span className="text-xs text-gray-600">{t(K.skill.injectedStatus)}</span>
          {Object.entries(INJECTED_LABEL).map(([k, v]) => (
            <span key={k} className={`text-xs ${v.color}`}>● {INJECTED_TEXT[k]}</span>
          ))}
          <span className="ml-auto text-xs text-gray-600">{t(K.skill.systemReadonly)}</span>
        </div>
      </div>
    </div>
  )
}

function SkillTestPanel({ bot, groupId, skill, onClose }) {
  const { t } = useTranslation()
  const [message, setMessage] = useState('')
  const [response, setResponse] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const run = async () => {
    if (!message.trim() || loading) return
    setLoading(true)
    setResponse(null)
    setError(null)
    try {
      const res = await fetch(`/api/members/${bot.id}/skills/${encodeURIComponent(skill.name)}/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: message.trim(), group_id: groupId || null }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || t(K.skill.requestFailed))
      setResponse(data.response)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="absolute inset-0 bg-gray-800 z-10 flex flex-col rounded-2xl overflow-hidden">
      <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700 flex-shrink-0">
        <div>
          <button onClick={onClose} className="text-xs text-gray-500 hover:text-gray-300 flex items-center gap-1 mb-1">
            ← {t(K.common.back)}
          </button>
          <div className="text-base font-semibold text-white">{t(K.skill.testTitle, { name: skill.name })}</div>
          {skill.description && <div className="text-xs text-gray-500 mt-0.5">{skill.description}</div>}
        </div>
      </div>
      <div className="flex-1 flex flex-col gap-3 p-5 overflow-y-auto">
        <div>
          <div className="text-xs text-gray-400 mb-1.5">{t(K.skill.inputMessage)}</div>
          <textarea
            className="w-full bg-gray-900 text-gray-100 text-sm rounded-lg px-3 py-2 resize-none outline-none focus:ring-1 focus:ring-indigo-500 font-mono"
            rows={4}
            value={message}
            onChange={e => setMessage(e.target.value)}
            onKeyDown={e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') run() }}
            placeholder={t(K.skill.testPlaceholder)}
          />
        </div>
        <button
          onClick={run}
          disabled={!message.trim() || loading}
          className="self-start text-sm px-4 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white transition-colors"
        >
          {loading ? t(K.skill.running) : t(K.skill.runTest)}
        </button>
        {error && (
          <div className="text-xs text-red-400 bg-red-900/20 rounded-lg px-3 py-2">{error}</div>
        )}
        {response !== null && (
          <div>
            <div className="text-xs text-gray-400 mb-1.5">{t(K.skill.aiResponse)}</div>
            <div className="bg-gray-900 rounded-lg px-3 py-2 text-sm text-gray-100 whitespace-pre-wrap leading-relaxed">
              {response}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function SkillRow({ skill, onToggle, onApprove, onReject, onTest, onEdit, toggling, t }) {
  const layer = LAYER_LABEL[skill.layer] || { text: skill.layer, color: 'bg-gray-700 text-gray-300' }
  const injected = INJECTED_LABEL[skill.injected] || INJECTED_LABEL['null']
  const isDraft = skill.status === 'draft'
  const isDisabled = skill.status === 'disabled'
  const isSystem = skill.layer === 'system'

  return (
    <div className={`px-5 py-3 flex items-start gap-3 hover:bg-gray-750 transition-colors ${isDisabled ? 'opacity-50' : ''}`}>
      {/* Layer badge */}
      <span className={`mt-0.5 text-[10px] font-medium px-2 py-0.5 rounded-full flex-shrink-0 ${layer.color}`}>
        {layer.text}
      </span>

      {/* Info */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-white">{skill.name}</span>
          {skill.always && (
            <span className="text-[10px] bg-indigo-900/50 text-indigo-300 px-1.5 py-0.5 rounded">always</span>
          )}
          {isDraft && (
            <span className="text-[10px] bg-yellow-900/50 text-yellow-300 px-1.5 py-0.5 rounded">{t(K.skill.pending)}</span>
          )}
        </div>
        {skill.description && (
          <div className="text-xs text-gray-400 mt-0.5 truncate">{skill.description}</div>
        )}
        {skill.when_to_use && (
          <div className="text-xs text-gray-600 mt-0.5 truncate">{t(K.skill.trigger)}{skill.when_to_use}</div>
        )}
        {skill.diagnostics && skill.diagnostics.length > 0 && (
          <div className="mt-1.5 space-y-1">
            {skill.diagnostics.map((diag, idx) => (
              <div
                key={idx}
                className={`text-[10px] px-2 py-0.5 rounded border leading-tight ${
                  diag.severity === 'critical'
                    ? 'bg-red-950/40 border-red-900/40 text-red-300'
                    : 'bg-yellow-950/40 border-yellow-900/40 text-yellow-300'
                }`}
              >
                ⚠️ {diag.message}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Injected status */}
      {!isDraft && (
        <span className={`text-xs mt-1 flex-shrink-0 ${injected.color}`}>
          {INJECTED_TEXT[skill.injected] ?? INJECTED_TEXT['null']}
        </span>
      )}

      {/* Actions */}
      <div className="flex items-center gap-2 flex-shrink-0">
        {isDraft ? (
          <>
            <button
              onClick={onApprove}
              className="text-xs px-2.5 py-1 rounded-lg bg-emerald-700 hover:bg-emerald-600 text-white transition-colors"
            >
              {t(K.skill.approve)}
            </button>
            <button
              onClick={onReject}
              className="text-xs px-2.5 py-1 rounded-lg bg-red-900/60 hover:bg-red-800 text-red-300 transition-colors"
            >
              {t(K.skill.reject)}
            </button>
          </>
        ) : (
          <>
            <button
              onClick={onEdit}
              className="text-xs px-2.5 py-1 rounded-lg bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors"
            >
              {isSystem ? t(K.skill.view) : t(K.skill.edit)}
            </button>
            <button
              onClick={onTest}
              className="text-xs px-2.5 py-1 rounded-lg bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors"
            >
              {t(K.skill.test)}
            </button>
            <button
              onClick={onToggle}
              disabled={toggling || isSystem}
              className={`relative w-10 h-5 rounded-full transition-colors flex-shrink-0 ${
                isSystem ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'
              } ${!isDisabled ? 'bg-indigo-600' : 'bg-gray-700'}`}
            >
              <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-all ${!isDisabled ? 'left-5' : 'left-0.5'}`} />
            </button>
          </>
        )}
      </div>
    </div>
  )
}

function SkillEditorPanel({ bot, groupId, skill, onClose }) {
  const { t } = useTranslation()
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [success, setSuccess] = useState(false)

  const isSystem = skill.layer === 'system'

  const getSkillScope = useCallback(() => {
    if (skill.layer === 'system') return 'system'
    if (skill.layer === 'group') return `group:${groupId}`
    if (skill.layer === 'role') return `role:${groupId}:${bot.role}`
    return `bot:${groupId}:${bot.id}`
  }, [skill.layer, groupId, bot.role, bot.id])

  const scope = getSkillScope()

  useEffect(() => {
    let active = true
    setLoading(true)
    setError(null)
    fetchSkillContent(scope, skill.name)
      .then(d => {
        if (active) {
          setContent(d.content || '')
          setLoading(false)
        }
      })
      .catch(e => {
        if (active) {
          setError(e.message)
          setLoading(false)
        }
      })
    return () => { active = false }
  }, [scope, skill.name])

  const save = async () => {
    if (isSystem || saving) return
    setSaving(true)
    setError(null)
    setSuccess(false)
    try {
      await writeScopeSkill(scope, skill.name, content)
      setSuccess(true)
      setTimeout(() => setSuccess(false), 3000)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="absolute inset-0 bg-gray-800 z-10 flex flex-col rounded-2xl overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700 flex-shrink-0">
        <div>
          <button onClick={onClose} className="text-xs text-gray-500 hover:text-gray-300 flex items-center gap-1 mb-1">
            ← {t(K.common.back)}
          </button>
          <div className="text-base font-semibold text-white">
            {isSystem ? t(K.skill.viewTitle, { name: skill.name }) : t(K.skill.editTitle, { name: skill.name })}
          </div>
          {skill.description && <div className="text-xs text-gray-500 mt-0.5">{skill.description}</div>}
        </div>
      </div>

      {/* Editor Body */}
      <div className="flex-1 flex flex-col p-5 overflow-hidden">
        {loading ? (
          <div className="flex-1 flex items-center justify-center text-gray-500 text-sm">{t(K.common.loading)}</div>
        ) : (
          <div className="flex-1 flex flex-col gap-3 min-h-0">
            {error && (
              <div className="text-xs text-red-400 bg-red-900/20 rounded-lg px-3 py-2 flex-shrink-0">{error}</div>
            )}
            {success && (
              <div className="text-xs text-emerald-400 bg-emerald-900/20 rounded-lg px-3 py-2 flex-shrink-0">{t(K.skill.saveSuccess)}</div>
            )}

            <div className="flex-1 min-h-0 flex flex-col">
              <textarea
                className="flex-1 w-full bg-gray-900 text-gray-100 text-sm rounded-lg p-3 outline-none focus:ring-1 focus:ring-indigo-500 font-mono resize-none leading-relaxed"
                value={content}
                onChange={e => setContent(e.target.value)}
                readOnly={isSystem}
                placeholder="# SKILL.md..."
              />
            </div>

            {!isSystem && (
              <button
                onClick={save}
                disabled={saving}
                className="self-start text-sm px-4 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white transition-colors flex-shrink-0"
              >
                {saving ? t(K.skill.saving) : t(K.skill.save)}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
