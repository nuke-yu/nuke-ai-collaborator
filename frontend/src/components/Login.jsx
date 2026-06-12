import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { login, register } from '../api'
import { K } from '../i18n/keys'

export default function Login({ onLogin }) {
  const { t } = useTranslation()
  const [isRegister, setIsRegister] = useState(false)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [email, setEmail] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      if (isRegister) {
        await register(username, password, email)
        setIsRegister(false)
        setError(t(K.auth.register.successMessage))
      } else {
        const data = await login(username, password)
        onLogin(data)
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="h-screen bg-gray-900 flex items-center justify-center p-4">
      <div className="bg-gray-800 rounded-2xl p-8 w-full max-w-sm shadow-2xl border border-gray-700">
        <h1 className="text-white text-2xl font-bold mb-2">{t(K.auth.login.appTitle)}</h1>
        <p className="text-gray-400 text-sm mb-6">{isRegister ? t(K.auth.register.title) : t(K.auth.login.subtitle)}</p>

        {error && (
          <div className="bg-red-900/30 border border-red-500/50 text-red-400 text-xs p-3 rounded-lg mb-4">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">{t(K.auth.login.username)}</label>
            <input
              required
              className="w-full bg-gray-900 text-white rounded-lg px-4 py-2.5 text-sm outline-none focus:ring-2 focus:ring-indigo-500 border border-gray-700"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </div>

          {isRegister && (
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">{t(K.auth.register.email)}</label>
              <input
                className="w-full bg-gray-900 text-white rounded-lg px-4 py-2.5 text-sm outline-none focus:ring-2 focus:ring-indigo-500 border border-gray-700"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
          )}

          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">{t(K.auth.login.password)}</label>
            <input
              required
              type="password"
              className="w-full bg-gray-900 text-white rounded-lg px-4 py-2.5 text-sm outline-none focus:ring-2 focus:ring-indigo-500 border border-gray-700"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-lg py-2.5 text-sm font-medium transition-colors shadow-lg shadow-indigo-900/20"
          >
            {loading ? t(K.auth.login.loading) : (isRegister ? t(K.auth.register.button) : t(K.auth.login.button))}
          </button>
        </form>

        <div className="mt-6 pt-6 border-t border-gray-700 text-center">
          <button
            onClick={() => setIsRegister(!isRegister)}
            className="text-indigo-400 hover:text-indigo-300 text-xs font-medium transition-colors"
          >
            {isRegister ? t(K.auth.register.switchToLogin) : t(K.auth.login.switchToRegister)}
          </button>
        </div>
      </div>
    </div>
  )
}
