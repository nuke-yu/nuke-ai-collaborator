import "@testing-library/jest-dom/vitest";

// jsdom in this config doesn't expose a usable localStorage at setup time, and the
// app i18n (i18n/index.js) reads localStorage.getItem('lang') at import. Stub it
// first, then dynamic-import i18n so components rendering via t(K...) produce
// translated text (default lng 'zh') instead of raw keys — matching production.
// (Static `import` is hoisted, so a dynamic import is required to run after the stub.)
if (typeof globalThis.localStorage?.getItem !== "function") {
  const _store = {};
  globalThis.localStorage = {
    getItem: (k) => (k in _store ? _store[k] : null),
    setItem: (k, v) => { _store[k] = String(v); },
    removeItem: (k) => { delete _store[k]; },
    clear: () => { Object.keys(_store).forEach((k) => delete _store[k]); },
  };
}

await import("../i18n");
