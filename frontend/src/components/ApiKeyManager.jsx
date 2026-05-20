import { useState, useEffect } from 'react'
import { fetchConfig, saveConfig } from '../api'

const PROVIDERS = [
  {
    key: 'deepseek_api_key',
    label: 'DeepSeek',
    hint: 'sk-...',
    url: 'https://platform.deepseek.com',
    masked: true,
  },
  {
    key: 'openai_api_key',
    label: 'OpenAI',
    hint: 'sk-...',
    url: 'https://platform.openai.com/api-keys',
    masked: true,
  },
  {
    key: 'anthropic_api_key',
    label: 'Claude (Anthropic)',
    hint: 'sk-ant-...',
    url: 'https://console.anthropic.com/settings/keys',
    masked: true,
  },
  {
    key: 'ollama_base_url',
    label: 'Ollama 地址',
    hint: 'http://localhost:11434',
    url: null,
    masked: false,
  },
]

export default function ApiKeyManager({ onClose }) {
  const [status, setStatus] = useState({})   // { key: { configured, preview } }
  const [values, setValues] = useState({})   // { key: inputValue }
  const [show, setShow] = useState({})       // { key: bool }
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    fetchConfig().then(data => {
      setStatus(data)
      const init = {}
      PROVIDERS.forEach(p => { init[p.key] = '' })
      setValues(init)
    })
  }, [])

  const handleSave = async () => {
    setSaving(true)
    const payload = {}
    PROVIDERS.forEach(p => {
      const v = values[p.key].trim()
      if (v) payload[p.key] = v
    })
    await saveConfig(payload)
    const fresh = await fetchConfig()
    setStatus(fresh)
    const reset = {}
    PROVIDERS.forEach(p => { reset[p.key] = '' })
    setValues(reset)
    setSaving(false)
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  const handleClear = async (key) => {
    await saveConfig({ [key]: '' })
    const fresh = await fetchConfig()
    setStatus(fresh)
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-gray-800 rounded-2xl shadow-xl w-[480px] max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-700">
          <h2 className="text-white font-semibold">API Key 管理</h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300 text-lg leading-none">✕</button>
        </div>

        <div className="px-6 py-5 space-y-5">
          {PROVIDERS.map(p => {
            const st = status[p.key] || {}
            const val = values[p.key] || ''
            const isVisible = show[p.key]
            return (
              <div key={p.key}>
                <div className="flex items-center justify-between mb-1.5">
                  <label className="text-sm font-medium text-gray-200 flex items-center gap-2">
                    {p.label}
                    <span className={`w-2 h-2 rounded-full flex-shrink-0 ${st.configured ? 'bg-green-500' : 'bg-gray-600'}`} />
                  </label>
                  <div className="flex items-center gap-2 text-xs">
                    {st.configured && (
                      <>
                        <span className="text-gray-500 font-mono">{st.preview}</span>
                        <button
                          onClick={() => handleClear(p.key)}
                          className="text-red-500 hover:text-red-400 transition-colors"
                        >
                          清除
                        </button>
                      </>
                    )}
                    {p.url && (
                      <a href={p.url} target="_blank" rel="noreferrer" className="text-indigo-400 hover:text-indigo-300 transition-colors">
                        获取 →
                      </a>
                    )}
                  </div>
                </div>
                <div className="relative">
                  <input
                    type={p.masked && !isVisible ? 'password' : 'text'}
                    className="w-full bg-gray-700 text-white rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500 placeholder-gray-500 pr-10"
                    placeholder={st.configured ? `已配置 — 输入新值可替换` : p.hint}
                    value={val}
                    onChange={e => setValues(v => ({ ...v, [p.key]: e.target.value }))}
                  />
                  {p.masked && (
                    <button
                      type="button"
                      onClick={() => setShow(s => ({ ...s, [p.key]: !s[p.key] }))}
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300 text-xs"
                    >
                      {isVisible ? '隐藏' : '显示'}
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>

        <div className="px-6 pb-5 flex items-center gap-3">
          <button
            onClick={handleSave}
            disabled={saving || Object.values(values).every(v => !v.trim())}
            className="flex-1 bg-indigo-600 hover:bg-indigo-500 disabled:bg-gray-700 disabled:text-gray-500 text-white rounded-lg py-2 text-sm font-medium transition-colors"
          >
            {saving ? '保存中...' : saved ? '✓ 已保存' : '保存'}
          </button>
          <button onClick={onClose} className="px-4 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded-lg py-2 text-sm transition-colors">
            关闭
          </button>
        </div>

        <div className="px-6 pb-4 text-xs text-gray-600">
          Key 保存在服务器本地 app_config.json，不会上传到任何第三方。留空字段不会修改已有配置。
        </div>
      </div>
    </div>
  )
}
