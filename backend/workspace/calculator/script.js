// ============================================================
// 计算器 UI 交互层
// 依赖：engine.js（提供 CalculatorEngine）
// ============================================================

(function() {
  const display = document.getElementById('display');
  const resultDisplay = document.getElementById('result-display');
  let expression = '';
  let justCalculated = false;

  function updateDisplay() {
    display.value = expression || '0';
    display.scrollLeft = display.scrollWidth;
  }

  function clearResult() {
    if (resultDisplay) resultDisplay.textContent = '';
  }

  function showResult(value) {
    if (resultDisplay) resultDisplay.textContent = '= ' + value;
  }

  function showError(msg) {
    if (resultDisplay) {
      resultDisplay.textContent = msg;
      resultDisplay.classList.add('error');
    }
  }

  function clearErrorStyle() {
    if (resultDisplay) resultDisplay.classList.remove('error');
  }

  function inputDigit(digit) {
    clearErrorStyle();
    if (justCalculated) { expression = ''; justCalculated = false; clearResult(); }
    expression += digit;
    updateDisplay();
  }

  function inputDot() {
    clearErrorStyle();
    if (justCalculated) { expression = '0.'; justCalculated = false; clearResult(); updateDisplay(); return; }
    let i = expression.length - 1;
    while (i >= 0 && !isOperatorChar(expression[i]) && expression[i] !== '(' && expression[i] !== ')') i--;
    const lastNumber = expression.slice(i + 1);
    if (lastNumber.includes('.')) return;
    expression += (lastNumber === '' ? '0.' : '.');
    updateDisplay();
  }

  function isOperatorChar(ch) { return ['+', '-', '*', '/'].includes(ch); }

  function inputOperator(op) {
    clearErrorStyle();
    if (justCalculated) { justCalculated = false; clearResult(); }
    if (expression === '') expression = '0';
    const lastChar = expression[expression.length - 1];
    if (lastChar && isOperatorChar(lastChar)) expression = expression.slice(0, -1);
    expression += op;
    updateDisplay();
  }

  function inputLParen() {
    clearErrorStyle();
    if (justCalculated) { expression = ''; justCalculated = false; clearResult(); }
    expression += '(';
    updateDisplay();
  }

  function inputRParen() {
    clearErrorStyle();
    if (justCalculated) { justCalculated = false; clearResult(); }
    expression += ')';
    updateDisplay();
  }

  function backspace() {
    clearErrorStyle();
    if (justCalculated) { expression = ''; justCalculated = false; clearResult(); updateDisplay(); return; }
    expression = expression.slice(0, -1);
    updateDisplay();
  }

  function clearAll() {
    expression = '';
    justCalculated = false;
    clearResult();
    clearErrorStyle();
    updateDisplay();
  }

  function calculate() {
    clearErrorStyle();
    if (!expression || expression.trim() === '') { showError('请输入表达式'); return; }
    const lastChar = expression[expression.length - 1];
    if (lastChar && (isOperatorChar(lastChar) || lastChar === '(')) { showError('表达式不完整'); return; }
    let balance = 0;
    for (const ch of expression) { if (ch === '(') balance++; if (ch === ')') balance--; if (balance < 0) { showError('括号不匹配'); return; } }
    if (balance !== 0) { showError('括号不匹配'); return; }

    const result = CalculatorEngine.calculate(expression);
    if (result.success) {
      showResult(result.result);
      expression = String(result.result);
      justCalculated = true;
    } else {
      showError(result.error);
    }
    updateDisplay();
  }

  function handleKeyboard(e) {
    const key = e.key;
    if (key >= '0' && key <= '9') { e.preventDefault(); inputDigit(key); return; }
    if (key === '.') { e.preventDefault(); inputDot(); return; }
    if (key === '+' || key === '-' || key === '*' || key === '/') { e.preventDefault(); inputOperator(key); return; }
    if (key === '(') { e.preventDefault(); inputLParen(); return; }
    if (key === ')') { e.preventDefault(); inputRParen(); return; }
    if (key === 'Enter' || key === '=') { e.preventDefault(); calculate(); return; }
    if (key === 'Backspace') { e.preventDefault(); backspace(); return; }
    if (key === 'Escape' || key === 'c' || key === 'C') { e.preventDefault(); clearAll(); return; }
  }

  document.addEventListener('DOMContentLoaded', () => {
    updateDisplay();
    document.querySelectorAll('.btn-digit').forEach(btn => btn.addEventListener('click', () => inputDigit(btn.dataset.digit)));
    document.querySelector('.btn-dot')?.addEventListener('click', inputDot);
    document.querySelectorAll('.btn-operator').forEach(btn => btn.addEventListener('click', () => inputOperator(btn.dataset.operator)));
    document.querySelector('.btn-lparen')?.addEventListener('click', inputLParen);
    document.querySelector('.btn-rparen')?.addEventListener('click', inputRParen);
    document.querySelector('.btn-backspace')?.addEventListener('click', backspace);
    document.querySelector('.btn-clear')?.addEventListener('click', clearAll);
    document.querySelector('.btn-equals')?.addEventListener('click', calculate);
    document.addEventListener('keydown', handleKeyboard);
  });
})();
