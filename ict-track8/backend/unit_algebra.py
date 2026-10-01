"""Dimension checking and explicit unit-scale conversion; no currency guessing."""
import ast


UNITS = {
    'ratio': ({}, 1), 'dimensionless': ({}, 1), '1': ({}, 1), '%': ({}, 0.01),
    'CNY': ({'CNY': 1}, 1), '元': ({'CNY': 1}, 1), '万元': ({'CNY': 1}, 10000),
    'USD': ({'USD': 1}, 1), 'count': ({'count': 1}, 1), '单': ({'count': 1}, 1),
    '件': ({'item': 1}, 1), '小时': ({'hour': 1}, 1), 'hour': ({'hour': 1}, 1),
    '分钟': ({'hour': 1}, 1/60), '天': ({'hour': 1}, 24),
}


def combine(left, right, sign=1):
    keys = left.keys() | right.keys()
    return {key: left.get(key, 0) + sign * right.get(key, 0) for key in keys if left.get(key, 0) + sign * right.get(key, 0)}


def infer(expression, units):
    def visit(node):
        if isinstance(node, ast.Name):
            return UNITS[units[node.id]][0]
        if isinstance(node, ast.Constant):
            return {}
        if isinstance(node, ast.UnaryOp):
            return visit(node.operand)
        if not isinstance(node, ast.BinOp):
            raise ValueError('单位分析包含不支持的节点')
        left, right = visit(node.left), visit(node.right)
        if isinstance(node.op, (ast.Add, ast.Sub, ast.Mod)):
            if left != right:
                raise ValueError('公式加减或取模的单位不一致')
            return left
        if isinstance(node.op, ast.Mult):
            return combine(left, right)
        if isinstance(node.op, ast.Div):
            return combine(left, right, -1)
        if isinstance(node.op, ast.Pow):
            if right or not isinstance(node.right, ast.Constant) or type(node.right.value) is not int:
                raise ValueError('带单位的乘方必须使用整数常量指数')
            return {key: exponent * node.right.value for key, exponent in left.items() if exponent * node.right.value}
        raise ValueError('公式单位运算未支持')
    dimension = visit(ast.parse(expression, mode='eval').body)
    return dimension, '*'.join(key + (f'^{power}' if power != 1 else '') for key, power in sorted(dimension.items())) or 'ratio'
