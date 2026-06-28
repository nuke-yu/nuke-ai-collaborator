import React, { useState, useEffect } from 'react'
import { fetchMcpConfig, saveMcpConfig } from '../api'
import i18n from '../i18n'

export default function McpManager({ onClose }) {
  const [jsonText, setJsonText] = useState('{\n  "mcpServers": {}\n}')
  const [errorMsg, setErrorMsg] = useState(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  const isZh = i18n.language?.startsWith('zh')

  useEffect(() => {
    fetchMcpConfig()
      .then(data => {
        setJsonText(JSON.stringify(data || { mcpServers: {} }, null, 2))
        setErrorMsg(null)
      })
      .catch(err => {
        setErrorMsg(isZh ? '加载配置失败' : 'Failed to load configuration')
      })
  }, [isZh])

  const handleTextChange = (val) => {
    setJsonText(val)
    if (!val.trim()) {
      setErrorMsg(isZh ? '配置不能为空' : 'Configuration cannot be empty')
      return
    }
    try {
      const parsed = JSON.parse(val)
      if (typeof parsed !== 'object' || parsed === null) {
        setErrorMsg(isZh ? '根节点必须是一个 JSON 对象' : 'Root must be a JSON object')
        return
      }
      if (!parsed.hasOwnProperty('mcpServers')) {
        setErrorMsg(isZh ? '根节点必须包含 "mcpServers" 键' : 'Root must contain "mcpServers" key')
        return
      }
      setErrorMsg(null)
    } catch (e) {
      setErrorMsg(e.message)
    }
  }

  const handleInsertPreset = (name) => {
    let presetSpec = {}
    if (name === 'filesystem') {
      presetSpec = {
        command: 'npx',
        args: ['-y', '@modelcontextprotocol/server-filesystem', '/var/lib/nuke-ai-collaborator/workspaces'],
        enabled: true
      }
    } else if (name === 'github') {
      presetSpec = {
        command: 'npx',
        args: ['-y', '@modelcontextprotocol/server-github'],
        env: {
          GITHUB_PERSONAL_ACCESS_TOKEN: 'your_github_token_here'
        },
        enabled: true
      }
    } else if (name === 'sse') {
      presetSpec = {
        url: 'https://mcp.example.com/sse',
        transport: 'sse',
        enabled: true
      }
    }

    try {
      const current = JSON.parse(jsonText)
      const currentServers = current.mcpServers || {}
      currentServers[name] = presetSpec
      current.mcpServers = currentServers
      handleTextChange(JSON.stringify(current, null, 2))
    } catch (e) {
      // If current json is invalid, overwrite it with a fresh structure containing the preset
      const fresh = {
        mcpServers: {
          [name]: presetSpec
        }
      }
      handleTextChange(JSON.stringify(fresh, null, 2))
    }
  }

  const handleSave = async () => {
    if (errorMsg) return
    setSaving(true)
    setSaved(false)
    try {
      const parsed = JSON.parse(jsonText)
      await saveMcpConfig(parsed)
      setSaved(true)
      setTimeout(() => {
        setSaved(false)
        onClose()
      }, 1000)
    } catch (err) {
      setErrorMsg(err.message || (isZh ? '保存配置失败' : 'Failed to save configuration'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div 
        className="bg-gray-800 rounded-2xl shadow-xl w-full max-w-2xl max-h-[90vh] flex flex-col overflow-hidden" 
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-700">
          <div>
            <h2 className="text-white font-semibold text-lg flex items-center gap-2">
              🔌 {isZh ? 'MCP 服务器配置' : 'MCP Servers Configuration'}
            </h2>
            <p className="text-xs text-gray-400 mt-1">
              {isZh 
                ? '支持本地进程与远程 SSE 服务，保存后自动实时重载生效' 
                : 'Supports local stdio & remote SSE servers. Hot-reloaded on save.'}
            </p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-200 text-lg leading-none transition-colors">✕</button>
        </div>

        {/* Presets Bar */}
        <div className="px-6 py-3 bg-gray-900/50 border-b border-gray-700/50 flex flex-wrap items-center gap-3">
          <span className="text-xs text-gray-400 font-medium">
            {isZh ? '🚀 快速模版:' : '🚀 Presets:'}
          </span>
          <button 
            onClick={() => handleInsertPreset('filesystem')}
            className="text-xs bg-gray-700 hover:bg-gray-600 text-gray-200 px-2.5 py-1 rounded transition-colors font-mono"
          >
            + FileSystem
          </button>
          <button 
            onClick={() => handleInsertPreset('github')}
            className="text-xs bg-gray-700 hover:bg-gray-600 text-gray-200 px-2.5 py-1 rounded transition-colors font-mono"
          >
            + GitHub
          </button>
          <button 
            onClick={() => handleInsertPreset('sse')}
            className="text-xs bg-gray-700 hover:bg-gray-600 text-gray-200 px-2.5 py-1 rounded transition-colors font-mono"
          >
            + Remote SSE
          </button>
        </div>

        {/* Content Area */}
        <div className="p-6 flex-1 flex flex-col min-h-0 space-y-4">
          <div className="flex-1 flex flex-col min-h-0 relative">
            <textarea
              className="w-full flex-1 bg-gray-950 text-emerald-400 font-mono text-sm rounded-xl p-4 border border-gray-700 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 outline-none resize-none overflow-y-auto"
              value={jsonText}
              onChange={e => handleTextChange(e.target.value)}
              placeholder={'{\n  "mcpServers": {}\n}'}
              spellCheck="false"
            />
          </div>

          {/* Validation Banner */}
          <div className="text-xs font-mono flex items-center min-h-[20px]">
            {errorMsg ? (
              <span className="text-red-400 flex items-center gap-1.5">
                ⚠️ {errorMsg}
              </span>
            ) : (
              <span className="text-green-400 flex items-center gap-1.5">
                ✓ JSON格式有效 (Valid JSON format)
              </span>
            )}
          </div>
        </div>

        {/* Footer Actions */}
        <div className="px-6 pb-6 flex items-center gap-3">
          <button
            onClick={handleSave}
            disabled={saving || !!errorMsg}
            className="flex-1 bg-indigo-600 hover:bg-indigo-500 disabled:bg-gray-700 disabled:text-gray-500 text-white rounded-lg py-2 text-sm font-medium transition-colors"
          >
            {saving 
              ? (isZh ? '保存中...' : 'Saving...') 
              : saved 
                ? (isZh ? '保存成功!' : 'Saved!') 
                : (isZh ? '保存并生效' : 'Save & Reload')}
          </button>
          <button 
            onClick={onClose} 
            className="px-6 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded-lg py-2 text-sm font-medium transition-colors"
          >
            {isZh ? '关闭' : 'Close'}
          </button>
        </div>
      </div>
    </div>
  )
}
