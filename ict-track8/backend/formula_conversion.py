"""Small explicit LaTeX arithmetic grammar. Conversion never approves execution."""
from __future__ import annotations
import re
from .document_analysis import SafeFormulaEvaluator


def convert_latex_arithmetic(source):
    if not isinstance(source,str) or len(source)>1000:
        raise ValueError('latex_conversion_budget')
    token = re.compile(r'\s*(\\[A-Za-z]+|[A-Za-z][A-Za-z0-9]*|\d+(?:\.\d+)?|[{}()+\-*/^_])')
    parts, pos = [], 0
    while pos < len(source):
        if not source[pos:].strip():break
        match = token.match(source,pos)
        if match is None:raise ValueError('unsupported_latex_token')
        parts.append(match[1]);pos=match.end()
    index = 0
    symbols = {}
    def take():
        nonlocal index
        if index >= len(parts):raise ValueError('incomplete_latex_expression')
        value=parts[index];index+=1;return value
    def group(depth):
        if take()!='{':raise ValueError('latex_group_required')
        value=expression(depth+1)
        if take()!='}':raise ValueError('latex_group_unclosed')
        return value
    def atom(depth):
        nonlocal index
        if depth>12:raise ValueError('latex_depth_budget')
        value=take()
        if value=='\\frac':
            left,right=group(depth),group(depth)
            out=f'(({left})/({right}))'
        elif value in {'\\mathrm','\\mathit'}:
            if take()!='{':raise ValueError('named_symbol_group_required')
            name=[]
            while index < len(parts) and parts[index]!='}':name.append(take())
            if take()!='}' or not name or not all(re.fullmatch('[A-Za-z][A-Za-z0-9]*',v) for v in name):
                raise ValueError('unsupported_named_symbol')
            out=''.join(name)
            symbols[out]=' '.join(name)
        elif value=='{':
            index-=1;out='('+group(depth)+')'
        elif value=='(':
            out='('+expression(depth+1)+')'
            if take()!=')':raise ValueError('latex_parenthesis_unclosed')
        elif value in {'+','-'}:out=value+atom(depth+1)
        elif re.fullmatch(r'[A-Za-z][A-Za-z0-9]*|\d+(?:\.\d+)?',value):out=value
        else:raise ValueError('unsupported_latex_atom')
        if index<len(parts) and parts[index]=='_':
            index+=1
            if not re.fullmatch('[A-Za-z][A-Za-z0-9]*',out):raise ValueError('subscript_requires_symbol')
            sub=take()
            if sub=='{':
                sub=take()
                if take()!='}':raise ValueError('complex_subscript_unsupported')
            if not re.fullmatch('[A-Za-z0-9]+',sub):raise ValueError('complex_subscript_unsupported')
            out+='_'+sub
        if index<len(parts) and parts[index]=='^':
            index+=1
            exponent=group(depth) if parts[index:index+1]==['{'] else atom(depth+1)
            out=f'({out})**({exponent})'
        return out
    def product(depth):
        nonlocal index
        out=atom(depth)
        while index<len(parts) and parts[index] in {'*','/','\\times','\\cdot'}:
            op=take();out=f'({out}{"/" if op=="/" else "*"}{atom(depth)})'
        return out
    def expression(depth):
        out=product(depth)
        while index<len(parts) and parts[index] in {'+','-'}:
            op=take();out=f'({out}{op}{product(depth)})'
        return out
    result=expression(0)
    if index != len(parts):raise ValueError('implicit_product_or_unsupported_expression')
    parameters=SafeFormulaEvaluator().parameters(result)
    return {'expression':result,'parameters':list(parameters),'named_symbol_receipts':symbols,
            'calculator_input_eligible':False,'status':'converted_requires_visual_review'}
