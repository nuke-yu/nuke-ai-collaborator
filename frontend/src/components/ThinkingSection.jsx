import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { K } from '../i18n/keys'

/**
 * Renders an AI message's thinking blocks compactly (Option B):
 *  - While streaming: show ONLY the current (latest) step, expanded.
 *  - After the turn finishes: collapse into a single "✓ 已思考 N 步" line that
 *    expands on click to reveal every step.
 *
 * `blocks` is the per-message map { iteration: { iteration, content, completed } }.
 */
export default function ThinkingSection({ blocks, streaming }) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)

  const entries = Object.entries(blocks || {})
    .map(([iter, t]) => [Number(iter), t])
    .sort((a, b) => a[0] - b[0])

  if (entries.length === 0) return null

  // ── streaming: only the current step, expanded ──────────────────────────
  if (streaming) {
    const [iter, thought] = entries[entries.length - 1]
    return (
      <div className="px-4">
        <div className="my-2 rounded-lg overflow-hidden border border-amber-700/50 bg-amber-950/10">
          <div className="w-full flex items-center gap-2 px-3 py-2 bg-amber-900/30 text-xs text-amber-300">
            <span className="text-lg animate-pulse">∴</span>
            <span className="font-semibold">Thinking</span>
            <span className="text-amber-500/70">iteration {iter}</span>
          </div>
          <div className="p-3 border-t border-amber-700/30">
            <div className="text-sm text-gray-300 leading-relaxed whitespace-pre-wrap font-mono text-xs">
              {thought.content || (
                <span className="italic text-amber-400/60">{t(K.thinking.analyzing)}</span>
              )}
            </div>
          </div>
        </div>
      </div>
    )
  }

  // ── finished: one collapsed summary line, click to view all steps ───────
  return (
    <div className="px-4">
      <div className="my-2 rounded-lg overflow-hidden border border-amber-700/40 bg-amber-950/10">
        <button
          onClick={() => setExpanded((v) => !v)}
          className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-amber-400/80 hover:bg-amber-900/20 transition-colors"
        >
          <span>✓</span>
          <span className="font-medium">{t(K.thinking.done, { count: entries.length })}</span>
          <span className="ml-auto text-amber-500/60">{expanded ? t(K.thinking.collapse) : t(K.thinking.expand)}</span>
        </button>
        {expanded && (
          <div className="border-t border-amber-700/20 divide-y divide-amber-700/10">
            {entries.map(([iter, thought]) => (
              <div key={iter} className="p-3">
                <div className="text-[11px] text-amber-500/70 mb-1">iteration {iter}</div>
                <div className="text-sm text-gray-300 leading-relaxed whitespace-pre-wrap font-mono text-xs">
                  {thought.content || <span className="italic text-amber-400/50">{t(K.thinking.empty)}</span>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
