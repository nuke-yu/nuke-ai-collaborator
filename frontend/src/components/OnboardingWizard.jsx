import React, { useState } from 'react';
import { useThemeStore } from '../store/useThemeStore';
import { login, register, saveConfig } from '../api';

export default function OnboardingWizard({ onComplete }) {
  const [step, setStep] = useState(1);
  const [loading, setLoading] = useState(false);
  const { theme, themes, setTheme } = useThemeStore();

  // Form State
  const [username, setUsername] = useState('');
  const [provider, setProvider] = useState('deepseek');
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('https://api.deepseek.com/v1');

  const handleNext = async () => {
    if (step < 3) {
      setStep(step + 1);
    } else {
      setLoading(true);
      const uname = username.trim() || 'Nuke';
      const defaultPassword = 'password123';

      localStorage.setItem('collaborator-onboarded', 'true');
      localStorage.setItem('default-username', uname);
      localStorage.setItem('model-provider', provider);
      localStorage.setItem('model-api-key', apiKey);
      localStorage.setItem('model-base-url', baseUrl);

      let authToken = null;
      try {
        await register(uname, defaultPassword);
      } catch (err) {
        // User may already exist, proceed to login
      }

      try {
        const loginData = await login(uname, defaultPassword);
        if (loginData && loginData.token) {
          authToken = loginData.token;
          localStorage.setItem('token', loginData.token);
          localStorage.setItem('user', JSON.stringify(loginData.user));

          // Sync entered LLM API key to backend app_config.json
          if (apiKey.trim()) {
            const providerKeyMap = {
              deepseek: 'deepseek_api_key',
              openai: 'openai_api_key',
              anthropic: 'anthropic_api_key',
              ollama: 'ollama_base_url',
              minimax: 'minimax_api_key',
              zhipu: 'zhipu_api_key',
              qwen: 'qwen_api_key',
            };
            const targetField = providerKeyMap[provider] || 'deepseek_api_key';
            try {
              await saveConfig({ [targetField]: apiKey.trim() });
            } catch (err) {
              console.warn('Backend API key sync skipped/failed:', err);
            }
          }
        }
      } catch (err) {
        console.warn('Auto-login skipped:', err);
      } finally {
        setLoading(false);
        onComplete(authToken);
      }
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-xl p-4">
      <div
        className="w-full max-w-xl rounded-2xl border shadow-2xl p-8 space-y-6 transition-all duration-300"
        style={{
          backgroundColor: 'var(--card)',
          borderColor: 'var(--border)',
          color: 'var(--card-foreground)',
        }}
      >
        {/* Header Step Indicator */}
        <div className="flex items-center justify-between border-b pb-4" style={{ borderColor: 'var(--border)' }}>
          <div className="flex items-center gap-3">
            <span className="w-8 h-8 rounded-full flex items-center justify-center font-bold text-sm" style={{ backgroundColor: 'var(--primary)', color: 'var(--primary-foreground)' }}>
              {step}
            </span>
            <div>
              <h2 className="text-lg font-bold">欢迎使用 Nuke AI Collaborator</h2>
              <p className="text-xs opacity-60">桌面应用初始化向导 ({step}/3)</p>
            </div>
          </div>

          {/* Progress dots */}
          <div className="flex gap-1.5">
            {[1, 2, 3].map((i) => (
              <div
                key={i}
                className={`w-2.5 h-2.5 rounded-full transition-all ${
                  i === step ? 'w-6' : 'opacity-30'
                }`}
                style={{ backgroundColor: 'var(--primary)' }}
              />
            ))}
          </div>
        </div>

        {/* Step 1: Theme selection */}
        {step === 1 && (
          <div className="space-y-4">
            <h3 className="text-base font-semibold">1. 选择您偏好的默认桌面皮肤</h3>
            <p className="text-xs opacity-70">支持 10 套经典主题，后续可在顶部工具栏随时一键切换。</p>

            <div className="grid grid-cols-2 gap-2.5 max-h-56 overflow-y-auto pr-1">
              {themes.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setTheme(t.id)}
                  className={`flex items-center justify-between p-3 rounded-xl border text-xs font-medium text-left transition-all ${
                    theme === t.id ? 'ring-2 ring-offset-1' : 'opacity-80 hover:opacity-100'
                  }`}
                  style={{
                    backgroundColor: theme === t.id ? 'var(--accent)' : 'var(--background)',
                    borderColor: 'var(--border)',
                    color: 'var(--foreground)',
                  }}
                >
                  <div className="flex items-center gap-2">
                    <span className="w-3 h-3 rounded-full" style={{ backgroundColor: 'var(--primary)' }} />
                    <span>{t.name}</span>
                  </div>
                  {theme === t.id && <span className="text-emerald-400 font-bold">✓</span>}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Step 2: User Identity */}
        {step === 2 && (
          <div className="space-y-4">
            <h3 className="text-base font-semibold">2. 设置您的默认称呼</h3>
            <p className="text-xs opacity-70">组内的 AI Bot 伙伴（BA, Dev, QA, PM 等）会使用此名称在协同对话中回应您。</p>

            <div className="space-y-2">
              <label className="text-xs font-medium opacity-80">您的名字 / 昵称</label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="例如: Nuke / 开发者"
                className="w-full px-4 py-3 rounded-xl border outline-none text-sm focus:ring-2 transition-all"
                style={{
                  backgroundColor: 'var(--background)',
                  borderColor: 'var(--border)',
                  color: 'var(--foreground)',
                }}
                autoFocus
              />
            </div>
          </div>
        )}

        {/* Step 3: AI Provider Setup */}
        {step === 3 && (
          <div className="space-y-4">
            <h3 className="text-base font-semibold">3. 配置 AI 大模型 API 服务</h3>
            <p className="text-xs opacity-70">设置系统默认的模型服务提供商，支持云端 API 与本地 Ollama 引擎。</p>

            <div className="space-y-3 text-xs">
              <div>
                <label className="font-medium opacity-80 block mb-1">模型服务提供商</label>
                <select
                  value={provider}
                  onChange={(e) => {
                    setProvider(e.target.value);
                    if (e.target.value === 'deepseek') setBaseUrl('https://api.deepseek.com/v1');
                    if (e.target.value === 'openai') setBaseUrl('https://api.openai.com/v1');
                    if (e.target.value === 'ollama') setBaseUrl('http://localhost:11434/v1');
                  }}
                  className="w-full px-3 py-2.5 rounded-xl border outline-none text-xs"
                  style={{ backgroundColor: 'var(--background)', borderColor: 'var(--border)', color: 'var(--foreground)' }}
                >
                  <option value="deepseek">DeepSeek API (推荐)</option>
                  <option value="openai">OpenAI (GPT-4o)</option>
                  <option value="anthropic">Anthropic Claude</option>
                  <option value="ollama">Ollama 本地大模型</option>
                  <option value="custom">自定义 OpenAI 兼容 Base URL</option>
                </select>
              </div>

              <div>
                <label className="font-medium opacity-80 block mb-1">API Key</label>
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="sk-..."
                  className="w-full px-3 py-2.5 rounded-xl border outline-none text-xs"
                  style={{ backgroundColor: 'var(--background)', borderColor: 'var(--border)', color: 'var(--foreground)' }}
                />
              </div>

              <div>
                <label className="font-medium opacity-80 block mb-1">Base URL</label>
                <input
                  type="text"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="https://..."
                  className="w-full px-3 py-2.5 rounded-xl border outline-none text-xs"
                  style={{ backgroundColor: 'var(--background)', borderColor: 'var(--border)', color: 'var(--foreground)' }}
                />
              </div>
            </div>
          </div>
        )}

        {/* Footer Navigation Buttons */}
        <div className="flex items-center justify-between pt-4 border-t" style={{ borderColor: 'var(--border)' }}>
          {step > 1 ? (
            <button
              onClick={() => setStep(step - 1)}
              className="px-4 py-2 rounded-xl text-xs font-medium border transition-colors opacity-80 hover:opacity-100"
              style={{ backgroundColor: 'var(--background)', borderColor: 'var(--border)' }}
            >
              上一步
            </button>
          ) : <div />}

          <button
            onClick={handleNext}
            className="px-6 py-2.5 rounded-xl text-xs font-semibold shadow-lg transition-all"
            style={{ backgroundColor: 'var(--primary)', color: 'var(--primary-foreground)' }}
          >
            {step === 3 ? '完成配置并进入协同平台 ✨' : '下一步 →'}
          </button>
        </div>
      </div>
    </div>
  );
}
