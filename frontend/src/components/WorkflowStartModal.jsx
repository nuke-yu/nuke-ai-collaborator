import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { K } from '../i18n/keys'

export default function WorkflowStartModal({ onStart, onClose, groupBots = [] }) {
  const { t } = useTranslation()

  // Discussion workflow options
  const [rounds, setRounds] = useState(3)
  const [selectedBots, setSelectedBots] = useState(() => groupBots.map(b => b.id))
  const [summarizerId, setSummarizerId] = useState(() => groupBots[0]?.id || '')

  const handleToggleBot = (botId) => {
    setSelectedBots(prev => {
      const isSelected = prev.includes(botId)
      const next = isSelected ? prev.filter(id => id !== botId) : [...prev, botId]
      // Auto adjust summarizer if current one gets deselected
      if (isSelected && summarizerId === botId) {
        const remaining = next.filter(id => id !== botId)
        if (remaining.length > 0) {
          setSummarizerId(remaining[0])
        }
      } else if (!isSelected && next.length === 1) {
        setSummarizerId(botId)
      }
      return next
    })
  }

  const handleStart = () => {
    if (selectedBots.length === 0) {
      alert(t(K.workflow.noBotsSelected))
      return
    }
    onStart({
      orchestrator_id: 'discussion_v1',
      spec: {
        bots: selectedBots,
        rounds: rounds,
        summarizer_id: summarizerId
      }
    })
  }

  return (
    <div className="fixed inset-0 bg-black/45 backdrop-blur-sm flex items-center justify-center z-50 animate-fade-in" onClick={onClose}>
      <div
        className="bg-gray-900/80 backdrop-blur-md border border-gray-700/50 rounded-2xl p-6 w-96 shadow-[0_20px_50px_rgba(0,0,0,0.55)] max-h-[80vh] overflow-y-auto animate-scale-up flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <h3 className="text-white font-semibold mb-1">{t(K.workflow.tabDiscussion)}</h3>
        <p className="text-gray-400 text-xs mb-4">
          Select bots to participate, configure discussion rounds, and choose the summarizer bot.
        </p>

        {/* Tab contents */}
        <div className="flex-1 space-y-4 mb-5">
          <div className="space-y-4">
            {/* Round Setting */}
            <div className="space-y-1">
              <label className="text-gray-400 text-xs font-semibold flex justify-between">
                <span>{t(K.workflow.discussionRounds)}</span>
                <span className="text-indigo-400 font-bold">{rounds}</span>
              </label>
              <div className="flex items-center gap-2">
                <input
                  type="range"
                  min="1"
                  max="20"
                  value={rounds}
                  onChange={e => setRounds(parseInt(e.target.value, 10))}
                  className="flex-1 accent-indigo-500 h-1 bg-gray-700 rounded-lg appearance-none cursor-pointer"
                />
                <input
                  type="number"
                  min="1"
                  max="100"
                  value={rounds}
                  onChange={e => setRounds(Math.max(1, parseInt(e.target.value, 10) || 1))}
                  className="w-12 bg-gray-850 text-white text-center text-xs rounded border border-gray-700 py-0.5 outline-none focus:ring-1 focus:ring-indigo-500"
                />
              </div>
            </div>

            {/* Bot Selector */}
            <div className="space-y-1.5">
              <label className="text-gray-400 text-xs font-semibold">{t(K.workflow.discussionBots)}</label>
              <div className="bg-gray-855 border border-gray-800 rounded-lg p-2.5 space-y-2 max-h-40 overflow-y-auto">
                {groupBots.map(bot => {
                  const isChecked = selectedBots.includes(bot.id)
                  return (
                    <label key={bot.id} className="flex items-center gap-2.5 cursor-pointer group">
                      <input
                        type="checkbox"
                        checked={isChecked}
                        onChange={() => handleToggleBot(bot.id)}
                        className="w-4 h-4 rounded border-gray-700 text-indigo-600 bg-gray-900 focus:ring-indigo-500 focus:ring-offset-gray-900 focus:ring-2 cursor-pointer"
                      />
                      <div
                        className="w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold text-white flex-shrink-0"
                        style={{ backgroundColor: bot.avatar_color }}
                      >
                        {bot.name[0]}
                      </div>
                      <span className="text-gray-300 text-xs group-hover:text-white transition-colors truncate">{bot.name}</span>
                    </label>
                  )
                })}
              </div>
            </div>

            {/* Summarizer Bot Selector */}
            <div className="space-y-1.5">
              <label className="text-gray-400 text-xs font-semibold">{t(K.workflow.discussionSummarizer)}</label>
              <select
                value={summarizerId}
                onChange={e => setSummarizerId(parseInt(e.target.value, 10))}
                disabled={selectedBots.length === 0}
                className="w-full bg-gray-850 text-white text-xs rounded border border-gray-700 px-2.5 py-1.5 outline-none focus:ring-1 focus:ring-indigo-500 cursor-pointer disabled:opacity-50"
              >
                {groupBots.filter(b => selectedBots.includes(b.id)).map(bot => (
                  <option key={bot.id} value={bot.id}>{bot.name}</option>
                ))}
              </select>
            </div>
          </div>
        </div>

        <div className="flex gap-2">
          <button
            onClick={handleStart}
            className="flex-1 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg py-2 text-sm font-medium transition-colors"
          >
            ⚡ {t(K.workflow.start)}
          </button>
          <button
            onClick={onClose}
            className="flex-1 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded-lg py-2 text-sm transition-colors"
          >
            {t(K.workflow.cancel)}
          </button>
        </div>
      </div>
    </div>
  )
}
