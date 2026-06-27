import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { K } from '../i18n/keys'
import {
  fetchMemberExternalSkills, putMemberExternalSkills, importExternalSkill, removeExternalSkill,
  fetchPermissionRules, addPermissionRule, removePermissionRule,
} from '../externalSkillsApi'

const POOL_FOR_SCOPE = { global: 'external_global', group: 'external_group' }

const RUN_SKILL_PATTERNS = new Set(['run_skill', 'run_skill*'])
const matchesSkill = (rule, name) =>
  RUN_SKILL_PATTERNS.has(rule.tool_pattern) && rule.args_pattern === name

export default function ExternalSkillPanel({ bot, groupId, onClose }) {
  const { t } = useTranslation()
  const [pool, setPool] = useState([])
  const [assignedNames, setAssignedNames] = useState(new Set())
  const [rules, setRules] = useState([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(null)   // skill name currently being PUT
  const [importing, setImporting] = useState(false)
  const [showImport, setShowImport] = useState(false)
  const [importScope, setImportScope] = useState('group')   // 'group' | 'global'
  const [gitUrl, setGitUrl] = useState('')
  const [gitRef, setGitRef] = useState('')
  const [importResult, setImportResult] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    const data = await fetchMemberExternalSkills(groupId, bot.id)
    setPool(data.pool || [])
    setAssignedNames(new Set((data.assigned || []).filter(a => a.enabled).map(a => a.skill_name)))
    setLoading(false)
  }, [groupId, bot.id])

  const loadRules = useCallback(async () => {
    setRules(await fetchPermissionRules(bot.id))
  }, [bot.id])

  useEffect(() => { load(); loadRules(); }, [load, loadRules])

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

  const submitImport = async () => {
    if (!gitUrl.trim() || importing) return
    setImporting(true)
    setImportResult(null)
    try {
      const scope = importScope === 'global' ? 'global' : { group_id: groupId }
      const res = await importExternalSkill({ git_url: gitUrl.trim(), ref: gitRef.trim(), scope })
      setImportResult(res)
      setShowImport(false)
      setGitUrl('')
      setGitRef('')
      await load()
    } catch (e) {
      setImportResult({ imported: [], rejected: [{ path: gitUrl, reason: e.message }] })
    } finally {
      setImporting(false)
    }
  }

  const removeFromPool = async (skill) => {
    if (!confirm(t(K.externalSkill.confirmRemove, { name: skill.name }))) return
    await removeExternalSkill(skill.id)
    await load()
  }

  const policyOf = useCallback(
    (name) => {
      const r = rules.find(rule => matchesSkill(rule, name))
      return r ? r.action : 'ask'   // no rule = default HIL = 'ask'
    },
    [rules],
  )

  const setPolicy = async (skill, action) => {
    // Clear every existing matching rule first, then add one if not 'ask'.
    for (const r of rules.filter(rule => matchesSkill(rule, skill.name))) {
      await removePermissionRule(bot.id, r.id)
    }
    if (action !== 'ask') {
      await addPermissionRule(bot.id, {
        tool_pattern: 'run_skill', args_pattern: skill.name, action,
      })
    }
    await loadRules()
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
          <div className="flex items-center gap-3">
            <button
              data-testid="open-import"
              onClick={() => setShowImport(true)}
              className="text-xs px-3 py-1 rounded-lg bg-indigo-700 hover:bg-indigo-600 text-white transition-colors"
            >
              {t(K.externalSkill.importButton)}
            </button>
            <button onClick={onClose} className="text-gray-500 hover:text-white text-xl leading-none">×</button>
          </div>
        </div>

        {importResult && (
          <div className="px-5 py-2 border-b border-gray-700 text-xs text-gray-400 flex-shrink-0">
            {t(K.externalSkill.importedCount, { count: importResult.imported.length })}
            {importResult.rejected.length > 0 && (
              <span className="text-red-400 ml-3">{t(K.externalSkill.rejectedCount, { count: importResult.rejected.length })}</span>
            )}
          </div>
        )}

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
                  policy={policyOf(skill.name)}
                  onToggle={() => toggleAssign(skill)}
                  onRemove={() => removeFromPool(skill)}
                  onPolicy={(action) => setPolicy(skill, action)}
                  t={t}
                />
              ))}
            </div>
          )}
        </div>

        {showImport && (
          <div className="absolute inset-0 bg-gray-800/95 z-10 flex flex-col rounded-2xl overflow-hidden">
            <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700">
              <div className="text-base font-semibold text-white">{t(K.externalSkill.importTitle)}</div>
              <button onClick={() => setShowImport(false)} className="text-gray-500 hover:text-white text-xl leading-none">×</button>
            </div>
            <div className="flex-1 flex flex-col gap-3 p-5">
              <label className="text-xs text-gray-400">{t(K.externalSkill.importScope)}</label>
              <select
                data-testid="import-scope"
                value={importScope}
                onChange={e => setImportScope(e.target.value)}
                className="bg-gray-900 text-gray-200 text-sm rounded-lg px-3 py-2 outline-none"
              >
                <option value="group">{t(K.externalSkill.sourceGroup)}</option>
                <option value="global">{t(K.externalSkill.sourceGlobal)}</option>
              </select>

              <label className="text-xs text-gray-400">{t(K.externalSkill.importUrl)}</label>
              <input
                data-testid="import-url"
                value={gitUrl}
                onChange={e => setGitUrl(e.target.value)}
                placeholder="https://github.com/org/repo"
                className="bg-gray-900 text-gray-100 text-sm rounded-lg px-3 py-2 outline-none focus:ring-1 focus:ring-indigo-500 font-mono"
              />

              <label className="text-xs text-gray-400">{t(K.externalSkill.importRef)}</label>
              <input
                data-testid="import-ref"
                value={gitRef}
                onChange={e => setGitRef(e.target.value)}
                placeholder={t(K.externalSkill.importRefPlaceholder)}
                className="bg-gray-900 text-gray-100 text-sm rounded-lg px-3 py-2 outline-none focus:ring-1 focus:ring-indigo-500 font-mono"
              />

              <button
                data-testid="submit-import"
                onClick={submitImport}
                disabled={!gitUrl.trim() || importing}
                className="self-start text-sm px-4 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white transition-colors"
              >
                {importing ? t(K.externalSkill.importing) : t(K.externalSkill.importSubmit)}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function ExternalSkillRow({ skill, assigned, busy, policy, onToggle, onRemove, onPolicy, t }) {
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
        {skill.description && (
          <div className="text-xs text-gray-400 mt-0.5">{skill.description}</div>
        )}
        <div className="text-xs text-gray-600 mt-0.5 truncate">{skill.source_url}</div>
        {skill.imported_by != null && (
          <div className="text-[10px] text-gray-600 mt-0.5">{t(K.externalSkill.importedBy)} #{skill.imported_by}</div>
        )}
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
          data-testid={`remove-skill-${skill.id}`}
          onClick={onRemove}
          className="text-xs px-2.5 py-1 rounded-lg bg-red-900/60 hover:bg-red-800 text-red-300 transition-colors"
        >
          {t(K.externalSkill.remove)}
        </button>
        {skill.high_privilege && (
          <select
            data-testid={`policy-${skill.name}`}
            value={policy}
            onChange={e => onPolicy(e.target.value)}
            className="bg-gray-900 text-gray-300 text-xs rounded-lg px-2 py-1 outline-none"
          >
            <option value="allow">{t(K.externalSkill.policyAllow)}</option>
            <option value="ask">{t(K.externalSkill.policyAsk)}</option>
            <option value="deny">{t(K.externalSkill.policyDeny)}</option>
          </select>
        )}
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
