import { useEffect, useState } from 'react'
import { fetchSessionTimeline } from '../api'

const NODE_ICONS = {
  context_injected: '🧠',
  tool_execution: '🛠️',
  permission_approved: '🔐',
  deliverable_produced: '📦',
  system_event: '⚡',
  error: '❌',
}

const NODE_COLORS = {
  context_injected: 'border-sky-500/30 bg-sky-500/10 text-sky-300',
  tool_execution: 'border-indigo-500/30 bg-indigo-500/10 text-indigo-300',
  permission_approved: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
  deliverable_produced: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
  system_event: 'border-gray-500/30 bg-gray-500/10 text-gray-300',
  error: 'border-rose-500/30 bg-rose-500/10 text-rose-300',
}

export default function ExecutionTimelineDrawer({ sessionId, groupId, onClose }) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [timeline, setTimeline] = useState(null)

  useEffect(() => {
    if (!sessionId || !groupId) return
    let active = true
    setLoading(true)
    setError(null)

    fetchSessionTimeline(sessionId, groupId)
      .then((data) => {
        if (active) {
          setTimeline(data)
          setLoading(false)
        }
      })
      .catch((err) => {
        if (active) {
          setError(err.message || '加载执行时间线失败')
          setLoading(false)
        }
      })

    return () => {
      active = false
    }
  }, [sessionId, groupId])

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50 backdrop-blur-xs transition-opacity animate-fade-in">
      <div className="relative w-full max-w-lg h-full bg-gray-900 border-l border-gray-800 shadow-2xl flex flex-col overflow-hidden animate-slide-left">
        {/* Drawer Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-800 bg-gray-900/90 backdrop-blur-md">
          <div className="flex items-center gap-2">
            <span className="text-xl">⚡</span>
            <div>
              <h3 className="font-semibold text-gray-100 text-sm">Session 执行时间线</h3>
              <p className="text-xs text-gray-400 font-mono">ID: {sessionId}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg text-gray-400 hover:text-white hover:bg-gray-800 transition-colors"
          >
            ✕
          </button>
        </div>

        {/* Status Bar */}
        {timeline && (
          <div className="flex items-center justify-between px-5 py-2.5 bg-gray-950/60 border-b border-gray-800 text-xs">
            <div className="flex items-center gap-2">
              <span className="text-gray-400">状态:</span>
              <span className="px-2 py-0.5 rounded-full font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                {timeline.status}
              </span>
            </div>
            <div className="flex items-center gap-2 text-gray-400">
              <span>总耗时:</span>
              <span className="font-mono text-indigo-400 font-semibold">
                ⚡ {timeline.total_duration_s}s
              </span>
            </div>
          </div>
        )}

        {/* Content Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {loading && (
            <div className="flex flex-col items-center justify-center py-12 text-gray-400 gap-3">
              <div className="w-6 h-6 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
              <p className="text-xs">加载 Session 事件节点中...</p>
            </div>
          )}

          {error && (
            <div className="p-4 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-300 text-xs">
              <p className="font-semibold">加载失败</p>
              <p className="mt-1">{error.includes('Not Found') || error.includes('404') ? '未找到当前 Session 的详细执行记录' : error}</p>
            </div>
          )}

          {timeline && timeline.nodes.length === 0 && (
            <div className="text-center py-12 text-gray-500 text-xs">
              暂无已投影的执行节点
            </div>
          )}

          {timeline && timeline.nodes.map((node, idx) => {
            const icon = NODE_ICONS[node.type] || '⚡'
            const badgeClass = NODE_COLORS[node.type] || NODE_COLORS.system_event

            return (
              <div key={node.node_id || idx} className="relative pl-6 pb-2 border-l border-gray-800 last:border-l-0">
                <span className="absolute -left-3 top-0.5 flex h-6 w-6 items-center justify-center rounded-full bg-gray-900 border border-gray-700 text-xs shadow-sm">
                  {icon}
                </span>

                <div className="rounded-xl border border-gray-800 bg-gray-850/80 p-3 shadow-xs hover:border-gray-700 transition-all">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className={`px-2 py-0.5 rounded-md text-[10px] font-medium border ${badgeClass}`}>
                        {node.type}
                      </span>
                      <h4 className="font-medium text-gray-200 text-xs">{node.title}</h4>
                    </div>

                    {node.duration_s !== null && (
                      <span className="text-[10px] font-mono text-indigo-400 bg-indigo-500/10 px-1.5 py-0.5 rounded-md">
                        {node.duration_s}s
                      </span>
                    )}
                  </div>

                  <p className="mt-2 text-xs text-gray-300 leading-relaxed font-mono whitespace-pre-wrap">
                    {node.detail}
                  </p>

                  {/* Artifact Badges */}
                  {node.artifact_ids && node.artifact_ids.length > 0 && (
                    <div className="mt-2 pt-2 border-t border-gray-800/80 flex flex-wrap gap-1.5">
                      {node.artifact_ids.map((artId) => (
                        <span key={artId} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] bg-emerald-500/10 text-emerald-300 border border-emerald-500/20 font-mono">
                          📄 {artId}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
