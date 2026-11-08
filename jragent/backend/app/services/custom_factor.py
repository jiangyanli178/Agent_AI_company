"""
自定义因子 —— 让 AI 提议内置库之外的新因子，并且**真的算得出来**。

用户的原话是「AI 不能去网上找需要的因子吗」。
光"找"没用：说得出名字、算不出数字，回测里就是编的。
所以这里给一个受限的表达式引擎：AI 把因子写成公式，系统解析、求值、回测验证，
验证不过就直接告诉它"这个算式算不出来"，而不是让它编一个结论。

安全边界（这是关键，不能省）：
- 只允许四则运算、括号、数字
- 只允许 close/open/high/low/volume 五个字段
- 只允许白名单函数（ma/ema/std/hhv/llv/ref/abs/max/min）
- 禁止属性访问、下标、比较、lambda、导入、任何函数名以外的调用

例：
  (close - ma(close, 20)) / ma(close, 20) * 100       # 类似乖离率
  (high - low) / close * 100                          # 日内振幅
  volume / ma(volume, 20)                             # 量比
  ma(close, 5) / ma(close, 20) - 1                    # 双均线距离
"""
from __future__ import annotations

import ast
import math
from typing import Any, Dict, List, Optional

try:
    from .backtest import Bar
except ImportError:
    from backtest import Bar


class FactorError(ValueError):
    """表达式不合法或算不出来"""


class _Series(list):
    """轻量序列，为了能让表达式里的 + - * / 直接作用到整列数据

    注意：任何一个操作数是 None（比如 ref() 往前挪出来的空位），
    结果就是 None，而不是抛异常。新手写的公式经常会撞上这种边界。
    """

    def _pair(self, other):
        if isinstance(other, (int, float)):
            return self, [other] * len(self)
        if isinstance(other, (list, tuple)):
            if len(other) != len(self):
                raise FactorError("长度不一致的序列不能直接运算")
            return self, list(other)
        raise FactorError(f"不支持和 {type(other).__name__} 运算")

    @staticmethod
    def _bin(a, b, fn):
        out = []
        for x, y in zip(a, b):
            if x is None or y is None:
                out.append(None)
            else:
                try:
                    out.append(fn(x, y))
                except ZeroDivisionError:
                    out.append(None)
        return _Series(out)

    def __add__(self, o):
        a, b = self._pair(o); return self._bin(a, b, lambda x, y: x + y)

    def __radd__(self, o):
        return self.__add__(o)

    def __sub__(self, o):
        a, b = self._pair(o); return self._bin(a, b, lambda x, y: x - y)

    def __rsub__(self, o):
        a, b = self._pair(o); return self._bin(a, b, lambda x, y: y - x)

    def __mul__(self, o):
        a, b = self._pair(o); return self._bin(a, b, lambda x, y: x * y)

    def __rmul__(self, o):
        return self.__mul__(o)

    def __truediv__(self, o):
        a, b = self._pair(o); return self._bin(a, b, lambda x, y: x / y)

    def __rtruediv__(self, o):
        a, b = self._pair(o); return self._bin(a, b, lambda x, y: y / x)

    def __neg__(self):
        return _Series([-x if x is not None else None for x in self])


def _clean(seq) -> _Series:
    """None 透传，参与运算时按缺失处理"""
    return _Series([None if (v is None or (isinstance(v, float) and math.isnan(v))) else float(v)
                    for v in seq])


def _ma(seq, n):
    n = int(n)
    out, s = [], 0.0
    buf = []
    for i, v in enumerate(seq):
        if v is None:
            buf.append(None)
            out.append(None)
            continue
        buf.append(v)
        if len(buf) > n:
            buf.pop(0)
        if len(buf) == n and all(x is not None for x in buf):
            out.append(sum(buf) / n)
        else:
            out.append(None)
    return _Series(out)


def _ema(seq, n):
    n = int(n)
    k = 2 / (n + 1)
    out, prev = [], None
    for v in seq:
        if v is None:
            out.append(prev)
            continue
        prev = v if prev is None else v * k + prev * (1 - k)
        out.append(prev)
    return _Series(out)


def _std(seq, n):
    n = int(n)
    out, buf = [], []
    for v in seq:
        buf.append(v)
        if len(buf) > n:
            buf.pop(0)
        vals = [x for x in buf if x is not None]
        if len(vals) < n:
            out.append(None)
            continue
        m = sum(vals) / n
        out.append(math.sqrt(sum((x - m) ** 2 for x in vals) / n))
    return _Series(out)


