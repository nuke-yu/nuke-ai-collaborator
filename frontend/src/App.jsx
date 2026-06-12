import Login from './components/Login'
import ErrorBoundary from './components/ErrorBoundary'
import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { addMember } from './api'
import ChatWindow from './components/ChatWindow'
import { K } from './i18n/keys'

export default function App() {
  const { t } = useTranslation()
  const [token, setToken] = useState(() => localStorage.getItem('token'))
  const [memberId, setMemberId] = useState(() => {
    const saved = localStorage.getItem('memberId')
    return saved ? parseInt(saved) : null
  })
  const [name, setName] = useState('')

  const handleLogin = (data) => {
    localStorage.setItem('token', data.token)
    localStorage.setItem('user', JSON.stringify(data.user))
    setToken(data.token)
  }

  const [theme, setTheme] = useState(() => localStorage.getItem('app-theme') || 'default-dark')

  useEffect(() => {
    const themeClasses = ['theme-default-dark', 'theme-elegant-light', 'theme-elevenlabs', 'theme-hsbc', 'theme-cyberpunk', 'theme-glass']
    themeClasses.forEach(cls => document.documentElement.classList.remove(cls))
    document.documentElement.classList.add(`theme-${theme}`)

    // For general compatibility with light/dark utilities in plugins
    const isLight = theme === 'elegant-light' || theme === 'hsbc'
    document.documentElement.classList.toggle('light', isLight)

    localStorage.setItem('app-theme', theme)
  }, [theme])

  const handleLogout = () => {
    localStorage.removeItem('token')
    localStorage.removeItem('user')
    localStorage.removeItem('memberId')
    setToken(null)
    setMemberId(null)
  }

  const handleJoin = async () => {
    if (!name.trim()) return
    const params = new URLSearchParams(window.location.search)
    const groupId = parseInt(params.get('groupId')) || 1
    const data = await addMember(groupId, name.trim())
    localStorage.setItem('memberId', data.id)
    setMemberId(data.id)
  }

  if (!token) return <Login onLogin={handleLogin} />

  if (!memberId) {
    return (
      <div className="h-screen bg-gray-900 flex items-center justify-center">
        <div className="bg-gray-800 rounded-2xl p-8 w-80 shadow-xl">
          <h1 className="text-white text-xl font-bold mb-2">{t(K.app.joinGroup.title)}</h1>
          <p className="text-gray-400 text-sm mb-6">{t(K.app.joinGroup.subtitle)}</p>
          <input
            className="w-full bg-gray-700 text-white rounded-lg px-4 py-2.5 text-sm outline-none focus:ring-2 focus:ring-indigo-500 mb-4"
            placeholder={t(K.app.joinGroup.namePlaceholder)}
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleJoin()}
            autoFocus
          />
          <button
            onClick={handleJoin}
            className="w-full bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg py-2.5 text-sm font-medium transition-colors"
          >
            {t(K.app.joinGroup.submit)}
          </button>
        </div>
      </div>
    )
  }

  return (
    <ErrorBoundary>
      <ChatWindow memberId={memberId} theme={theme} onThemeChange={setTheme} onLogout={handleLogout} />
    </ErrorBoundary>
  )
}
