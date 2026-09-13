"""Deterministic matching rules. No model calls or executable expressions."""
import ast
from decimal import Decimal, InvalidOperation
import re


NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?")


def contains_required(fragment: str, output: str) -> bool:
    if NUMBER.fullmatch(fragment):
        # Compare complete numeric tokens: 4 is not present in 14, -4 or 0.4.
        try:
            expected = Decimal(fragment)
        except InvalidOperation:
            return False
        for match in NUMBER.finditer(output):
            try:
                if Decimal(match.group()) == expected:
                    return True
            except InvalidOperation:
                continue
        return False
    return fragment.casefold() in output.casefold()


def arithmetic_syntax(value: str) -> str:
    """Ignore formatting and redundant parentheses, preserving operations and operands."""
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        raise ValueError("arithmetic_syntax requires a bounded arithmetic string")
    try:
        tree = ast.parse(value.strip(), mode="eval")
    except (SyntaxError, ValueError) as error:
        raise ValueError("invalid arithmetic syntax") from error
    nodes = list(ast.walk(tree))
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.UAdd, ast.USub)
    if len(nodes) > 40 or any(type(node) not in allowed for node in nodes):
        raise ValueError("only bounded arithmetic syntax is supported")
    if any(isinstance(node, ast.Constant) and (
        type(node.value) not in (int, float) or not -1e12 <= node.value <= 1e12
    ) for node in nodes):
        raise ValueError("invalid arithmetic operand")
    return ast.dump(tree, include_attributes=False)
