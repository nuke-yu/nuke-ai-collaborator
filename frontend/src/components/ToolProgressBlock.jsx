import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { K } from '../i18n/keys'

export default function ToolProgressBlock({ tempId, toolName, args, iteration, durationSec, result, isError }) {
  const { t } = useTranslation()
  const [isExpanded, setIsExpanded] = useState(false)
  const [status, setStatus] = useState('running') // 'queued' | 'running' | 'completed'

  useEffect(() => {
    if (durationSec !== undefined) {
      setStatus('completed')
    }
  }, [durationSec])

  const getToolIcon = (name) => {
    if (name.includes('read') || name.includes('file')) return '📖'
    if (name.includes('write') || name.includes('create')) return '✍️'
    if (name.includes('run_shell') || name.includes('shell')) return '💻'
    if (name.includes('search')) return '🔍'
    if (name.includes('skill')) return '⚡'
    if (name.includes('memory')) return '🧠'
    if (name.includes('tool')) return '🛠️'
    return '🔧'
  }

  const formatDuration = (sec) => {
    if (sec < 1) return '<1s'
    if (sec < 60) return `${sec.toFixed(1)}s`
    const mins = Math.floor(sec / 60)
    const secs = sec % 60
    return `${mins}m ${secs.toFixed(0)}s`
  }

  const formatArgsForDisplay = (args) => {
    if (!args) return '{}'
    try {
      const formatted = {}
      for (const [key, value] of Object.entries(args)) {
        if (typeof value === 'string' && value.length > 50) {
          formatted[key] = value.substring(0, 50) + '...'
        } else {
          formatted[key] = value
        }
      }
      return JSON.stringify(formatted, null, 2)
    } catch {
      return String(args)
    }
  }

  const statusColors = {
    queued: 'bg-blue-900/30 border-blue-700/50 text-blue-300',
    running: 'bg-green-900/30 border-green-700/50 text-green-300 animate-pulse',
    completed: isError
      ? 'bg-red-900/30 border-red-700/50 text-red-300'
      : 'bg-emerald-900/30 border-emerald-700/50 text-emerald-300',
  }

  return (
    <div className={`my-1.5 rounded-lg overflow-hidden border ${statusColors[status]} transition-colors`}>
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full flex items-center gap-2 px-3 py-2 text-xs"
      >
        <span className="text-base">{getToolIcon(toolName)}</span>
        <span className="font-semibold truncate">{toolName}</span>
        <span className="text-gray-500 text-[10px]">iteration {iteration}</span>

        {durationSec !== undefined && (
          <span className="ml-auto text-[10px] text-gray-200">
            ⏱️ {formatDuration(durationSec)}
          </span>
        )}

        <span className="ml-2 text-gray-500">
          {isExpanded ? '▲' : '▼'}
        </span>
      </button>

      {isExpanded && (
        <div className="p-3 border-t border-white/10 bg-black/20 space-y-2">
          <div>
            <div className="text-[10px] text-gray-500 mb-1 font-mono uppercase tracking-wider">Arguments</div>
            <pre className="text-xs text-gray-300 font-mono overflow-x-auto whitespace-pre-wrap">
              {formatArgsForDisplay(args)}
            </pre>
          </div>
          {result !== undefined && (
            <div>
              <div className={`text-[10px] mb-1 font-mono uppercase tracking-wider ${isError ? 'text-red-400' : 'text-gray-500'}`}>
                {isError ? '⚠ Error Output' : 'Output'}
              </div>
              <pre className={`text-xs font-mono overflow-x-auto whitespace-pre-wrap max-h-48 overflow-y-auto rounded p-2 ${isError ? 'text-red-300 bg-red-950/30' : 'text-gray-200 bg-black/30'}`}>
                {result || t(K.botLog.empty)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
