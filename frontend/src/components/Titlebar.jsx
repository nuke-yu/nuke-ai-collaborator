import React from 'react';
import { useTranslation } from 'react-i18next';
import { useThemeStore } from '../store/useThemeStore';

export default function Titlebar({ activeGroupName, onResetOnboarding }) {
  const { t } = useTranslation();
  const { theme } = useThemeStore();
  const isElectron = typeof window !== 'undefined' && (Boolean(window.electronAPI?.isElectron) || Boolean(window.electron) || true);

  return (
    <div
      className="w-full h-9 flex items-center justify-between px-3 border-b flex-shrink-0 select-none text-xs transition-colors z-40"
      style={{
        backgroundColor: 'var(--sidebar-background)',
        borderColor: 'var(--sidebar-border)',
        color: 'var(--sidebar-foreground)',
        WebkitAppRegion: 'drag',
      }}
    >
      {/* Left side: traffic light offset for macOS Electron + App Logo */}
      <div className={`flex items-center gap-2.5 ${isElectron ? 'pl-20' : ''}`}>
        <div className="flex items-center gap-1.5 font-bold tracking-tight">
          <span className="w-2 h-2 rounded-full bg-indigo-500 animate-pulse"></span>
          <span className="text-white/90 font-semibold">Nuke AI Collaborator</span>
        </div>
        {activeGroupName && (
          <>
            <span className="opacity-30">/</span>
            <span className="opacity-75 font-medium truncate max-w-[200px]">
              {activeGroupName}
            </span>
          </>
        )}
      </div>

      {/* Right side: quick actions (no-drag) */}
      <div className="flex items-center gap-2" style={{ WebkitAppRegion: 'no-drag' }}>
        <button
          onClick={onResetOnboarding}
          className="px-2 py-0.5 rounded text-[11px] opacity-60 hover:opacity-100 hover:bg-white/10 transition-all flex items-center gap-1"
          title="重新打开初始化向导"
        >
          <span>✨ 导引</span>
        </button>
      </div>
    </div>
  );
}
