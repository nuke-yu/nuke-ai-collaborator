import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { K } from '../i18n/keys'
import { fetchMemberExternalSkills, putMemberExternalSkills } from '../externalSkillsApi'

const POOL_FOR_SCOPE = { global: 'external_global', group: 'external_group' }

export default function ExternalSkillPanel({ bot, groupId, onClose }) {
  const { t } = useTranslation()
  const [pool, setPool] = useState([])
  const [assignedNames, setAssignedNames] = useState(new Set())
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(null)   // skill name currently being PUT

  const load = useCallback(async () => {
    setLoading(true)
    const data = await fetchMemberExternalSkills(groupId, bot.id)
    setPool(data.pool || [])
    setAssignedNames(new Set((data.assigned || []).filter(a => a.enabled).map(a => a.skill_name)))
    setLoading(false)
  }, [groupId, bot.id])

  useEffect(() => { load() }, [load])

  const poolFor = useCallback(
    (name) => {
      const row = pool.find(p => p.name === name)
      return POOL_FOR_SCOPE[row?.scope_kind] || 'external_global'
    },
    [pool],
  )

  const toggleAssign = async (skill) => {
    setBusy(skill.name)
    const next = new Set(assignedNames)
    if (next.has(skill.name)) next.delete(skill.name)
    else next.add(skill.name)
    const desired = [...next].map(name => ({ name, pool: poolFor(name), enabled: true }))
    try {
      await putMemberExternalSkills(groupId, bot.id, desired)
      setAssignedNames(next)
    } finally {
      setBusy(null)
    }
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
            <div className="text-base font-semibold text-white">{t(K.externalSkill.title)}</div>
            <div className="text-xs text-gray-500 mt-0.5">{bot.name}</div>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white text-xl leading-none">×</button>
        </div>

        {/* Pool list */}
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center h-full text-gray-500 text-sm">{t(K.common.loading)}</div>
          ) : pool.length === 0 ? (
            <div className="flex items-center justify-center h-full text-gray-600 text-sm">{t(K.externalSkill.poolEmpty)}</div>
          ) : (
            <div className="divide-y divide-gray-700/50">
              {pool.map(skill => (
                <ExternalSkillRow
                  key={skill.id}
                  skill={skill}
                  assigned={assignedNames.has(skill.name)}
                  busy={busy === skill.name}
                  onToggle={() => toggleAssign(skill)}
                  t={t}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function ExternalSkillRow({ skill, assigned, busy, onToggle, t }) {
  const isGlobal = skill.scope_kind === 'global'
  const hostSpecific = skill.platforms === 'posix' || skill.platforms === 'windows'
  return (
    <div className="px-5 py-3 flex items-start gap-3 hover:bg-gray-750 transition-colors">
      <span className={`mt-0.5 text-[10px] font-medium px-2 py-0.5 rounded-full flex-shrink-0 ${
        isGlobal ? 'bg-purple-900/50 text-purple-300' : 'bg-blue-900/50 text-blue-300'
      }`}>
        {isGlobal ? t(K.externalSkill.sourceGlobal) : t(K.externalSkill.sourceGroup)}
      </span>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-white">{skill.name}</span>
          {skill.version && (
            <span className="text-[10px] bg-gray-700 text-gray-300 px-1.5 py-0.5 rounded">v{skill.version}</span>
          )}
        </div>
        <div className="text-xs text-gray-600 mt-0.5 truncate">{skill.source_url}</div>
        {skill.high_privilege && (
          <div className="mt-1.5 text-[10px] px-2 py-0.5 rounded border bg-red-950/40 border-red-900/40 text-red-300 leading-tight inline-block">
            ⚠️ {t(K.externalSkill.highPrivilege, { tools: skill.high_privilege })}
          </div>
        )}
        {hostSpecific && (
          <div className="mt-1 text-[10px] px-2 py-0.5 rounded border bg-yellow-950/40 border-yellow-900/40 text-yellow-300 leading-tight inline-block">
            {t(K.externalSkill.platformWarn, { platforms: skill.platforms })}
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 flex-shrink-0">
        <button
          data-testid={`assign-toggle-${skill.name}`}
          onClick={onToggle}
          disabled={busy}
          className={`relative w-10 h-5 rounded-full transition-colors flex-shrink-0 ${busy ? 'opacity-40' : 'cursor-pointer'} ${assigned ? 'bg-indigo-600' : 'bg-gray-700'}`}
        >
          <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-all ${assigned ? 'left-5' : 'left-0.5'}`} />
        </button>
      </div>
    </div>
  )
}
