"""Парсер и вычислитель формул для pnl_metrics.

Грамматика — подмножество математических выражений Python:

  expr   := term (('+' | '-') term)*
  term   := factor (('*' | '/') factor)*
  factor := NUMBER | LINE_REF | '-' factor | '(' expr ')'
  LINE_REF := '[' INTEGER ']'

Внутри ссылка `[N]` — `line_no` узла шаблона PnL. При вычислении
подставляется заранее посчитанный rollup-amount этого узла (direct + сумма
потомков). Деление на ноль → возвращает None (рисуется в UI как «—»).

Парсер реализован через ast.parse + walk: используем штатный Python-AST,
явно whitelist'им только нужные узлы, всё остальное — SyntaxError. То есть
никакого `eval`, никаких вызовов функций, никаких атрибутов.

Поддержка `[N]` через хак: перед парсингом подменяем `[N]` на функцию
`_L(N)`, парсим, и при walk'е разворачиваем `Call(_L, [Constant(N)])`
обратно в `LineRef(N)`.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Optional, Union


# ---------- AST nodes ----------

@dataclass(frozen=True)
class Number:
    value: float


@dataclass(frozen=True)
class LineRef:
    line_no: int


@dataclass(frozen=True)
class BinOp:
    op: str  # '+', '-', '*', '/'
    left: "Node"
    right: "Node"


@dataclass(frozen=True)
class UnaryMinus:
    operand: "Node"


Node = Union[Number, LineRef, BinOp, UnaryMinus]


class FormulaError(ValueError):
    """Ошибка парсинга или вычисления формулы."""


# ---------- Parser ----------

# `[14]` → `_L(14)`. Допустимые символы внутри скобок: цифры (можно с
# пробелами по краям). Минус и прочее не разрешаем — line_no положительный.
_LINE_REF_RE = re.compile(r"\[\s*(\d+)\s*\]")

_ALLOWED_BIN_OPS = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
}


def parse(formula: str) -> Node:
    """Распарсить формулу в AST. На любую кривизну — FormulaError."""
    if not formula or not formula.strip():
        raise FormulaError("Пустая формула")
    expr = _LINE_REF_RE.sub(r"_L(\1)", formula)
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise FormulaError(f"Синтаксическая ошибка: {e.msg}") from e
    return _to_node(tree.body)


def _to_node(n: ast.AST) -> Node:
    if isinstance(n, ast.Constant):
        if isinstance(n.value, (int, float)):
            return Number(float(n.value))
        raise FormulaError(f"Недопустимое значение: {n.value!r}")
    if isinstance(n, ast.UnaryOp):
        if isinstance(n.op, ast.USub):
            return UnaryMinus(_to_node(n.operand))
        if isinstance(n.op, ast.UAdd):
            return _to_node(n.operand)  # +X = X
        raise FormulaError("Допустим только унарный минус")
    if isinstance(n, ast.BinOp):
        op_name = _ALLOWED_BIN_OPS.get(type(n.op))
        if op_name is None:
            raise FormulaError(
                f"Недопустимая операция: {type(n.op).__name__} "
                "(разрешены только + - * /)"
            )
        return BinOp(op_name, _to_node(n.left), _to_node(n.right))
    if isinstance(n, ast.Call):
        # Только наш `_L(N)` для line-ref'ов
        if (
            isinstance(n.func, ast.Name)
            and n.func.id == "_L"
            and len(n.args) == 1
            and not n.keywords
            and isinstance(n.args[0], ast.Constant)
            and isinstance(n.args[0].value, int)
        ):
            line_no = n.args[0].value
            if line_no <= 0:
                raise FormulaError(f"line_no должен быть положительным, получено [{line_no}]")
            return LineRef(line_no)
        raise FormulaError("Вызовы функций запрещены — используй [N] для ссылки на строку")
    if isinstance(n, ast.Name):
        raise FormulaError(
            f"Неизвестное имя {n.id!r} — переменные не поддерживаются, "
            "используй [N] для ссылки на строку"
        )
    raise FormulaError(f"Недопустимая конструкция: {type(n).__name__}")


# ---------- Evaluator ----------

def evaluate(node: Node, line_values: dict[int, Optional[float]]) -> Optional[float]:
    """Вычислить AST с подстановкой значений line_no.

    line_values: {line_no -> rollup-amount}. Если строки нет в маппе или её
    значение None — считается отсутствующим: вся формула вернёт None
    (нечего считать). При делении на 0 — тоже None.
    """
    if isinstance(node, Number):
        return node.value
    if isinstance(node, LineRef):
        return line_values.get(node.line_no)
    if isinstance(node, UnaryMinus):
        v = evaluate(node.operand, line_values)
        return None if v is None else -v
    if isinstance(node, BinOp):
        a = evaluate(node.left, line_values)
        b = evaluate(node.right, line_values)
        if a is None or b is None:
            return None
        if node.op == "+":
            return a + b
        if node.op == "-":
            return a - b
        if node.op == "*":
            return a * b
        if node.op == "/":
            if b == 0:
                return None
            return a / b
    raise FormulaError(f"Неизвестный тип узла: {type(node).__name__}")


def line_refs(node: Node) -> set[int]:
    """Все line_no, на которые ссылается формула. Нужно для валидации
    («формула ссылается на удалённую строку»)."""
    if isinstance(node, LineRef):
        return {node.line_no}
    if isinstance(node, Number):
        return set()
    if isinstance(node, UnaryMinus):
        return line_refs(node.operand)
    if isinstance(node, BinOp):
        return line_refs(node.left) | line_refs(node.right)
    return set()


def parse_and_validate(formula: str, valid_line_nos: set[int]) -> Node:
    """Парсить + проверить что все [N] ссылки существуют в шаблоне."""
    node = parse(formula)
    refs = line_refs(node)
    missing = refs - valid_line_nos
    if missing:
        sample = sorted(missing)[:5]
        raise FormulaError(
            f"Формула ссылается на несуществующие строки: "
            f"{', '.join(f'[{n}]' for n in sample)}"
            + (" …" if len(missing) > 5 else "")
        )
    return node
