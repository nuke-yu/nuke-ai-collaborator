import { useState, useEffect } from 'react'

export default function AIThoughtBlock({ tempId, iteration, children }) {
  const [isOpen, setIsOpen] = useState(false)
  const [isCollapsed, setIsCollapsed] = useState(true)

  // Auto-expand on first render to show thinking content
  useEffect(() => {
    setIsCollapsed(false)
  }, [tempId])

  const handleToggle = () => {
    setIsCollapsed(!isCollapsed)
  }

  return (
    <div className="my-2 rounded-lg overflow-hidden border border-amber-700/50 bg-amber-950/10">
      <button
        onClick={handleToggle}
        className="w-full flex items-center gap-2 px-3 py-2 bg-amber-900/30 hover:bg-amber-900/40 text-xs text-amber-300 transition-colors"
      >
        <span className="text-lg">∴</span>
        <span className="font-semibold">Thinking</span>
        <span className="text-amber-500/70">iteration {iteration}</span>
        <span className="ml-auto text-amber-500/70">
          {isCollapsed ? '▼ 展开思考过程' : '▲ 收起思考过程'}
        </span>
      </button>
      {!isCollapsed && (
        <div className="p-3 border-t border-amber-700/30">
          <div className="text-xs text-amber-400/60 mb-2 italic">
            AI 正在分析任务、制定计划、评估工具...
          </div>
          <div className="text-sm text-gray-300 leading-relaxed whitespace-pre-wrap font-mono text-xs">
            {children}
          </div>
        </div>
      )}
    </div>
  )
}
