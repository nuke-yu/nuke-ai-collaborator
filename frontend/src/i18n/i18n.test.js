// frontend/src/i18n/i18n.test.js
import { describe, it, expect } from 'vitest'
import { K } from './keys'
import zh from './locales/zh.json'
import en from './locales/en.json'

// All dotted key strings referenced by the K registry.
function collectKeys(obj, out = []) {
  for (const v of Object.values(obj)) {
    if (typeof v === 'string') out.push(v)
    else if (v && typeof v === 'object') collectKeys(v, out)
  }
  return out
}

// All dotted leaf paths present in a locale resource.
function collectPaths(obj, prefix = '', out = []) {
  for (const [k, v] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${k}` : k
    if (v && typeof v === 'object') collectPaths(v, path, out)
    else out.push(path)
  }
  return out
}

const referenced = collectKeys(K)
const zhPaths = new Set(collectPaths(zh))
const enPaths = new Set(collectPaths(en))

describe('i18n key parity', () => {
  it('every K key resolves in zh', () => {
    expect(referenced.filter((k) => !zhPaths.has(k))).toEqual([])
  })
  it('every K key resolves in en', () => {
    expect(referenced.filter((k) => !enPaths.has(k))).toEqual([])
  })
  it('zh and en have identical leaf paths', () => {
    const onlyZh = [...zhPaths].filter((p) => !enPaths.has(p))
    const onlyEn = [...enPaths].filter((p) => !zhPaths.has(p))
    expect({ onlyZh, onlyEn }).toEqual({ onlyZh: [], onlyEn: [] })
  })
})
