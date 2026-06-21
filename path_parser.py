from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from jsonpath_ng.ext import parse as jsonpath_parse


def parse_jsonpath(data: Any, expr: str) -> List[Any]:
    try:
        jsonpath_expr = jsonpath_parse(expr)
        return [match.value for match in jsonpath_expr.find(data)]
    except Exception:
        return []


def parse_mapping(input_text: str) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    lines = input_text.strip().split('\n')
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' in line:
            parts = line.split('=', 1)
            new_col = parts[0].strip()
            source = parts[1].strip().strip('"').strip("'")
            if new_col and source:
                mapping[new_col] = source
        elif ':' in line:
            parts = line.split(':', 1)
            new_col = parts[0].strip()
            source = parts[1].strip().strip('"').strip("'")
            if new_col and source:
                mapping[new_col] = source
    return mapping


def parse_array_selectors(input_text: str) -> List[str]:
    selectors: List[str] = []
    lines = input_text.strip().split('\n')
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('$') or '.' in line or line.startswith('['):
            selectors.append(line)
        elif re.match(r'^[\w\.\[\]]+$', line):
            selectors.append(line)
    return selectors


def parse_pivot_config(input_text: str) -> Optional[Tuple[str, str]]:
    match = re.match(r'pivot\s*\(\s*([^,]+)\s*,\s*([^)]+)\s*\)', input_text, re.IGNORECASE)
    if match:
        key_col = match.group(1).strip().strip('"').strip("'")
        value_col = match.group(2).strip().strip('"').strip("'")
        return (key_col, value_col)
    return None


def evaluate_expression(expr: str, row: Dict[str, Any]) -> Any:
    if expr in row:
        return row[expr]

    try:
        result = parse_jsonpath(row, expr)
        if result:
            if len(result) == 1:
                return result[0]
            return result
    except Exception:
        pass

    def replace_field(match):
        field = match.group(1).strip('{}')
        if field in row:
            val = row[field]
            if isinstance(val, str):
                return val
            return str(val)
        return match.group(0)

    if '{' in expr and '}' in expr:
        return re.sub(r'\{([^}]+)\}', replace_field, expr)

    try:
        safe_dict = {k: v for k, v in row.items() if isinstance(k, str) and k.isidentifier()}
        safe_dict['__builtins__'] = {}
        return eval(expr, {"__builtins__": {}}, safe_dict)
    except Exception:
        return expr


def parse_input(input_text: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        'mapping': {},
        'array_selectors': [],
        'jsonpath': None,
        'pivot': None,
        'primary_array': None,
    }

    lines = input_text.strip().split('\n')
    remaining_lines: List[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue

        pivot_cfg = parse_pivot_config(stripped)
        if pivot_cfg:
            result['pivot'] = pivot_cfg
            continue

        if stripped.lower().startswith('from ') or stripped.lower().startswith('array '):
            parts = stripped.split(None, 1)
            if len(parts) == 2:
                result['primary_array'] = parts[1].strip()
                continue

        if stripped.startswith('$'):
            result['jsonpath'] = stripped
            continue

        remaining_lines.append(line)

    combined = '\n'.join(remaining_lines)
    result['mapping'] = parse_mapping(combined)
    result['array_selectors'] = parse_array_selectors(combined)

    return result
