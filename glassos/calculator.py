import ast
import operator
import re
from decimal import Context, Decimal, localcontext
from fractions import Fraction


OPERATIONS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
TOOL = {
    "type": "function",
    "function": {
        "name": "calculate",
        "description": "Calculate an arithmetic expression. Use this for numerical questions and word problems.",
        "parameters": {
            "type": "object",
            "properties": {"expression": {"type": "string", "description": "Numbers, parentheses and + - * / only"}},
            "required": ["expression"],
        },
    },
}


def calculate(expression: str) -> str:
    expression = expression.translate(str.maketrans("×÷−", "*/-")).strip()
    if len(expression) > 120 or not re.fullmatch(r"[0-9.+*/() \-]+", expression):
        raise ValueError("Invalid expression")

    def evaluate(node: ast.AST) -> Fraction:
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return Fraction(ast.get_source_segment(expression, node))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = evaluate(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp) and type(node.op) in OPERATIONS:
            return OPERATIONS[type(node.op)](evaluate(node.left), evaluate(node.right))
        raise ValueError("Unsupported arithmetic")

    return format_number(evaluate(ast.parse(expression, mode="eval").body))


def format_number(value: Fraction) -> str:
    """Show a result as a decimal: exact when it terminates within 10 places, otherwise rounded with ≈."""
    if value.denominator == 1:
        return str(value.numerator)
    with localcontext(Context(prec=300)):
        rounded = (Decimal(value.numerator) / Decimal(value.denominator)).quantize(Decimal("1e-10"))
    text = format(rounded, "f").rstrip("0").rstrip(".")
    if text in ("", "-0"):
        text = "0"
    return text if Fraction(rounded) == value else f"≈ {text}"
