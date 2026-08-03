import { useCallback, useEffect, useState } from 'react'
import { fetchGroupTimeline } from '../api'


const SOURCE_META = {
  workflow: { label: '工作流', icon: '◆', color: 'text-violet-300 bg-violet-500/10 border-violet-500/30' },
  permission: { label: '权限', icon: '🔐', color: 'text-amber-300 bg-amber-500/10 border-amber-500/30' },
  session: { label: '执行', icon: '●', color: 'text-sky-300 bg-sky-500/10 border-sky-500/30' },
}

const EVENT_LABELS = {
  workflow_started: '工作流启动',
  workflow_completed: '工作流完成',
  workflow_failed: '工作流失败',
  workflow_recovered: '工作流恢复',
  workflow_paused: '工作流暂停',
  stage_entered: '进入阶段',
  stage_completed: '阶段完成',
  stage_rework_started: '阶段返工',
  gate_requested: '等待人工确认',
  gate_approved: '人工确认通过',
  gate_revision_requested: '要求修改',
  session_start: 'Bot 执行开始',
  session_status: '执行状态变化',
  session_completed: 'Bot 执行完成',
  session_failed: 'Bot 执行失败',
  session_recovered: 'Bot 执行恢复',
  permission_requested: '请求操作权限',
  permission_approved: '操作已批准',
  permission_denied: '操作被拒绝',
  tool_call: '工具调用',
  tool_result: '工具结果',
  llm_response: '模型响应',
  child_fork: '委派子任务',
  child_join: '合并子任务',
}

function eventSummary(item) {
  const payload = item.payload || {}
  const context = item.context || {}
  if (item.source === 'permission') {
    return [payload.tool_name, payload.decision_source].filter(Boolean).join(' · ')
  }
  if (item.source === 'workflow') {
    return [context.stage_id, payload.stage_name || payload.reason].filter(Boolean).join(' · ')
  }
  if (item.event_type === 'session_status') {
    return [payload.from_status, payload.status].filter(Boolean).join(' → ')
  }
  return payload.tool_name || payload.model || context.session_id || ''
}

