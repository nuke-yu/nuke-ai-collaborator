import React from 'react'
import { useTranslation } from 'react-i18next'
import { K } from '../i18n/keys'

export default function SuggestionBar({
  workflow,
  isStreaming,
  awaySummary,
  messages,
  members,
  onSelect,
  aiSuggestions = [],
  loading = false,
  onFetch
}) {
  const { t } = useTranslation()
  const suggestions = deriveSuggestions(t, workflow, isStreaming, awaySummary, messages, members)

  const hasBots = members?.some(m => m.type === 'bot')
  const showAiButton = hasBots && !isStreaming

  if (suggestions.length === 0 && !showAiButton && aiSuggestions.length === 0) {
    return null
  }

  return (
    <div className="flex flex-col gap-2 px-4 py-2 bg-gray-900/40 backdrop-blur-md border-t border-gray-800/40 select-none animate-fade-in">
      {/* Row 1: Actions & AI Trigger */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap min-w-0">
          {suggestions.length > 0 && (
            <>
              <span className="text-[10px] uppercase font-bold tracking-wider text-gray-500 mr-1 flex-shrink-0">
                {t(K.suggestion.title)}
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
            </>
          )}
        </div>

        {/* AI Suggestions Trigger Button */}
        {showAiButton && (
          <button
            onClick={onFetch}
            disabled={loading}
            className={`text-xs px-3 py-1 rounded-full border transition-all duration-200 active:scale-95 flex items-center gap-1 font-medium flex-shrink-0
              ${loading 
                ? 'bg-purple-500/20 text-purple-300 border-purple-500/30 cursor-not-allowed animate-pulse' 
                : 'bg-purple-500/10 text-purple-300 border-purple-500/20 hover:bg-purple-500/30 hover:text-white'
              }`}
          >
            {loading ? (
              <>
                <svg className="animate-spin -ml-1 h-3.5 w-3.5 text-purple-300" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                {t(K.suggestion.generating)}
              </>
            ) : (
              <>
                <span>✨</span> {t(K.suggestion.aiSuggest)}
              </>
            )}
          </button>
        )}
      </div>

      {/* Row 2: AI Suggestions */}
      {aiSuggestions.length > 0 && (
        <div className="flex items-start gap-2 border-t border-gray-800/30 pt-1.5 animate-fade-in">
          <span className="text-[10px] uppercase font-bold tracking-wider text-purple-400/80 mt-1.5 flex-shrink-0">
            ✨ {t(K.suggestion.aiSuggest)}
          </span>
          <div className="flex flex-col gap-1.5 min-w-0 flex-1">
            {aiSuggestions.map((text, idx) => (
              <button
                key={idx}
                onClick={() => onSelect(text)}
                className="text-left text-xs px-3 py-1.5 rounded-lg border border-purple-500/10 bg-purple-500/5 text-purple-200 hover:bg-purple-500/15 hover:text-white transition-all duration-200 active:scale-[0.99] font-normal leading-relaxed break-all"
              >
                {text}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function deriveSuggestions(t, workflow, isStreaming, awaySummary, messages, members) {
  const suggestions = []

  // 1. Confirm Gate (driven by workflow.awaiting_confirm)
  const isAwaitingConfirm = workflow?.awaiting_confirm
  if (isAwaitingConfirm) {
    const isRework = String(isAwaitingConfirm).endsWith('rework')
    suggestions.push({
      label: isRework ? t(K.suggestion.confirmRework) : t(K.suggestion.confirmContinue),
      action: 'confirm',
      variant: 'indigo'
    })
    suggestions.push({
      label: t(K.suggestion.wantModify),
      text: t(K.suggestion.wantModifyText),
      variant: 'gray'
    })
  }

  // 2. Running Task
  if (isStreaming) {
    suggestions.push({
      label: t(K.suggestion.stopGenerate),
      action: 'abort',
      variant: 'red'
    })
  }

  // 3. Idle (Start Workflow)
  const isWorkflowActive = workflow?.active
  if (!isWorkflowActive && !isStreaming) {
    suggestions.push({
      label: t(K.suggestion.startPipeline),
      action: 'start',
      variant: 'indigo'
    })
  }

  // 4. Recap/Retro is active (Tier 2 suggestion)
  if (awaySummary && !isStreaming && !isAwaitingConfirm) {
    const firstBot = members?.find(m => m.type === 'bot')
    const botMention = firstBot ? `@${firstBot.name} ` : ''
    suggestions.push({
      label: t(K.suggestion.viewRetro),
      text: `${botMention}${t(K.suggestion.viewRetroText)}`,
      variant: 'purple'
    })
  }

  return suggestions.slice(0, 3)
}

