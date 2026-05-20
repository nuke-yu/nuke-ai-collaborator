export default function WorkflowBar({ workflow, onNext, onEnd }) {
  if (!workflow?.active) return null
  const { stages, current } = workflow
  const isLast = current >= stages.length - 1

  return (
    <div className="bg-indigo-950/60 border-b border-indigo-800/40 px-4 py-2 flex items-center gap-3 flex-shrink-0">
      <span className="text-xs text-indigo-400 font-semibold flex-shrink-0">工作流</span>
      <div className="flex items-center gap-1 flex-1 overflow-x-auto min-w-0">
        {stages.map((s, i) => (
          <div key={i} className="flex items-center gap-1 flex-shrink-0">
            {i > 0 && <span className="text-gray-700 text-xs mx-0.5">→</span>}
            {s.stage_type === 'pool' ? (
              <PoolStage stage={s} active={i === current} done={i < current} />
            ) : (
              <SingleStage stage={s} active={i === current} done={i < current} />
            )}
          </div>
        ))}
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        <button
          onClick={onNext}
          className="text-xs text-indigo-500 hover:text-indigo-300 transition-colors"
          title="手动推进到下一阶段"
        >
          {isLast ? '完成 ✓' : '手动推进 →'}
        </button>
        <button
          onClick={onEnd}
          className="text-xs text-gray-600 hover:text-gray-400 transition-colors"
          title="结束工作流"
        >✕</button>
      </div>
    </div>
  )
}

function SingleStage({ stage, active, done }) {
  return (
    <div className={`flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium transition-all duration-500 ${
      active ? 'bg-indigo-600 text-white ring-1 ring-indigo-400'
      : done ? 'bg-gray-800 text-gray-600 line-through'
      : 'bg-gray-800/60 text-gray-500'
    }`}>
      {active && <span className="w-1.5 h-1.5 rounded-full bg-indigo-300 animate-pulse flex-shrink-0" />}
      {!active && <div className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ backgroundColor: stage.avatar_color }} />}
      {stage.name}
    </div>
  )
}

function PoolStage({ stage, active, done }) {
  const inProgress = stage.in_progress || {}
  const completedCount = stage.completed_count ?? 0
  const totalTickets = stage.total_tickets ?? 0

  return (
    <div className={`flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium border transition-all duration-500 ${
      active ? 'bg-indigo-950/80 border-indigo-700 text-white'
      : done ? 'bg-gray-800/40 border-gray-700/40 text-gray-600'
      : 'bg-gray-800/40 border-gray-700/40 text-gray-500'
    }`}>
      {active && <span className="w-1.5 h-1.5 rounded-full bg-indigo-300 animate-pulse flex-shrink-0" />}
      {stage.bots.map((bot, bi) => {
        const ticket = inProgress[String(bot.id)]
        return (
          <span
            key={bot.id}
            title={ticket ? `${bot.name}：${ticket}` : bot.name}
            className={`flex items-center gap-0.5 transition-all duration-300 ${
              ticket ? 'text-white' : 'text-gray-500'
            }`}
          >
            {bi > 0 && <span className="text-gray-700 mx-0.5">·</span>}
            <div className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: bot.avatar_color, opacity: ticket ? 1 : 0.35 }} />
            {bot.name}
          </span>
        )
      })}
      {totalTickets > 0 && (
        <span className="ml-1 text-indigo-400 tabular-nums">{completedCount}/{totalTickets}</span>
      )}
    </div>
  )
}
