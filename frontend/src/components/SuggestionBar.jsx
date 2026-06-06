import React from 'react'

export default function SuggestionBar({ workflow, isStreaming, awaySummary, messages, members, onSelect }) {
  const suggestions = deriveSuggestions(workflow, isStreaming, awaySummary, messages, members)

  if (suggestions.length === 0) return null

  return (
    <div className="flex flex-wrap gap-2 px-4 py-2 bg-gray-900/40 backdrop-blur-md border-t border-gray-800/40 items-center animate-fade-in select-none">
      <span className="text-[10px] uppercase font-bold tracking-wider text-gray-500 mr-1 flex-shrink-0">
        建议操作
      </span>
      <div className="flex flex-wrap gap-1.5 min-w-0">
        {suggestions.map((s, idx) => {
          let styleClass = 'bg-gray-800/40 text-gray-300 border-gray-700/30 hover:bg-gray-800/80 hover:text-white'
          if (s.variant === 'indigo') {
            styleClass = 'bg-indigo-500/10 text-indigo-300 border-indigo-500/20 hover:bg-indigo-500/20 hover:text-white'
          } else if (s.variant === 'purple') {
            styleClass = 'bg-purple-500/10 text-purple-300 border-purple-500/20 hover:bg-purple-500/20 hover:text-white'
          } else if (s.variant === 'red') {
            styleClass = 'bg-red-500/10 text-red-400 border-red-500/20 hover:bg-red-500/20 hover:text-red-300'
          }

          return (
            <button
              key={idx}
              onClick={() => onSelect(s.text, s.action)}
              className={`text-xs px-2.5 py-1 rounded-full border transition-all duration-200 active:scale-95 flex items-center gap-1 font-medium ${styleClass}`}
            >
              {s.label}
            </button>
          )
        })}
      </div>
    </div>
  )
}

function deriveSuggestions(workflow, isStreaming, awaySummary, messages, members) {
  const suggestions = []

  // 1. Confirm Gate (driven by workflow.awaiting_confirm)
  const isAwaitingConfirm = workflow?.awaiting_confirm
  if (isAwaitingConfirm) {
    const isRework = String(isAwaitingConfirm).endsWith('rework')
    suggestions.push({
      label: isRework ? '👍 确认打回 Dev 修复' : '👍 确认并继续',
      action: 'confirm',
      variant: 'indigo'
    })
    suggestions.push({
      label: '✏️ 我想修改',
      text: '我想做以下调整：',
      variant: 'gray'
    })
  }

  // 2. Running Task
  if (isStreaming) {
    suggestions.push({
      label: '🛑 停止生成',
      action: 'abort',
      variant: 'red'
    })
  }

  // 3. Idle (Start Workflow)
  const isWorkflowActive = workflow?.active
  if (!isWorkflowActive && !isStreaming) {
    suggestions.push({
      label: '🚀 开始开发流水线',
      action: 'start',
      variant: 'indigo'
    })
  }

  // 4. Recap/Retro is active (Tier 2 suggestion)
  if (awaySummary && !isStreaming && !isAwaitingConfirm) {
    const firstBot = members?.find(m => m.type === 'bot')
    const botMention = firstBot ? `@${firstBot.name} ` : ''
    suggestions.push({
      label: '📄 让 Bot 查看开发复盘 (RETRO_LATEST.md)',
      text: `${botMention}请读取工作区的 RETRO_LATEST.md，并告诉我有什么需要注意的。`,
      variant: 'purple'
    })
  }

  return suggestions.slice(0, 3)
}
