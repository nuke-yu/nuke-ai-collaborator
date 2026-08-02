import Login from './components/Login'
import ErrorBoundary from './components/ErrorBoundary'
import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { addMember, login, register, fetchAllGroups, createGroup } from './api'
import ChatWindow from './components/ChatWindow'
import OnboardingWizard from './components/OnboardingWizard'
import Titlebar from './components/Titlebar'
import { useThemeStore } from './store/useThemeStore'
import { K } from './i18n/keys'

export default function App() {
  const { t } = useTranslation()
  const { theme, setTheme } = useThemeStore()
  const [isOnboarded, setIsOnboarded] = useState(() => localStorage.getItem('collaborator-onboarded') === 'true')
  const [token, setToken] = useState(() => localStorage.getItem('token'))
  const [memberId, setMemberId] = useState(() => {
    const saved = localStorage.getItem('memberId')
    return saved ? parseInt(saved) : null
  })
  const [name, setName] = useState(() => localStorage.getItem('default-username') || '')
  const [joining, setJoining] = useState(false)
  const [errorMsg, setErrorMsg] = useState('')

  const handleLogin = (data) => {
    localStorage.setItem('token', data.token)
    localStorage.setItem('user', JSON.stringify(data.user))
    setToken(data.token)
  }

  const handleLogout = () => {
    localStorage.removeItem('token')
    localStorage.removeItem('user')
    localStorage.removeItem('memberId')
    setToken(null)
    setMemberId(null)
  }

  const handleResetOnboarding = () => {
    localStorage.removeItem('collaborator-onboarded')
    localStorage.removeItem('token')
    localStorage.removeItem('user')
    localStorage.removeItem('memberId')
    setToken(null)
    setMemberId(null)
    setIsOnboarded(false)
  }

  const handleJoin = async () => {
    const targetName = name.trim() || localStorage.getItem('default-username') || 'NukeDesktop'
    setJoining(true)
    setErrorMsg('')
    try {
      // Auto-ensure valid auth token if missing
      let currentToken = localStorage.getItem('token')
      if (!currentToken) {
        const defaultPass = 'NukeDesktop123!'
        try {
          await register(targetName, defaultPass)
        } catch (e) {
          // Ignore registration error if user exists
        }
        const loginData = await login(targetName, defaultPass)
        if (loginData && loginData.token) {
          currentToken = loginData.token
          localStorage.setItem('token', loginData.token)
          localStorage.setItem('user', JSON.stringify(loginData.user))
          setToken(loginData.token)
        }
      }

      const params = new URLSearchParams(window.location.search)
      let groupId = parseInt(params.get('groupId'))
      if (!groupId || isNaN(groupId)) {
        try {
          const groupList = await fetchAllGroups()
          if (Array.isArray(groupList) && groupList.length > 0) {
            groupId = groupList[0].id
          } else {
            const newGroup = await createGroup('全能 AI 协同组')
            groupId = newGroup.id
          }
        } catch (e) {
          groupId = 3
        }
      }

      const data = await addMember(groupId, targetName)
      if (data && data.id) {
        localStorage.setItem('memberId', data.id)
        setMemberId(data.id)
      } else {
        setErrorMsg(data?.detail || '加入失败，请稍后重试')
      }
    } catch (err) {
      console.error('Join group error:', err)
      setErrorMsg(err.message || '网络连通异常，请稍后重试')
    } finally {
      setJoining(false)
    }
  }

  const handleOnboardingComplete = (authToken) => {
    setIsOnboarded(true)
    if (authToken) {
      setToken(authToken)
    }
  }

  if (!isOnboarded) {
    return <OnboardingWizard onComplete={handleOnboardingComplete} />
  }

  if (!token) return <Login onLogin={handleLogin} />

  if (!memberId) {
    return (
      <div className="h-screen bg-gray-900 flex items-center justify-center">
        <div className="bg-gray-800 rounded-2xl p-8 w-80 shadow-xl space-y-4">
          <div>
            <h1 className="text-white text-xl font-bold mb-1">{t(K.app.joinGroup.title)}</h1>
            <p className="text-gray-400 text-xs">{t(K.app.joinGroup.subtitle)}</p>
          </div>
          {errorMsg && (
            <div className="p-2.5 rounded-lg bg-red-500/20 border border-red-500/30 text-red-300 text-xs">
              {errorMsg}
            </div>
          )}
          <input
            className="w-full bg-gray-700 text-white rounded-lg px-4 py-2.5 text-sm outline-none focus:ring-2 focus:ring-indigo-500"
            placeholder={t(K.app.joinGroup.namePlaceholder)}
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !joining && handleJoin()}
            autoFocus
          />
          <button
            onClick={handleJoin}
            disabled={joining || !name.trim()}
            className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-lg py-2.5 text-sm font-medium transition-colors"
          >
            {joining ? '正在加入项目组...' : t(K.app.joinGroup.submit)}
          </button>
          
          <div className="pt-2 text-center border-t border-gray-700">
            <button
              onClick={handleResetOnboarding}
              className="text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
            >
              ✨ 重新体验初始化向导 (挑选皮肤/设置API)
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <ErrorBoundary>
      <div className="h-screen w-screen flex flex-col overflow-hidden bg-gray-900">
        <Titlebar onResetOnboarding={handleResetOnboarding} />
        <div className="flex-1 min-h-0 overflow-hidden">
          <ChatWindow memberId={memberId} theme={theme} onThemeChange={setTheme} onLogout={handleLogout} />
        </div>
      </div>
    </ErrorBoundary>
  )
}
