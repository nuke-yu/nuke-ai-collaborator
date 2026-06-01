import ErrorBoundary from './components/ErrorBoundary'
import { useState, useEffect } from 'react'
import { addMember } from './api'
import ChatWindow from './components/ChatWindow'

export default function App() {
  const [memberId, setMemberId] = useState(() => {
    const saved = localStorage.getItem('memberId')
    return saved ? parseInt(saved) : null
  })
  const [name, setName] = useState('')
  const [isDark, setIsDark] = useState(() => localStorage.getItem('theme') !== 'light')

  useEffect(() => {
    document.documentElement.classList.toggle('light', !isDark)
    localStorage.setItem('theme', isDark ? 'dark' : 'light')
  }, [isDark])

  const handleJoin = async () => {
    if (!name.trim()) return
    const params = new URLSearchParams(window.location.search)
    const groupId = parseInt(params.get('groupId')) || 1
    const data = await addMember(groupId, name.trim())
    localStorage.setItem('memberId', data.id)
    setMemberId(data.id)
  }

  if (!memberId) {
    return (
      <div className="h-screen bg-gray-900 flex items-center justify-center">
        <div className="bg-gray-800 rounded-2xl p-8 w-80 shadow-xl">
          <h1 className="text-white text-xl font-bold mb-2">AI 协作工作区</h1>
          <p className="text-gray-400 text-sm mb-6">输入你的名字加入群组</p>
          <input
            className="w-full bg-gray-700 text-white rounded-lg px-4 py-2.5 text-sm outline-none focus:ring-2 focus:ring-indigo-500 mb-4"
            placeholder="你的名字"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleJoin()}
            autoFocus
          />
          <button
            onClick={handleJoin}
            className="w-full bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg py-2.5 text-sm font-medium transition-colors"
          >
            加入
          </button>
        </div>
      </div>
    )
  }

  return (
    <ErrorBoundary>
      <ChatWindow memberId={memberId} isDark={isDark} onToggleTheme={() => setIsDark(d => !d)} />
    </ErrorBoundary>
  )
}
