import { useState, useCallback, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { K } from '../i18n/keys'

const COLORS = ['#6366f1', '#8b5cf6', '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#ec4899']

const PROVIDER_LABELS_KEYS = {
  deepseek: 'DeepSeek',
  openai: 'OpenAI',
  claude: 'Claude',
  minimax: 'MiniMax',
  zhipu: 'Zhipu GLM',
  qwen: 'Qwen',
  ollama: null, // rendered with t(K.member.providerOllamaLocal)
}

const PROVIDER_MODELS = {
  deepseek: ['deepseek-chat', 'deepseek-reasoner'],
  openai: ['gpt-4o', 'gpt-4o-mini', 'gpt-3.5-turbo'],
  claude: ['claude-opus-4-7', 'claude-sonnet-4-6', 'claude-haiku-4-5-20251001'],
  minimax: ['abab6.5s-chat', 'abab6.5g-chat', 'abab6.5-chat'],
  zhipu: ['glm-4', 'glm-4-flash', 'glm-4-air', 'glm-3-turbo'],
  qwen: ['qwen-turbo', 'qwen-plus', 'qwen-max', 'qwen-long'],
  ollama: null,
}

const PERSONALITY_DIM_KEYS = [
  { key: 'challenge',     labelKey: K.member.personalityDimChallenge,     hintKey: K.member.personalityDimChallengeHint },
  { key: 'proactivity',   labelKey: K.member.personalityDimProactivity,   hintKey: K.member.personalityDimProactivityHint },
  { key: 'expression',    labelKey: K.member.personalityDimExpression,    hintKey: K.member.personalityDimExpressionHint },
  { key: 'detail',        labelKey: K.member.personalityDimDetail,        hintKey: K.member.personalityDimDetailHint },
  { key: 'collaboration', labelKey: K.member.personalityDimCollaboration, hintKey: K.member.personalityDimCollaborationHint },
]

// Keep hardcoded texts for prompt generation (these are inserted into AI prompts, not UI)
const PERSONALITY_DIMS_TEXTS = [
  {
    key: 'challenge',
    texts: [
      '接受指令，执行为主，不主动质疑。',
      '适时提出疑问，平衡执行与独立思考。',
      '主动挑战不合理的假设和决策，提出替代方案。',
    ],
  },
  {
    key: 'proactivity',
    texts: [
      '等待明确指令后再行动，不超出任务范围。',
      '在任务范围内适当补充相关背景信息。',
      '主动预判下一步需求，提前提供建议和相关信息。',
    ],
  },
  {
    key: 'expression',
    texts: [
      '语言正式、简洁，避免口语化和情绪词汇。',
      '语言自然，兼顾专业性与可读性。',
      '语言口语化，带个人色彩，可使用情绪化表达。',
    ],
  },
  {
    key: 'detail',
    texts: [
      '只给结论，省略推理过程和边界条件。',
      '关键处简要说明理由。',
      '详细阐述推理过程，列出边界条件和潜在风险。',
    ],
  },
  {
    key: 'collaboration',
    texts: [
      '独立完成任务，专注自身职责范围。',
      '必要时与他人协调，不主动发起。',
      '主动与队友互动，频繁提及他人工作，寻求共识。',
    ],
  },
]

const DEFAULT_SLIDERS = Object.fromEntries(PERSONALITY_DIM_KEYS.map(d => [d.key, 0.5]))

function generatePersonalityPrompt(sliders) {
  return PERSONALITY_DIMS_TEXTS.map(({ key, texts }) => {
    const v = sliders[key] ?? 0.5
    const idx = v < 0.35 ? 0 : v < 0.68 ? 1 : 2
    return texts[idx]
  }).join('\n')
}

const CRON_PRESET_KEYS = [
  { labelKey: K.member.cronPresetWeekday9,  value: '0 9 * * 1-5'  },
  { labelKey: K.member.cronPresetDaily9,    value: '0 9 * * *'    },
  { labelKey: K.member.cronPresetMidnight,  value: '0 0 * * *'    },
  { labelKey: K.member.cronPresetHourly,    value: '0 * * * *'    },
  { labelKey: K.member.cronPreset30min,     value: '*/30 * * * *'  },
  { labelKey: K.member.cronPresetCustom,    value: '__custom__'   },
]

const CRON_READABLE_KEYS = {
  '0 9 * * 1-5':  K.member.cronPresetWeekday9,
  '0 9 * * *':    K.member.cronPresetDaily9,
  '0 0 * * *':    K.member.cronPresetMidnight,
  '0 * * * *':    K.member.cronPresetHourly,
  '*/30 * * * *': K.member.cronPreset30min,
}

const INIT_FORM = {
  name: '', type: 'human', role: '', system_prompt: '', avatar_color: '#6366f1',
  model_provider: 'deepseek', model_name: 'deepseek-chat',
  temperature: 0.7, max_tokens: 4096,
  personality_prompt: '',
  executor_id: 'simple_v1', executor_config: { max_iterations: 100 },
  done_keyword: '',
  traits: [],
}

const AVAILABLE_TRAIT_KEYS = [
  { id: 'python_dev',     labelKey: K.member.traitPythonDev,     descKey: K.member.traitPythonDevDesc },
  { id: 'jira_reader',   labelKey: K.member.traitJiraReader,    descKey: K.member.traitJiraReaderDesc },
  { id: 'git_ops',       labelKey: K.member.traitGitOps,        descKey: K.member.traitGitOpsDesc },
  { id: 'code_reviewer', labelKey: K.member.traitCodeReviewer,  descKey: K.member.traitCodeReviewerDesc },
  { id: 'frontend_dev',  labelKey: K.member.traitFrontendDev,   descKey: K.member.traitFrontendDevDesc },
  { id: 'test_writer',   labelKey: K.member.traitTestWriter,    descKey: K.member.traitTestWriterDesc },
]

const DEFAULT_BOOTSTRAP = `# BOOTSTRAP.md

_You just woke up. Time to figure out who you are._

There is no memory yet. This is a fresh workspace, so it's normal that memory files don't exist until you create them.

## The Conversation

Don't interrogate. Don't be robotic. Just... talk.

Start with something like:

> "Hey. I just came online. Who am I? Who are you?"

Then figure out together:

1. **Your name** — What should they call you?
2. **Your nature** — What kind of creature are you? (AI assistant is fine, but maybe you're something weirder)
3. **Your vibe** — Formal? Casual? Snarky? Warm? What feels right?
4. **Your emoji** — Everyone needs a signature.

Offer suggestions if they're stuck. Have fun with it.

## After You Know Who You Are

Update these files with what you learned:

- \`IDENTITY.md\` — your name, creature, vibe, emoji
- \`USER.md\` — their name, how to address them, timezone, notes

Then open \`SOUL.md\` together and talk about:

- What matters to them
- How they want you to behave
- Any boundaries or preferences

Write it down. Make it real.

## When You're Done

Delete this file. You don't need a bootstrap script anymore — you're you now.

---

_Good luck out there. Make it count._
`

export default function MemberList({ onAddMember, onEditMember, onClose, initialData }) {
  const { t } = useTranslation()
  const isEdit = !!initialData
  const [form, setForm] = useState(() => {
    const base = isEdit ? { ...INIT_FORM, ...initialData } : INIT_FORM
    if (base.executor_config) {
      base.executor_config = {
        max_iterations: 100,
        ...base.executor_config
      }
    }
    return base
  })
  const [sliders, setSliders] = useState(DEFAULT_SLIDERS)
  const [plugins, setPlugins] = useState([])
  const [bootstrap, setBootstrap] = useState('')
  const [bootstrapLoaded, setBootstrapLoaded] = useState(false)
  const [agent, setAgent] = useState('')
  const [agentLoaded, setAgentLoaded] = useState(false)
  const [permRules, setPermRules] = useState([])
  const [newRule, setNewRule] = useState({ tool_pattern: '', args_pattern: '', action: 'allow' })
  const [cronJobs, setCronJobs] = useState([])
  const [newCron, setNewCron] = useState({ cron_expr: '0 9 * * 1-5', message: '', label: '', enabled: true })
  const [cronPreset, setCronPreset] = useState('0 9 * * 1-5')
  const [cronCustom, setCronCustom] = useState(false)
  const [cronErr, setCronErr] = useState('')
  const [cronRunning, setCronRunning] = useState(null)

  useEffect(() => {
    fetch('/api/plugins').then(r => r.json()).then(setPlugins).catch(() => {})
  }, [])

  useEffect(() => {
    if (isEdit && initialData?.id && form.type === 'bot') {
      fetch(`/api/cron-jobs?bot_id=${initialData.id}`)
        .then(r => r.ok ? r.json() : { jobs: [] })
        .then(d => setCronJobs(d.jobs || []))
        .catch(() => {})
    }
  }, [isEdit, initialData?.id, form.type])

  useEffect(() => {
    if (isEdit && initialData?.id && form.type === 'bot') {
      fetch(`/api/members/${initialData.id}/permissions`)
        .then(r => r.ok ? r.json() : [])
        .then(setPermRules)
        .catch(() => {})
    }
  }, [isEdit, initialData?.id, form.type])

  const addPermRule = async () => {
    if (!newRule.tool_pattern.trim()) return
    const res = await fetch(`/api/members/${initialData.id}/permissions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(newRule),
    })
    if (res.ok) {
      const { id } = await res.json()
      setPermRules(prev => [...prev, { ...newRule, id }])
      setNewRule({ tool_pattern: '', args_pattern: '', action: 'allow' })
    }
  }

  const deletePermRule = async (ruleId) => {
    await fetch(`/api/members/${initialData.id}/permissions/${ruleId}`, { method: 'DELETE' })
    setPermRules(prev => prev.filter(r => r.id !== ruleId))
  }

  const permMode = form.executor_config?.permission_mode || 'default'
  const setPermMode = (val) => setField({ executor_config: { ...form.executor_config, permission_mode: val } })

  useEffect(() => {
    if (isEdit && initialData?.id && form.type === 'bot') {
      fetch(`/api/members/${initialData.id}/workspace/file?path=BOOTSTRAP.md`)
        .then(r => r.ok ? r.json() : null)
        .then(data => { setBootstrap(data ? data.content : DEFAULT_BOOTSTRAP); setBootstrapLoaded(true) })
        .catch(() => { setBootstrap(DEFAULT_BOOTSTRAP); setBootstrapLoaded(true) })
      fetch(`/api/members/${initialData.id}/workspace/file?path=AGENT.md`)
        .then(r => r.ok ? r.json() : null)
        .then(data => { setAgent(data ? data.content : ''); setAgentLoaded(true) })
        .catch(() => { setAgent(''); setAgentLoaded(true) })
    } else if (!isEdit) {
      setBootstrap(DEFAULT_BOOTSTRAP)
      setBootstrapLoaded(true)
      setAgent('')
      setAgentLoaded(true)
    }
  }, [isEdit, initialData?.id, form.type])

  const setField = (patch) => setForm(f => ({ ...f, ...patch }))

  const handleSliderChange = useCallback((key, val) => {
    const next = { ...sliders, [key]: val }
    setSliders(next)
    setField({ personality_prompt: generatePersonalityPrompt(next) })
  }, [sliders])

  const handleCronPresetChange = (value) => {
    setCronPreset(value)
    if (value === '__custom__') {
      setCronCustom(true)
    } else {
      setCronCustom(false)
      setNewCron(c => ({ ...c, cron_expr: value }))
    }
  }

  const addCronJob = async () => {
    if (!newCron.message.trim()) return
    setCronErr('')
    const res = await fetch('/api/cron-jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...newCron, bot_id: initialData.id, group_id: initialData.group_id }),
    })
    if (res.ok) {
      const job = await res.json()
      setCronJobs(prev => [...prev, job])
      setNewCron({ cron_expr: '0 9 * * 1-5', message: '', label: '', enabled: true })
      setCronPreset('0 9 * * 1-5')
      setCronCustom(false)
    } else {
      try { setCronErr((await res.json()).detail || t(K.member.cronCreateFailed)) } catch { setCronErr(t(K.member.cronCreateFailed)) }
    }
  }

  const toggleCronJob = async (jobId) => {
    const res = await fetch(`/api/cron-jobs/${jobId}/toggle`, { method: 'POST' })
    if (res.ok) {
      const updated = await res.json()
      setCronJobs(prev => prev.map(j => j.id === jobId ? updated : j))
    }
  }

  const deleteCronJob = async (jobId) => {
    await fetch(`/api/cron-jobs/${jobId}`, { method: 'DELETE' })
    setCronJobs(prev => prev.filter(j => j.id !== jobId))
  }

  const runCronJobNow = async (jobId) => {
    setCronRunning(jobId)
    await fetch(`/api/cron-jobs/${jobId}/run`, { method: 'POST' }).catch(() => {})
    setTimeout(() => setCronRunning(r => r === jobId ? null : r), 1500)
  }

  const handleProviderChange = (provider) => {
    const models = PROVIDER_MODELS[provider]
    setField({ model_provider: provider, model_name: models ? models[0] : '' })
  }

  const handleSubmit = async () => {
    if (!form.name.trim()) return
    if (isEdit) {
      await onEditMember(initialData.id, form)
      if (bootstrapLoaded) {
        await fetch(`/api/members/${initialData.id}/workspace/file`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: 'BOOTSTRAP.md', content: bootstrap }),
        })
      }
      if (agentLoaded && agent) {
        await fetch(`/api/members/${initialData.id}/workspace/file`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: 'AGENT.md', content: agent }),
        })
      }
    } else {
      const result = await onAddMember({ ...form, bootstrap_content: bootstrap })
      // onAddMember returns the new bot id; write bootstrap if we have it
      const newId = result?.id
      if (newId) {
        if (bootstrap) await fetch(`/api/members/${newId}/workspace/file`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: 'BOOTSTRAP.md', content: bootstrap }),
        })
        if (agent) await fetch(`/api/members/${newId}/workspace/file`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: 'AGENT.md', content: agent }),
        })
      }
    }
    onClose()
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-gray-800 rounded-2xl p-6 w-[600px] max-w-[95vw] shadow-xl max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-white font-semibold mb-4">{isEdit ? t(K.member.editMember) : t(K.member.addMember)}</h2>

        <div className="space-y-3">
          <div>
            <label className="text-xs text-gray-400 mb-1 block">{t(K.member.name)}</label>
            <input
              className="w-full bg-gray-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500"
              placeholder={t(K.member.namePlaceholder)}
              value={form.name}
              onChange={(e) => setField({ name: e.target.value })}
              autoFocus
            />
          </div>

          {!isEdit && (
            <div>
              <label className="text-xs text-gray-400 mb-1 block">{t(K.member.type)}</label>
              <div className="flex gap-2">
                {['human', 'bot'].map((tp) => (
                  <button
                    key={tp}
                    onClick={() => setField({ type: tp })}
                    className={`flex-1 py-1.5 rounded-lg text-sm transition-colors ${form.type === tp ? 'bg-indigo-600 text-white' : 'bg-gray-700 text-gray-400 hover:bg-gray-600'}`}
                  >
                    {tp === 'human' ? t(K.member.typeHumanLabel) : t(K.member.typeBotLabel)}
                  </button>
                ))}
              </div>
            </div>
          )}

          {form.type === 'bot' && (
            <>
              <div>
                <label className="text-xs text-gray-400 mb-1 block">{t(K.member.role)}</label>
                <input
                  className="w-full bg-gray-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                  placeholder={t(K.member.rolePlaceholder2)}
                  value={form.role}
                  onChange={(e) => setField({ role: e.target.value })}
                />
              </div>
              <div>
                <label className="text-xs text-gray-400 mb-1 block">{t(K.member.systemPromptLabel)}</label>
                <textarea
                  className="w-full bg-gray-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500 resize-none"
                  placeholder={t(K.member.systemPromptPlaceholder2)}
                  rows={3}
                  value={form.system_prompt}
                  onChange={(e) => setField({ system_prompt: e.target.value })}
                />
              </div>

              <div className="space-y-1">
                <label className="text-xs text-indigo-300 block">{t(K.member.traits)}</label>
                <div className="flex flex-wrap gap-2 mt-1">
                  {AVAILABLE_TRAIT_KEYS.map(trait => (
                    <label key={trait.id} className={`flex items-center gap-2 text-sm px-3 py-1.5 rounded cursor-pointer transition-colors border ${form.traits?.includes(trait.id) ? 'bg-indigo-600/40 border-indigo-400 text-indigo-100' : 'bg-gray-800/40 border-gray-600 text-gray-400 hover:bg-gray-700/50'}`}>
                      <input type="checkbox" checked={form.traits?.includes(trait.id)} onChange={e => {
                        const newTraits = e.target.checked ? [...(form.traits || []), trait.id] : (form.traits || []).filter(tr => tr !== trait.id)
                        setField({ traits: newTraits })
                      }} className="hidden" />
                      <span title={t(trait.descKey)}>{t(trait.labelKey)}</span>
                    </label>
                  ))}
                </div>
                <p className="text-[10px] text-gray-500 mt-1">{t(K.member.traitsHelper)}</p>
              </div>

              <div>
                <label className="text-xs text-gray-400 mb-1 block">{t(K.member.provider)}</label>
                <div className="grid grid-cols-2 gap-1.5">
                  {Object.entries(PROVIDER_LABELS_KEYS).map(([key, label]) => (
                    <button
                      key={key}
                      onClick={() => handleProviderChange(key)}
                      className={`py-1.5 rounded-lg text-xs transition-colors ${form.model_provider === key ? 'bg-indigo-600 text-white' : 'bg-gray-700 text-gray-400 hover:bg-gray-600'}`}
                    >
                      {label !== null ? label : t(K.member.providerOllamaLocal)}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="text-xs text-gray-400 mb-1 block">{t(K.member.model)}</label>
                {PROVIDER_MODELS[form.model_provider] ? (
                  <select
                    className="w-full bg-gray-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                    value={form.model_name}
                    onChange={(e) => setField({ model_name: e.target.value })}
                  >
                    {PROVIDER_MODELS[form.model_provider].map(m => (
                      <option key={m} value={m}>{m}</option>
                    ))}
                  </select>
                ) : (
                  <input
                    className="w-full bg-gray-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                    placeholder={t(K.member.ollamaModelPlaceholder)}
                    value={form.model_name}
                    onChange={(e) => setField({ model_name: e.target.value })}
                  />
                )}
              </div>

              <div>
                <label className="text-xs text-gray-400 mb-1 block">
                  {t(K.member.temperature)}<span className="text-indigo-400 ml-1">{form.temperature}</span>
                </label>
                <input
                  type="range" min="0" max="2" step="0.1"
                  className="w-full accent-indigo-500"
                  value={form.temperature}
                  onChange={(e) => setField({ temperature: parseFloat(e.target.value) })}
                />
                <div className="flex justify-between text-xs text-gray-500 mt-0.5">
                  <span>{t(K.member.temperatureLow)}</span><span>{t(K.member.temperatureMid)}</span><span>{t(K.member.temperatureHigh)}</span>
                </div>
              </div>

              <div>
                <label className="text-xs text-gray-400 mb-1 block">{t(K.member.maxTokensLabel)}</label>
                <input
                  type="number" min="256" max="32768" step="256"
                  className="w-full bg-gray-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                  value={form.max_tokens}
                  onChange={(e) => setField({ max_tokens: parseInt(e.target.value) || 4096 })}
                />
              </div>

              <div>
                <label className="text-xs text-gray-400 mb-1 block">{t(K.member.maxIterationsLabel)}</label>
                <input
                  type="number" min="1" max="1000" step="1"
                  className="w-full bg-gray-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                  value={form.executor_config?.max_iterations ?? 100}
                  onChange={(e) => setField({
                    executor_config: {
                      ...form.executor_config,
                      max_iterations: parseInt(e.target.value) || 100
                    }
                  })}
                />
              </div>

              <div className="border-t border-gray-700 pt-3">
                <label className="text-xs text-gray-400 mb-2 block font-medium">{t(K.member.personalityConfig)}</label>
                <div className="space-y-2 mb-3">
                  {PERSONALITY_DIM_KEYS.map(({ key, labelKey, hintKey }) => (
                    <div key={key}>
                      <div className="flex justify-between text-xs text-gray-500 mb-0.5">
                        <span className="text-gray-300">{t(labelKey)}</span>
                        <span>{t(hintKey)}</span>
                      </div>
                      <input
                        type="range" min="0" max="1" step="0.01"
                        className="w-full accent-indigo-500"
                        value={sliders[key]}
                        onChange={(e) => handleSliderChange(key, parseFloat(e.target.value))}
                      />
                    </div>
                  ))}
                </div>
                <label className="text-xs text-gray-400 mb-1 block">{t(K.member.personalityGenerated)}</label>
                <textarea
                  className="w-full bg-gray-900 text-gray-200 rounded-lg px-3 py-2 text-xs outline-none focus:ring-2 focus:ring-indigo-500 resize-none"
                  placeholder={t(K.member.personalityPlaceholder)}
                  rows={5}
                  value={form.personality_prompt || ''}
                  onChange={(e) => setField({ personality_prompt: e.target.value })}
                />
              </div>

              <div className="border-t border-gray-700 pt-3">
                <label className="text-xs text-gray-400 mb-1 block">{t(K.member.doneKeyword)}</label>
                <input
                  className="w-full bg-gray-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                  placeholder={t(K.member.doneKeywordPlaceholder)}
                  value={form.done_keyword || ''}
                  onChange={(e) => setField({ done_keyword: e.target.value })}
                />
              </div>

              <div className="border-t border-gray-700 pt-3">
                <label className="text-xs text-gray-400 mb-1 block">{t(K.member.bootstrap)}
                  <span className="text-gray-600 ml-1">{t(K.member.bootstrapSubtitle)}</span>
                </label>
                <textarea
                  className="w-full bg-gray-900 text-gray-200 rounded-lg px-3 py-2 text-xs font-mono outline-none focus:ring-2 focus:ring-indigo-500 resize-none"
                  placeholder={t(K.member.bootstrapPlaceholder)}
                  rows={12}
                  value={bootstrap}
                  onChange={(e) => setBootstrap(e.target.value)}
                />
              </div>

              <div className="border-t border-gray-700 pt-3">
                <label className="text-xs text-gray-400 mb-1 block">{t(K.member.agentFramework)}
                  <span className="text-gray-600 ml-1">{t(K.member.agentFrameworkSubtitle)}</span>
                </label>
                <textarea
                  className="w-full bg-gray-900 text-gray-200 rounded-lg px-3 py-2 text-xs font-mono outline-none focus:ring-2 focus:ring-indigo-500 resize-none"
                  placeholder={t(K.member.agentFrameworkPlaceholder)}
                  rows={12}
                  value={agent}
                  onChange={(e) => setAgent(e.target.value)}
                />
              </div>

              <div className="border-t border-gray-700 pt-3">
                <label className="text-xs text-gray-400 mb-2 block font-medium">{t(K.member.permissionMode)}</label>
                <div className="flex gap-2">
                  {[
                    { val: 'default',            labelKey: K.member.permModeDefault,    descKey: K.member.permModeDefaultDesc },
                    { val: 'bypassPermissions',  labelKey: K.member.permModeAllow,      descKey: K.member.permModeAllowDesc },
                    { val: 'dontAsk',            labelKey: K.member.permModeDeny,       descKey: K.member.permModeDenyDesc },
                  ].map(opt => (
                    <button
                      key={opt.val}
                      onClick={() => setPermMode(opt.val)}
                      title={t(opt.descKey)}
                      className={`flex-1 text-xs py-1.5 rounded-lg border transition-colors ${
                        permMode === opt.val
                          ? 'border-indigo-500 bg-indigo-600/20 text-indigo-300'
                          : 'border-gray-700 bg-gray-900 text-gray-400 hover:border-gray-600'
                      }`}
                    >
                      {t(opt.labelKey)}
                    </button>
                  ))}
                </div>
              </div>

              {isEdit && (
                <div className="border-t border-gray-700 pt-3">
                  <label className="text-xs text-gray-400 mb-2 block font-medium">{t(K.member.permissionRules)}</label>
                  <div className="space-y-1 mb-2">
                    {permRules.length === 0 && (
                      <p className="text-xs text-gray-600 italic">{t(K.member.permissionRulesEmpty)}</p>
                    )}
                    {permRules.map(rule => (
                      <div key={rule.id} className="flex items-center gap-2 bg-gray-900 rounded-lg px-2 py-1.5">
                        <span className={`text-xs px-1.5 py-0.5 rounded font-mono ${rule.action === 'allow' ? 'bg-green-900/40 text-green-400' : 'bg-red-900/40 text-red-400'}`}>
                          {rule.action}
                        </span>
                        <span className="text-xs font-mono text-gray-300 flex-1 truncate">{rule.tool_pattern}</span>
                        {rule.args_pattern && (
                          <span className="text-xs font-mono text-gray-500 truncate max-w-[80px]">({rule.args_pattern})</span>
                        )}
                        <button onClick={() => deletePermRule(rule.id)} className="text-gray-600 hover:text-red-400 text-xs transition-colors ml-auto">✕</button>
                      </div>
                    ))}
                  </div>
                  <div className="flex gap-1.5">
                    <input
                      className="flex-1 bg-gray-900 text-gray-200 rounded-lg px-2 py-1.5 text-xs font-mono outline-none focus:ring-1 focus:ring-indigo-500"
                      placeholder={t(K.member.permRuleToolPlaceholder)}
                      value={newRule.tool_pattern}
                      onChange={e => setNewRule(r => ({ ...r, tool_pattern: e.target.value }))}
                      onKeyDown={e => e.key === 'Enter' && addPermRule()}
                    />
                    <select
                      className="bg-gray-900 text-gray-300 rounded-lg px-2 py-1.5 text-xs outline-none focus:ring-1 focus:ring-indigo-500"
                      value={newRule.action}
                      onChange={e => setNewRule(r => ({ ...r, action: e.target.value }))}
                    >
                      <option value="allow">allow</option>
                      <option value="deny">deny</option>
                    </select>
                    <button onClick={addPermRule} className="bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg px-3 py-1.5 text-xs transition-colors">+</button>
                  </div>
                </div>
              )}

              {plugins.length > 0 && (
                <div className="border-t border-gray-700 pt-3">
                  <label className="text-xs text-gray-400 mb-2 block font-medium">{t(K.member.executionEngine)}</label>
                  <div className="space-y-1.5">
                    {plugins.map(p => (
                      <button
                        key={p.executor_id}
                        onClick={() => setField({ executor_id: p.executor_id })}
                        className={`w-full text-left px-3 py-2 rounded-lg border transition-colors ${
                          form.executor_id === p.executor_id
                            ? 'border-indigo-500 bg-indigo-600/20'
                            : 'border-gray-700 bg-gray-900 hover:border-gray-600'
                        }`}
                      >
                        <div className="flex items-center gap-2">
                          <span className={`w-2 h-2 rounded-full flex-shrink-0 ${form.executor_id === p.executor_id ? 'bg-indigo-400' : 'bg-gray-600'}`} />
                          <span className="text-xs text-white font-medium">{p.display_name}</span>
                          <span className="text-xs text-gray-500 ml-auto">{p.executor_id}</span>
                        </div>
                        <p className="text-xs text-gray-400 mt-0.5 ml-4">{p.manifest.description}</p>
                        {p.manifest.tools.length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-1 ml-4">
                            {p.manifest.tools.map(tool => (
                              <span key={tool.name} className="text-xs bg-gray-700 text-gray-300 px-1.5 py-0.5 rounded">{tool.name}</span>
                            ))}
                          </div>
                        )}
                        <div className="flex flex-wrap gap-1 mt-1 ml-4">
                          {p.manifest.memory_layers.map(l => (
                            <span key={l} className="text-xs bg-blue-900/40 text-blue-300 px-1.5 py-0.5 rounded">{l}</span>
                          ))}
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {isEdit && (
                <div className="border-t border-gray-700 pt-3">
                  <label className="text-xs text-gray-400 mb-2 block font-medium">⏰ {t(K.member.cronJobs)}</label>

                  {/* existing jobs list */}
                  <div className="space-y-1.5 mb-3">
                    {cronJobs.length === 0 && (
                      <p className="text-xs text-gray-600 italic">{t(K.member.cronJobsEmpty)}</p>
                    )}
                    {cronJobs.map(job => (
                      <div key={job.id} className="flex items-center gap-2 bg-gray-900 rounded-lg px-2.5 py-2">
                        <button
                          onClick={() => toggleCronJob(job.id)}
                          title={job.enabled ? t(K.member.cronDisable) : t(K.member.cronEnable)}
                          className={`w-2 h-2 rounded-full flex-shrink-0 transition-colors ${job.enabled ? 'bg-green-400' : 'bg-gray-600'}`}
                        />
                        <div className="flex-1 min-w-0">
                          {job.label && (
                            <span className="text-xs text-indigo-300 font-medium mr-1.5">{job.label}</span>
                          )}
                          <span className="text-xs font-mono text-gray-400">
                            {CRON_READABLE_KEYS[job.cron_expr] ? t(CRON_READABLE_KEYS[job.cron_expr]) : job.cron_expr}
                          </span>
                          <p className="text-xs text-gray-300 truncate mt-0.5">{job.message}</p>
                        </div>
                        <button
                          onClick={() => runCronJobNow(job.id)}
                          title={t(K.member.cronRunNow)}
                          className={`text-xs px-1.5 py-0.5 rounded transition-colors ${
                            cronRunning === job.id
                              ? 'text-yellow-400'
                              : 'text-gray-500 hover:text-indigo-400'
                          }`}
                        >
                          {cronRunning === job.id ? '…' : '▶'}
                        </button>
                        <button
                          onClick={() => deleteCronJob(job.id)}
                          className="text-gray-600 hover:text-red-400 text-xs transition-colors"
                        >✕</button>
                      </div>
                    ))}
                  </div>

                  {/* add new job form */}
                  <div className="bg-gray-900 rounded-lg p-2.5 space-y-2">
                    <div className="flex gap-1.5">
                      <input
                        className="flex-1 bg-gray-800 text-gray-200 rounded-lg px-2 py-1.5 text-xs outline-none focus:ring-1 focus:ring-indigo-500"
                        placeholder={t(K.member.cronLabelPlaceholder)}
                        value={newCron.label}
                        onChange={e => setNewCron(c => ({ ...c, label: e.target.value }))}
                      />
                    </div>

                    <div className="flex gap-1.5">
                      <select
                        className="bg-gray-800 text-gray-300 rounded-lg px-2 py-1.5 text-xs outline-none focus:ring-1 focus:ring-indigo-500 flex-shrink-0"
                        value={cronPreset}
                        onChange={e => handleCronPresetChange(e.target.value)}
                      >
                        {CRON_PRESET_KEYS.map(p => (
                          <option key={p.value} value={p.value}>{t(p.labelKey)}</option>
                        ))}
                      </select>
                      {cronCustom && (
                        <input
                          className="flex-1 bg-gray-800 text-gray-200 rounded-lg px-2 py-1.5 text-xs font-mono outline-none focus:ring-1 focus:ring-indigo-500"
                          placeholder={t(K.member.cronCustomPlaceholder)}
                          value={newCron.cron_expr}
                          onChange={e => setNewCron(c => ({ ...c, cron_expr: e.target.value }))}
                        />
                      )}
                    </div>

                    <div className="flex gap-1.5">
                      <input
                        className="flex-1 bg-gray-800 text-gray-200 rounded-lg px-2 py-1.5 text-xs outline-none focus:ring-1 focus:ring-indigo-500"
                        placeholder={t(K.member.cronMessagePlaceholder)}
                        value={newCron.message}
                        onChange={e => setNewCron(c => ({ ...c, message: e.target.value }))}
                        onKeyDown={e => e.key === 'Enter' && addCronJob()}
                      />
                      <button
                        onClick={addCronJob}
                        className="bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg px-3 py-1.5 text-xs transition-colors flex-shrink-0"
                      >{t(K.common.add)}</button>
                    </div>

                    {cronErr && (
                      <p className="text-xs text-red-400">{cronErr}</p>
                    )}
                  </div>
                </div>
              )}
            </>
          )}

          <div>
            <label className="text-xs text-gray-400 mb-1 block">{t(K.member.avatarColor)}</label>
            <div className="flex gap-2">
              {COLORS.map((c) => (
                <button
                  key={c}
                  onClick={() => setField({ avatar_color: c })}
                  className={`w-6 h-6 rounded-full transition-transform ${form.avatar_color === c ? 'scale-125 ring-2 ring-white' : ''}`}
                  style={{ backgroundColor: c }}
                />
              ))}
            </div>
          </div>
        </div>

        <div className="flex gap-2 mt-5">
          <button onClick={handleSubmit} className="flex-1 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg py-2 text-sm font-medium transition-colors">{isEdit ? t(K.member.save) : t(K.common.add)}</button>
          <button onClick={onClose} className="flex-1 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded-lg py-2 text-sm transition-colors">{t(K.member.cancel)}</button>
        </div>
      </div>
    </div>
  )
}
