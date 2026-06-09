/**
 * CalculatorEngine - 四则运算引擎
 * 
 * 支持：加减乘除、括号、小数、负数
 * 算法：Shunting-yard（中缀转后缀）+ 后缀求值
 */
const CalculatorEngine = (() => {
  const TT = { NUMBER: 'NUMBER', OPERATOR: 'OPERATOR', LPAREN: 'LPAREN', RPAREN: 'RPAREN' };
  const PREC = { '+': 1, '-': 1, '*': 2, '/': 2 };
  const ASSOC = { '+': 'LEFT', '-': 'LEFT', '*': 'LEFT', '/': 'LEFT' };

  function isDigit(ch) { return ch >= '0' && ch <= '9'; }
  function isOp(ch) { return ch === '+' || ch === '-' || ch === '*' || ch === '/'; }

  /**
   * 词法分析
   * 处理：数字(含小数)、运算符、括号、一元负号
   */
  function tokenize(expr) {
    const tokens = [];
    let i = 0;
    const s = expr.replace(/\s+/g, '');
    
    while (i < s.length) {
      const ch = s[i];

      // 数字或小数点开头
      if (isDigit(ch) || (ch === '.' && i + 1 < s.length && isDigit(s[i + 1]))) {
        let num = '';
        let dot = false;
        while (i < s.length && (isDigit(s[i]) || s[i] === '.')) {
          if (s[i] === '.') { if (dot) throw new CalcErr('小数点重复'); dot = true; }
          num += s[i]; i++;
        }
        tokens.push({ type: TT.NUMBER, value: num });
        continue;
      }

      // 运算符（但 '-' 可能是一元，单独处理）
      if (ch === '+' || ch === '*' || ch === '/') {
        tokens.push({ type: TT.OPERATOR, value: ch });
        i++;
        continue;
      }

      // 负号：判断是一元还是二元
      if (ch === '-') {
        // 看前一个 token：如果不存在 / 是左括号 / 是运算符 → 一元
        const prev = tokens.length > 0 ? tokens[tokens.length - 1] : null;
        const isUnary = !prev || prev.type === TT.LPAREN || prev.type === TT.OPERATOR;
        
        if (isUnary) {
          // 一元负号：吃掉后面的数字（或小数点开头数字）
          i++;
          if (i >= s.length) throw new CalcErr('表达式不完整');
          
          // 后面必须跟数字或小数点
          if (isDigit(s[i]) || s[i] === '.') {
            let num = '-';
            let dot = false;
            while (i < s.length && (isDigit(s[i]) || s[i] === '.')) {
              if (s[i] === '.') { if (dot) throw new CalcErr('小数点重复'); dot = true; }
              num += s[i]; i++;
            }
            tokens.push({ type: TT.NUMBER, value: num });
          } else {
            // 后面不是数字，当作二元运算符（应该不会到这里，但兜底）
            tokens.push({ type: TT.OPERATOR, value: '-' });
          }
        } else {
          // 二元减号
          tokens.push({ type: TT.OPERATOR, value: '-' });
          i++;
        }
        continue;
      }

      // 括号
      if (ch === '(') { tokens.push({ type: TT.LPAREN, value: '(' }); i++; continue; }
      if (ch === ')') { tokens.push({ type: TT.RPAREN, value: ')' }); i++; continue; }

      throw new CalcErr('非法字符: "' + ch + '"');
    }

    return tokens;
  }

  /**
   * Shunting-yard: 中缀 → 后缀
   */
  function toPostfix(tokens) {
    const out = [];
    const stack = [];

    for (const t of tokens) {
      switch (t.type) {
        case TT.NUMBER:
          out.push(t);
          break;
        case TT.OPERATOR:
          while (stack.length > 0 && stack[stack.length - 1].type === TT.OPERATOR &&
            (PREC[stack[stack.length - 1].value] > PREC[t.value] ||
            (PREC[stack[stack.length - 1].value] === PREC[t.value] && ASSOC[t.value] === 'LEFT'))) {
            out.push(stack.pop());
          }
          stack.push(t);
          break;
        case TT.LPAREN:
          stack.push(t);
          break;
        case TT.RPAREN:
          while (stack.length > 0 && stack[stack.length - 1].type !== TT.LPAREN) {
            out.push(stack.pop());
          }
          if (stack.length === 0) throw new CalcErr('括号不匹配');
          stack.pop(); // 丢弃 '('
          break;
      }
    }

    while (stack.length > 0) {
      const t = stack.pop();
      if (t.type === TT.LPAREN) throw new CalcErr('括号不匹配');
      out.push(t);
    }

    return out;
  }

  /**
   * 后缀求值
   */
  function evalPostfix(postfix) {
    const stack = [];
    for (const t of postfix) {
      if (t.type === TT.NUMBER) {
        stack.push(parseFloat(t.value));
      } else if (t.type === TT.OPERATOR) {
        if (stack.length < 2) throw new CalcErr('表达式不完整');
        const b = stack.pop();
        const a = stack.pop();
        let r;
        switch (t.value) {
          case '+': r = a + b; break;
          case '-': r = a - b; break;
          case '*': r = a * b; break;
          case '/':
            if (b === 0) throw new CalcErr('除数不能为0');
            r = a / b; break;
          default: throw new CalcErr('未知运算符: ' + t.value);
        }
        stack.push(r);
      }
    }
    if (stack.length !== 1) throw new CalcErr('表达式不完整');
    return stack[0];
  }

  class CalcErr extends Error {
    constructor(msg) { super(msg); this.name = 'CalcErr'; }
  }

  /**
   * 公开 API：计算表达式
   * @param {string} expr
   * @returns {{ success: boolean, result?: number, error?: string }}
   */
  function calculate(expr) {
    try {
      if (!expr || expr.trim() === '') return { success: false, error: '请输入表达式' };
      const tokens = tokenize(expr);
      if (tokens.length === 0) return { success: false, error: '请输入表达式' };
      const postfix = toPostfix(tokens);
      const result = evalPostfix(postfix);
      return { success: true, result: parseFloat(result.toFixed(10)) };
    } catch (err) {
      if (err instanceof CalcErr) return { success: false, error: err.message };
      return { success: false, error: '计算错误' };
    }
  }

  return { calculate, CalcErr };
})();

if (typeof module !== 'undefined' && module.exports) {
  module.exports = CalculatorEngine;
}
