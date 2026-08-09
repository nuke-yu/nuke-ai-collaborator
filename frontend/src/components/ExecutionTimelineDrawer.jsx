import { useEffect, useState } from 'react'
import { cancelSessionRecovery, fetchSessionTimeline, resumeSession } from '../api'

const NODE_ICONS = {
  thinking: '💡',
  context_injected: '🧠',
  tool_execution: '🛠️',
  permission_approved: '🔐',
  deliverable_produced: '📦',
  system_event: '⚡',
  error: '❌',
}

const NODE_COLORS = {
  thinking: 'border-purple-500/30 bg-purple-500/10 text-purple-300',
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
  const [actionLoading, setActionLoading] = useState(false)

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

  const runRecoveryAction = async (action) => {
    setActionLoading(true)
    setError(null)
    try {
      if (action === 'resume') {
        await resumeSession(sessionId, groupId)
      } else {
        await cancelSessionRecovery(sessionId, groupId)
      }
      const refreshed = await fetchSessionTimeline(sessionId, groupId)
      setTimeline(refreshed)
    } catch (err) {
      setError(err.message || '恢复操作失败')
    } finally {
      setActionLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50 backdrop-blur-xs transition-opacity animate-fade-in">
      <div className="relative w-full max-w-xl h-full bg-gray-900 border-l border-gray-800 shadow-2xl flex flex-col overflow-hidden animate-slide-left">
        {/* Drawer Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-800 bg-gray-900/90 backdrop-blur-md">
          <div className="flex items-center gap-2">
            <span className="text-xl">⚡</span>
            <div>
              <h3 className="font-semibold text-gray-100 text-sm">Session 执行过程与时间线</h3>
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
              <span className="text-gray-400">运行状态:</span>
              <span className={`px-2 py-0.5 rounded-full font-medium border ${timeline.status === 'failed' ? 'bg-rose-500/10 text-rose-400 border-rose-500/20' : timeline.status === 'awaiting_recovery' ? 'bg-amber-500/10 text-amber-400 border-amber-500/20' : 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'}`}>
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

        {timeline?.recovery_actions?.length > 0 && (
          <div className="flex items-center justify-end gap-2 border-b border-gray-800 bg-amber-950/20 px-5 py-2.5">
            <span className="mr-auto text-xs text-amber-300">该执行等待恢复处理</span>
            <button type="button" disabled={actionLoading} onClick={() => runRecoveryAction('cancel_recovery')} className="rounded-md border border-gray-700 px-2.5 py-1 text-xs text-gray-400 hover:border-gray-500 hover:text-gray-200 disabled:opacity-50">
              取消恢复
            </button>
            <button type="button" disabled={actionLoading} onClick={() => runRecoveryAction('resume')} className="rounded-md border border-indigo-500/40 bg-indigo-500/15 px-2.5 py-1 text-xs text-indigo-300 hover:bg-indigo-500/25 disabled:opacity-50">
              {actionLoading ? '处理中…' : '恢复执行'}
            </button>
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

          {timeline?.warnings?.length > 0 && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-200">
              <p className="font-semibold">时间线存在未投影事件</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-4 text-amber-300/80">
                {timeline.warnings.map(warning => <li key={warning}>{warning}</li>)}
              </ul>
            </div>
          )}

          {timeline && timeline.nodes.map((node, idx) => {
            const icon = NODE_ICONS[node.type] || '⚡'
            const badgeClass = NODE_COLORS[node.type] || NODE_COLORS.system_event
            const isTool = node.type === 'tool_execution'
            const isThinking = node.type === 'thinking'
            const hasArgs = node.metadata?.arguments && Object.keys(node.metadata.arguments).length > 0
            const hasResult = Boolean(node.metadata?.result)

            return (
              <div key={node.node_id || idx} className="relative pl-6 pb-2 border-l border-gray-800 last:border-l-0">
                <span className="absolute -left-3 top-0.5 flex h-6 w-6 items-center justify-center rounded-full bg-gray-900 border border-gray-700 text-xs shadow-sm">
                  {icon}
                </span>

                <div className="rounded-xl border border-gray-800 bg-gray-850/80 p-3.5 shadow-xs hover:border-gray-700 transition-all space-y-2">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className={`px-2 py-0.5 rounded-md text-[10px] font-medium border ${badgeClass}`}>
                        {node.type}
                      </span>
                      <h4 className="font-medium text-gray-200 text-xs">{node.title}</h4>
                    </div>

                    {node.duration_s !== null && (
                      <span className="text-[10px] font-mono text-indigo-400 bg-indigo-500/10 px-1.5 py-0.5 rounded-md border border-indigo-500/20">
                        ⚡ {node.duration_s}s
                      </span>
                    )}
                  </div>

                  {/* Summary / Detail Text */}
                  {!isThinking && (
                    <p className="text-xs text-gray-300 leading-relaxed font-mono whitespace-pre-wrap">
                      {node.detail}
                    </p>
                  )}

                  {/* Thinking Section */}
                  {isThinking && (
                    <div className="text-xs italic text-purple-200/90 bg-purple-950/30 border border-purple-800/30 rounded-lg p-2.5 leading-relaxed font-serif">
                      💡 {node.detail}
                    </div>
                  )}

                  {/* Input Arguments Section */}
                  {isTool && hasArgs && (
                    <div className="text-[11px] rounded-lg bg-gray-950/80 border border-gray-800 p-2.5">
                      <span className="text-[10px] text-gray-400 font-semibold block mb-1">工具输入参数 (Input Args):</span>
                      <pre className="font-mono text-indigo-300 whitespace-pre-wrap leading-relaxed max-h-36 overflow-y-auto">
                        {typeof node.metadata.arguments === 'string'
                          ? node.metadata.arguments
                          : JSON.stringify(node.metadata.arguments, null, 2)}
                      </pre>
                    </div>
                  )}

                  {/* Console Execution Result Section */}
                  {isTool && hasResult && (
                    <div className="text-[11px] rounded-lg bg-black/90 border border-gray-800 p-2.5">
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-[10px] text-emerald-400 font-semibold">控制台输出 (Console Output):</span>
                        {node.status === 'failed' && (
                          <span className="text-[10px] text-rose-400 font-semibold">❌ 执行异常</span>
                        )}
                      </div>
                      <pre className="font-mono text-gray-300 whitespace-pre-wrap leading-relaxed max-h-48 overflow-y-auto text-[10px]">
                        {node.metadata.result.slice(0, 1500)}
                      </pre>
                    </div>
                  )}

                  {/* Artifact Badges */}
                  {node.artifact_ids && node.artifact_ids.length > 0 && (
                    <div className="pt-2 border-t border-gray-800/80 flex flex-wrap gap-1.5">
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
