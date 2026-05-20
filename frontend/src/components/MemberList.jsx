import { useState } from 'react'

const COLORS = ['#6366f1', '#8b5cf6', '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#ec4899']

const PROVIDER_LABELS = {
  deepseek: 'DeepSeek',
  openai: 'OpenAI',
  claude: 'Claude',
  ollama: 'Ollama (本地)',
}

const PROVIDER_MODELS = {
  deepseek: ['deepseek-chat', 'deepseek-reasoner'],
  openai: ['gpt-4o', 'gpt-4o-mini', 'gpt-3.5-turbo'],
  claude: ['claude-opus-4-7', 'claude-sonnet-4-6', 'claude-haiku-4-5-20251001'],
  ollama: null,
}

const INIT_FORM = {
  name: '', type: 'human', role: '', system_prompt: '', avatar_color: '#6366f1',
  model_provider: 'deepseek', model_name: 'deepseek-chat',
}

export default function MemberList({ onAddMember, onEditMember, onClose, initialData }) {
  const isEdit = !!initialData
  const [form, setForm] = useState(isEdit ? { ...INIT_FORM, ...initialData } : INIT_FORM)

  const setField = (patch) => setForm(f => ({ ...f, ...patch }))

  const handleProviderChange = (provider) => {
    const models = PROVIDER_MODELS[provider]
    setField({ model_provider: provider, model_name: models ? models[0] : '' })
  }

  const handleSubmit = async () => {
    if (!form.name.trim()) return
    if (isEdit) {
      await onEditMember(initialData.id, form)
    } else {
      await onAddMember(form)
    }
    onClose()
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-gray-800 rounded-2xl p-6 w-80 shadow-xl max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-white font-semibold mb-4">{isEdit ? '编辑成员' : '添加成员'}</h2>

        <div className="space-y-3">
          <div>
            <label className="text-xs text-gray-400 mb-1 block">名字</label>
            <input
              className="w-full bg-gray-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500"
              placeholder="成员名字"
              value={form.name}
              onChange={(e) => setField({ name: e.target.value })}
              autoFocus
            />
          </div>

          {!isEdit && (
            <div>
              <label className="text-xs text-gray-400 mb-1 block">类型</label>
              <div className="flex gap-2">
                {['human', 'bot'].map((t) => (
                  <button
                    key={t}
                    onClick={() => setField({ type: t })}
                    className={`flex-1 py-1.5 rounded-lg text-sm transition-colors ${form.type === t ? 'bg-indigo-600 text-white' : 'bg-gray-700 text-gray-400 hover:bg-gray-600'}`}
                  >
                    {t === 'human' ? '👤 真人' : '🤖 AI 角色'}
                  </button>
                ))}
              </div>
            </div>
          )}

          {form.type === 'bot' && (
            <>
              <div>
                <label className="text-xs text-gray-400 mb-1 block">角色</label>
                <input
                  className="w-full bg-gray-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                  placeholder="如：产品经理、架构师..."
                  value={form.role}
                  onChange={(e) => setField({ role: e.target.value })}
                />
              </div>
              <div>
                <label className="text-xs text-gray-400 mb-1 block">角色描述（System Prompt）</label>
                <textarea
                  className="w-full bg-gray-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500 resize-none"
                  placeholder="描述这个 AI 角色的职责和行为..."
                  rows={3}
                  value={form.system_prompt}
                  onChange={(e) => setField({ system_prompt: e.target.value })}
                />
              </div>

              <div>
                <label className="text-xs text-gray-400 mb-1 block">模型提供商</label>
                <div className="grid grid-cols-2 gap-1.5">
                  {Object.entries(PROVIDER_LABELS).map(([key, label]) => (
                    <button
                      key={key}
                      onClick={() => handleProviderChange(key)}
                      className={`py-1.5 rounded-lg text-xs transition-colors ${form.model_provider === key ? 'bg-indigo-600 text-white' : 'bg-gray-700 text-gray-400 hover:bg-gray-600'}`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="text-xs text-gray-400 mb-1 block">模型</label>
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
                    placeholder="如：llama3, qwen2, mistral..."
                    value={form.model_name}
                    onChange={(e) => setField({ model_name: e.target.value })}
                  />
                )}
              </div>
            </>
          )}

          <div>
            <label className="text-xs text-gray-400 mb-1 block">头像颜色</label>
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
          <button onClick={handleSubmit} className="flex-1 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg py-2 text-sm font-medium transition-colors">{isEdit ? '保存' : '添加'}</button>
          <button onClick={onClose} className="flex-1 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded-lg py-2 text-sm transition-colors">取消</button>
        </div>
      </div>
    </div>
  )
}
