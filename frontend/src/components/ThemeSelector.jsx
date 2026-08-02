import React, { useState } from 'react';
import { useThemeStore } from '../store/useThemeStore';

export default function ThemeSelector() {
  const { theme, themes, setTheme } = useThemeStore();
  const [isOpen, setIsOpen] = useState(false);

  const activeTheme = themes.find((t) => t.id === theme) || themes[0];

  return (
    <div className="relative inline-block text-left">
      <button
        onClick={(e) => { e.stopPropagation(); setIsOpen(!isOpen); }}
        className="flex items-center gap-1.5 px-2 py-1 rounded text-xs transition-colors hover:bg-white/10 opacity-80 hover:opacity-100"
        title="换肤"
      >
        <span>🎨</span>
        <span>{activeTheme.icon} {activeTheme.name.split(' ')[0]}</span>
      </button>

      {isOpen && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setIsOpen(false)} />
          <div
            className="absolute left-0 top-full mt-1.5 w-64 rounded-xl shadow-2xl z-50 border p-2 space-y-1 backdrop-blur-md animate-scale-up"
            style={{
              backgroundColor: 'var(--popover)',
              borderColor: 'var(--border)',
              color: 'var(--popover-foreground)',
            }}
          >
            <div className="px-2 py-1 text-[11px] font-semibold uppercase tracking-wider opacity-60 flex items-center justify-between">
              <span>全量皮肤库 (13 套)</span>
              <span className="text-[10px] text-indigo-400">实时预览</span>
            </div>

            <div className="max-h-80 overflow-y-auto space-y-0.5 custom-scrollbar">
              {themes.map((item) => {
                const isSelected = item.id === theme;
                return (
                  <button
                    key={item.id}
                    onClick={() => {
                      setTheme(item.id);
                      setIsOpen(false);
                    }}
                    className={`w-full flex items-center justify-between px-3 py-2 text-xs rounded-lg transition-all ${
                      isSelected ? 'font-bold' : 'hover:opacity-80'
                    }`}
                    style={{
                      backgroundColor: isSelected ? 'var(--accent)' : 'transparent',
                      color: isSelected ? 'var(--accent-foreground)' : 'var(--foreground)',
                    }}
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-sm">{item.icon}</span>
                      <span>{item.name}</span>
                    </div>
                    {isSelected && (
                      <span className="text-emerald-400 text-xs font-bold">✓</span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