function formatTime(timestamp) {
  const date = new Date(timestamp)
  if (Number.isNaN(date.getTime())) return '未知时间'
  return date.toLocaleString([], {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

function TimelineItem({ item }) {
  const [expanded, setExpanded] = useState(false)
  const source = SOURCE_META[item.source] || SOURCE_META.session
  const effects = item.policy?.effects || []
  const summary = eventSummary(item)

  return (
    <article className="relative pl-8" data-testid={`timeline-item-${item.event_id}`}>
      <span className="absolute left-[7px] top-3 h-2.5 w-2.5 rounded-full bg-gray-500 ring-4 ring-gray-900" />
      <button
        type="button"
        onClick={() => setExpanded(value => !value)}
        className="w-full text-left rounded-xl border border-gray-700 bg-gray-800/70 p-3 hover:border-gray-600 transition-colors"
      >
        <div className="flex items-start gap-2">
          <span className={`shrink-0 rounded-md border px-1.5 py-0.5 text-[10px] font-semibold ${source.color}`}>
            {source.icon} {source.label}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between gap-2">
              <h4 className="truncate text-xs font-semibold text-gray-200">
                {EVENT_LABELS[item.event_type] || item.event_type}
              </h4>
              <time className="shrink-0 text-[10px] text-gray-500">{formatTime(item.occurred_at)}</time>
            </div>
            {summary && <p className="mt-1 truncate text-[11px] text-gray-400">{summary}</p>}
            <div className="mt-2 flex flex-wrap gap-1">
              {effects.map(effect => (
                <span key={effect} className="rounded bg-gray-700/70 px-1.5 py-0.5 text-[9px] text-gray-400">
                  {effect}
                </span>
              ))}
            </div>
          </div>
          <span className="text-[10px] text-gray-500">{expanded ? '▲' : '▼'}</span>
        </div>
      </button>
      {expanded && (
        <div className="mt-1 rounded-lg border border-gray-700/70 bg-gray-950 p-3">
          <dl className="mb-2 grid grid-cols-[72px_1fr] gap-x-2 gap-y-1 text-[10px]">
            <dt className="text-gray-500">Event ID</dt>
            <dd className="truncate font-mono text-gray-400" title={item.event_id}>{item.event_id}</dd>
            {item.context?.workflow_id && <><dt className="text-gray-500">Workflow</dt><dd className="truncate font-mono text-gray-400">{item.context.workflow_id}</dd></>}
            {item.context?.session_id && <><dt className="text-gray-500">Session</dt><dd className="truncate font-mono text-gray-400">{item.context.session_id}</dd></>}
            {item.context?.permission_id && <><dt className="text-gray-500">Permission</dt><dd className="truncate font-mono text-gray-400">{item.context.permission_id}</dd></>}
          </dl>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all text-[10px] leading-relaxed text-gray-400">
            {JSON.stringify(item.payload || {}, null, 2)}
          </pre>
        </div>
      )}
    </article>
  )
}

export default function TimelinePanel({ groupId, onClose }) {
  const [source, setSource] = useState('all')
  const [significance, setSignificance] = useState('business')
  const [items, setItems] = useState([])
  const [cursor, setCursor] = useState(null)
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async ({ append = false, quiet = false } = {}) => {
    if (!groupId) return
    if (!quiet) setLoading(true)
    setError('')
    try {
      const result = await fetchGroupTimeline(groupId, {
        limit: 50,
        cursor: append ? cursor : undefined,
        sources: source === 'all' ? [] : [source],
        businessSignificant: significance === 'business',
        eventClasses: significance === 'diagnostic' ? ['diagnostic'] : [],
      })
      setItems(previous => append ? [...previous, ...(result.items || [])] : (result.items || []))
      setCursor(result.next_cursor || null)
      setHasMore(Boolean(result.has_more))
    } catch (requestError) {
      const msg = requestError.message || ''
      if (msg.includes('Not Found') || msg.includes('404') || msg.includes('Group not found')) {
        setItems([])
        setError('')
      } else {
        setError(msg || '时间线加载失败')
      }
    } finally {
      if (!quiet) setLoading(false)
    }
  }, [groupId, source, significance, cursor])

  useEffect(() => {
    // Data fetching on a scope change intentionally drives the panel's loading state.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load()
  // cursor is deliberately excluded: changing page state must not restart the query.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupId, source, significance])

  return (
    <aside className="flex h-full w-full shrink-0 flex-col border-l border-gray-700 bg-gray-900 md:w-[430px]" aria-label="业务时间线">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-gray-700 px-4">
        <div>
          <h3 className="text-sm font-semibold text-gray-200">业务时间线</h3>
          <p className="text-[10px] text-gray-500">工作流 · Bot 执行 · 人工权限</p>
        </div>
        <div className="flex items-center gap-1">
          <button type="button" onClick={() => load()} disabled={loading} className="rounded px-2 py-1 text-xs text-gray-500 hover:bg-gray-800 hover:text-gray-300 disabled:opacity-50" title="刷新">↻</button>
          <button type="button" onClick={onClose} className="rounded px-2 py-1 text-sm text-gray-500 hover:bg-gray-800 hover:text-gray-300" aria-label="关闭时间线">✕</button>
        </div>
      </header>

      <div className="space-y-2 border-b border-gray-800 p-3">
        <div className="flex gap-1 rounded-lg bg-gray-800 p-1" role="group" aria-label="事件来源">
          {[
            ['all', '全部'], ['workflow', '工作流'], ['session', '执行'], ['permission', '权限'],
          ].map(([value, label]) => (
            <button key={value} type="button" onClick={() => setSource(value)} className={`flex-1 rounded-md px-2 py-1.5 text-[11px] transition-colors ${source === value ? 'bg-gray-700 text-gray-100 shadow-sm' : 'text-gray-500 hover:text-gray-300'}`}>
              {label}
            </button>
          ))}
        </div>
        <label className="flex items-center justify-between text-[11px] text-gray-400">
          <span>事件范围</span>
          <select value={significance} onChange={event => setSignificance(event.target.value)} className="rounded-md border border-gray-700 bg-gray-800 px-2 py-1 text-[11px] text-gray-300 outline-none focus:border-indigo-500">
            <option value="business">业务事件</option>
            <option value="diagnostic">诊断事件</option>
          </select>
        </label>
      </div>

      <div className="relative flex-1 overflow-y-auto px-3 py-4">
        <div className="absolute bottom-0 left-[27px] top-0 w-px bg-gray-800" />
        {loading && items.length === 0 && <div className="py-12 text-center text-xs text-gray-500">正在加载时间线…</div>}
        {error && (
          <div className="relative rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-300">
            {error}
            <button type="button" onClick={() => load()} className="ml-2 underline">重试</button>
          </div>
        )}
        {!loading && !error && items.length === 0 && (
          <div className="relative py-12 text-center">
            <div className="mb-2 text-2xl opacity-50">◌</div>
            <p className="text-xs text-gray-500">当前筛选条件下还没有事件</p>
          </div>
        )}
        <div className="space-y-3">
          {items.map(item => <TimelineItem key={item.event_id} item={item} />)}
        </div>
        {hasMore && (
          <button type="button" onClick={() => load({ append: true })} disabled={loading} className="relative mt-4 w-full rounded-lg border border-gray-700 bg-gray-800 py-2 text-xs text-gray-400 hover:border-gray-600 hover:text-gray-200 disabled:opacity-50">
            {loading ? '加载中…' : '加载更早事件'}
          </button>
        )}
      </div>
    </aside>
  )
}
