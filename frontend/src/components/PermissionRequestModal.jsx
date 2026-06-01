import { useState } from 'react'

export default function PermissionRequestModal({ request, onRespond }) {
  const [persistence, setPersistence] = useState('once')

  const handleRespond = (approved) => {
    onRespond(request.request_id, approved, approved ? persistence : 'once')
  }

  const args = request.arguments || {}
  const argEntries = Object.entries(args).filter(([, v]) => v !== null && v !== undefined)

  return (
    <div className="fixed inset-0 bg-black/45 backdrop-blur-sm flex items-end sm:items-center justify-center z-50 p-4 animate-fade-in">
      <div className="bg-gray-900/80 backdrop-blur-md rounded-2xl p-5 w-full max-w-md shadow-[0_20px_50px_rgba(0,0,0,0.55)] border border-gray-700/50 animate-scale-up">
        <div className="flex items-start gap-3 mb-4">
          <div className="w-9 h-9 rounded-full bg-amber-500/20 flex items-center justify-center flex-shrink-0 mt-0.5">
            <svg className="w-4 h-4 text-amber-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
            </svg>
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-white font-medium text-sm">Bot 请求执行工具</p>
            <p className="text-gray-400 text-xs mt-0.5">是否允许此次操作？</p>
          </div>
        </div>

        <div className="bg-gray-900 rounded-lg p-3 mb-4 space-y-1.5">
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-500 w-14 flex-shrink-0">工具</span>
            <span className="text-xs font-mono text-indigo-300 bg-indigo-900/30 px-2 py-0.5 rounded">
              {request.tool}
            </span>
          </div>
          {argEntries.map(([k, v]) => (
            <div key={k} className="flex items-start gap-2">
              <span className="text-xs text-gray-500 w-14 flex-shrink-0">{k}</span>
              <span className="text-xs font-mono text-gray-300 break-all line-clamp-3">
                {typeof v === 'string' ? v : JSON.stringify(v)}
              </span>
            </div>
          ))}
        </div>

        <div className="flex gap-2 mb-4">
          {['once', 'always'].map(p => (
            <button
              key={p}
              onClick={() => setPersistence(p)}
              className={`flex-1 text-xs py-1.5 rounded-lg border transition-colors ${
                persistence === p
                  ? 'border-indigo-500 bg-indigo-600/20 text-indigo-300'
                  : 'border-gray-700 bg-gray-900 text-gray-400 hover:border-gray-600'
              }`}
            >
              {p === 'once' ? '仅此次' : '永久允许'}
            </button>
          ))}
        </div>

        <div className="flex gap-2">
          <button
            onClick={() => handleRespond(true)}
            className="flex-1 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg py-2 text-sm font-medium transition-colors"
          >
            允许
          </button>
          <button
            onClick={() => handleRespond(false)}
            className="flex-1 bg-gray-700 hover:bg-gray-600 text-gray-200 rounded-lg py-2 text-sm transition-colors"
          >
            拒绝
          </button>
        </div>
      </div>
    </div>
  )
}
