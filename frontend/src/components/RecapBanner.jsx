import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { K } from '../i18n/keys'

export default function RecapBanner({ summary, onDismiss, onRegenerate, loading }) {
  const { t } = useTranslation()
  const [isDismissing, setIsDismissing] = useState(false)

  if (!summary && !loading) return null

  const handleDismiss = () => {
    setIsDismissing(true)
    setTimeout(() => {
      onDismiss()
      setIsDismissing(false)
    }, 300) // Match fade-out animation duration
  }

  return (
    <div
      className={`mx-4 mt-3 transition-all duration-300 ease-out ${
        isDismissing ? 'opacity-0 scale-95 max-h-0 py-0 my-0' : 'opacity-100 max-h-40'
      }`}
    >
      <div className="relative overflow-hidden rounded-2xl border border-white/10 bg-gradient-to-r from-indigo-500/10 via-purple-500/10 to-pink-500/10 p-4 shadow-xl backdrop-blur-xl transition-all duration-300 hover:border-white/20 hover:shadow-indigo-500/5">
        {/* Subtle decorative glow */}
        <div className="absolute -left-10 -top-10 h-24 w-24 rounded-full bg-indigo-500/10 blur-2xl pointer-events-none" />
        <div className="absolute -right-10 -bottom-10 h-24 w-24 rounded-full bg-purple-500/10 blur-2xl pointer-events-none" />

        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3 flex-1 min-w-0">
            {/* Sparkling Recap Icon */}
            <div className="flex-shrink-0 mt-0.5 flex items-center justify-center w-8 h-8 rounded-xl bg-gradient-to-tr from-indigo-500 to-purple-600 text-white shadow-md shadow-indigo-500/20 animate-pulse">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 21l8.982-11.795H13.62l1.378-5.02L6 16.096h3.813z" />
              </svg>
            </div>

            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-xs font-semibold tracking-wider text-indigo-300 uppercase">
                  {t(K.recap.title)}
                </span>
                {loading && (
                  <span className="flex h-2 w-2 relative">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-75"></span>
                    <span className="relative inline-flex rounded-full h-2 w-2 bg-indigo-500"></span>
                  </span>
                )}
              </div>
              {loading ? (
                <div className="space-y-2 py-1">
                  <div className="h-4 bg-white/10 rounded w-3/4 animate-pulse" />
                  <div className="h-4 bg-white/10 rounded w-1/2 animate-pulse" />
                </div>
              ) : (
                <p className="text-sm font-medium text-gray-200 leading-relaxed drop-shadow-sm select-text">
                  {summary}
                </p>
              )}
            </div>
          </div>

          <div className="flex items-center gap-1.5 flex-shrink-0">
            {/* Manual Regenerate Button */}
            <button
              onClick={onRegenerate}
              disabled={loading}
              title={t(K.recap.triggerGenerate)}
              className="p-1.5 rounded-lg text-gray-400 hover:text-gray-200 hover:bg-white/5 active:bg-white/10 disabled:opacity-50 transition-all duration-200"
            >
              <svg
                className={`w-4 h-4 ${loading ? 'animate-spin' : 'hover:rotate-180 transition-transform duration-500'}`}
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                viewBox="0 0 24 24"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99" />
              </svg>
            </button>

            {/* Dismiss Button */}
            <button
              onClick={handleDismiss}
              title={t(K.recap.dismiss)}
              className="p-1.5 rounded-lg text-gray-400 hover:text-gray-200 hover:bg-white/5 active:bg-white/10 transition-all duration-200"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