def _hhv(seq, n):
    n = int(n)
    out, buf = [], []
    for v in seq:
        buf.append(v)
        if len(buf) > n:
            buf.pop(0)
        vals = [x for x in buf if x is not None]
        out.append(max(vals) if vals else None)
    return _Series(out)


def _llv(seq, n):
    n = int(n)
    out, buf = [], []
    for v in seq:
        buf.append(v)
        if len(buf) > n:
            buf.pop(0)
        vals = [x for x in buf if x is not None]
        out.append(min(vals) if vals else None)
    return _Series(out)


def _ref(seq, n):
    n = int(n)
    return _Series([None] * n + list(seq[:-n]) if n > 0 else list(seq))


def _abs(seq):
    return _Series([None if v is None else abs(v) for v in seq])


def _max(*args):
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        args = args[0]
    vals = [a for a in args if a is not None]
    return max(vals) if vals else None


def _min(*args):
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        args = args[0]
    vals = [a for a in args if a is not None]
    return min(vals) if vals else None


ALLOWED_FUNCS = {
    "ma": _ma, "ema": _ema, "std": _std, "hhv": _hhv, "llv": _llv,
    "ref": _ref, "abs": _abs, "max": _max, "min": _min,
}

ALLOWED_FIELDS = ("close", "open", "high", "low", "volume")

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Load,
    ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd,
)


def _validate(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise FactorError(
                f"表达式里不允许出现 {type(node).__name__}，只能用四则运算和 "
                "ma/ema/std/hhv/llv/ref/abs/max/min"
            )
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise FactorError("函数调用只允许直接写函数名")
            if node.func.id not in ALLOWED_FUNCS:
                raise FactorError(f"不支持函数 {node.func.id}")
            if node.keywords:
                raise FactorError("不支持关键字参数")
        if isinstance(node, ast.Name):
            if node.id not in ALLOWED_FUNCS and node.id not in ALLOWED_FIELDS:
                raise FactorError(f"未知字段或函数名：{node.id}")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise FactorError("只允许数字常量")


def evaluate(expression: str, bars: List[Bar]) -> List[Optional[float]]:
    """把表达式在这段 K 线上算出来"""
    expr = (expression or "").strip()
    if not expr:
        raise FactorError("表达式为空")
    if len(expr) > 400:
        raise FactorError("表达式太长（上限 400 字符）")

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise FactorError(f"公式写错了：{e.msg}")

    _validate(tree)

    env: Dict[str, Any] = {
        "close": _clean([b.close for b in bars]),
        "open": _clean([b.open for b in bars]),
        "high": _clean([b.high for b in bars]),
        "low": _clean([b.low for b in bars]),
        "volume": _clean([b.volume for b in bars]),
        **ALLOWED_FUNCS,
    }

    try:
        val = eval(compile(tree, "<factor>", "eval"), {"__builtins__": {}}, env)
    except FactorError:
        raise
    except ZeroDivisionError:
        raise FactorError("算出来除以零了，检查分母")
    except Exception as e:
        raise FactorError(f"算不出来：{type(e).__name__}: {e}")

    if isinstance(val, (int, float)):
        # 常量也算合法，但没有区分度，直接拒绝
        raise FactorError("公式结果是一个常数，没有任何区分度")

    if not isinstance(val, (list, tuple)):
        raise FactorError("公式结果不是序列")

    out = [None if v is None else float(v) for v in val]
    valid = [v for v in out if v is not None]
    if len(valid) < len(out) * 0.3:
        raise FactorError("有效值太少（超过 70% 是空的），这个公式在这段数据上算不出来")

    # 全是同一个值也没用
    if len(set(round(v, 6) for v in valid)) <= 1:
        raise FactorError("公式结果恒定不变，没有区分度")

    return out


def register(expression: str, bars: List[Bar], name: str = "",
             desc: str = "") -> Dict[str, Any]:
    """
    验证并返回一个可用的自定义因子。
    同时给出描述性统计，让 AI 判断它是否合理。
    """
    seq = evaluate(expression, bars)
    valid = sorted(v for v in seq if v is not None)
    n = len(valid)
    q = lambda p: valid[min(n - 1, int(n * p))]

    return {
        "name": name or "custom",
        "expression": expression,
        "desc": desc,
        "series": seq,
        "stats": {
            "valid": n,
            "min": round(valid[0], 4),
            "p25": round(q(0.25), 4),
            "median": round(q(0.5), 4),
            "p75": round(q(0.75), 4),
            "max": round(valid[-1], 4),
        },
    }
