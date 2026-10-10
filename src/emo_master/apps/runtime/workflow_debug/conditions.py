"""Small, bounded expression interpreter. Never executes Python bytecode."""
import ast
import operator
import math

from emo_master.apps.runtime.operator_debug.contracts import fail


COMPARISONS = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
               ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}
NAMES = {"inputs", "params", "variables", "hits"}


def literalKey(node):
    if isinstance(node, ast.Constant) and type(node.value) in {str, int}:
        return node.value
    if (isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd))
            and isinstance(node.operand, ast.Constant) and type(node.operand.value) is int):
        return -node.operand.value if isinstance(node.op, ast.USub) else node.operand.value
    fail("E_DEBUG_CONDITION", "subscripts must be literal strings or integers")


def compileCondition(source):
    if not isinstance(source, str) or len(source) > 1024:
        fail("E_DEBUG_CONDITION", "condition must contain at most 1024 characters")
    if not source.strip():
        return None
    try:
        tree = ast.parse(source, mode="eval")
    except (SyntaxError, ValueError, RecursionError) as error:
        fail("E_DEBUG_CONDITION", str(error)[:256])
    nodes = list(ast.walk(tree))
    allowed = (ast.Expression, ast.Constant, ast.Name, ast.Load, ast.Subscript, ast.BoolOp,
               ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd, ast.Compare, *COMPARISONS)
    if len(nodes) > 128 or any(not isinstance(node, allowed) for node in nodes):
        fail("E_DEBUG_CONDITION", "only names, scalar comparisons, boolean operators and literal subscripts are allowed")
    for node in nodes:
        if isinstance(node, ast.Name) and node.id not in NAMES:
            fail("E_DEBUG_CONDITION", "unknown name: " + node.id)
        if isinstance(node, ast.Constant) and type(node.value) not in {str, int, float, bool, type(None)}:
            fail("E_DEBUG_CONDITION", "unsupported literal")
        if isinstance(node, ast.Constant) and type(node.value) is float and not math.isfinite(node.value):
            fail("E_DEBUG_CONDITION", "non-finite condition literal")
        if isinstance(node, ast.Subscript):
            literalKey(node.slice)
        if (isinstance(node, ast.UnaryOp) and not isinstance(node.op, ast.Not)
                and (not isinstance(node.operand, ast.Constant) or type(node.operand.value) not in {int, float})):
            fail("E_DEBUG_CONDITION", "a numeric sign is allowed only on a literal")
    return tree.body


def evaluate(tree, environment):
    def scalar(value):
        if type(value) not in {str, int, float, bool, type(None)}:
            fail("E_DEBUG_CONDITION", "comparison requires scalar values")
        return value

    def boolean(value):
        if type(value) is not bool:
            fail("E_DEBUG_CONDITION", "condition must be boolean, without coercion")
        return value

    def visit(node, depth=0):
        if depth > 32:
            fail("E_DEBUG_CONDITION", "condition nesting exceeds 32")
        def child(value):
            return visit(value, depth + 1)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return environment[node.id]
        if isinstance(node, ast.Subscript):
            value, key = child(node.value), literalKey(node.slice)
            if type(value) not in {dict, list, tuple}:
                fail("E_DEBUG_CONDITION", "subscript requires a plain container")
            return value[key]
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return not boolean(child(node.operand))
            return -node.operand.value if isinstance(node.op, ast.USub) else node.operand.value
        if isinstance(node, ast.BoolOp):
            for item in node.values:
                result = boolean(child(item))
                if result == isinstance(node.op, ast.Or):
                    return result
            return result
        if isinstance(node, ast.Compare):
            left = scalar(child(node.left))
            for operation, other in zip(node.ops, node.comparators):
                right = scalar(child(other))
                if not COMPARISONS[type(operation)](left, right):
                    return False
                left = right
            return True
        fail("E_DEBUG_CONDITION", "invalid expression")
    if tree is None:
        return True
    try:
        return boolean(visit(tree))
    except (KeyError, IndexError, TypeError, ValueError) as error:
        fail("E_DEBUG_CONDITION", str(error)[:256])
