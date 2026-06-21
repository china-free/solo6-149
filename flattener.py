from __future__ import annotations

import csv
import io
import re
from typing import Any, Dict, List, Optional, Tuple, Union


def _sanitize_key(key: str) -> str:
    return re.sub(r'[\s\-]+', '_', str(key))


def _join_keys(keys: List[str], sep: str = '.') -> str:
    return sep.join(_sanitize_key(k) for k in keys if k != '')


def flatten_object(
    obj: Any,
    parent_key: str = '',
    sep: str = '.',
    max_depth: Optional[int] = None,
    current_depth: int = 0,
    keep_lists: bool = True,
) -> Dict[str, Any]:
    items: Dict[str, Any] = {}
    if isinstance(obj, dict):
        if max_depth is not None and current_depth >= max_depth:
            items[parent_key] = obj
        else:
            for k, v in obj.items():
                new_key = _join_keys([parent_key, k], sep) if parent_key else _sanitize_key(k)
                items.update(flatten_object(v, new_key, sep, max_depth, current_depth + 1, keep_lists))
    elif isinstance(obj, list) and keep_lists:
        items[parent_key] = obj
    elif isinstance(obj, list) and not keep_lists:
        items[parent_key] = str(obj)
    else:
        items[parent_key] = obj
    return items


def _find_array_paths(data: Any, prefix: str = '') -> List[str]:
    paths: List[str] = []
    if isinstance(data, dict):
        for k, v in data.items():
            child_prefix = f"{prefix}.{k}" if prefix else k
            if isinstance(v, list):
                paths.append(child_prefix)
            paths.extend(_find_array_paths(v, child_prefix))
    elif isinstance(data, list):
        for item in data:
            paths.extend(_find_array_paths(item, prefix))
    return paths


def _dedup_paths(paths: List[str]) -> List[str]:
    seen = set()
    result = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


def _get_value_at_path(data: Any, path: str) -> Any:
    parts = re.split(r'\.|\[|\]', path)
    parts = [p for p in parts if p != '']
    current = data
    for part in parts:
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, list):
            if not part.isdigit():
                return None
            idx = int(part)
            if 0 <= idx < len(current):
                current = current[idx]
            else:
                return None
        else:
            return None
    return current


def _expand_array_in_rows(
    rows: List[Dict[str, Any]],
    data: Any,
    array_path: str,
    sep: str = '.',
) -> List[Dict[str, Any]]:
    if not rows:
        return rows

    has_inline = any(
        array_path in row and isinstance(row[array_path], list)
        for row in rows
    )

    global_array = _get_value_at_path(data, array_path)
    global_is_list = isinstance(global_array, list) and global_array

    if not has_inline and not global_is_list:
        return rows

    new_rows: List[Dict[str, Any]] = []
    for row in rows:
        local_array = None
        if array_path in row and isinstance(row[array_path], list):
            local_array = row[array_path]
        elif global_is_list:
            local_array = global_array

        if local_array is None:
            new_rows.append(row)
            continue

        if not local_array:
            if array_path in row:
                row[array_path] = str(local_array)
            new_rows.append(row)
            continue

        all_primitive = all(not isinstance(x, (dict, list)) for x in local_array)
        if all_primitive:
            row[array_path] = str(local_array)
            new_rows.append(row)
            continue

        for idx, item in enumerate(local_array):
            new_row = dict(row)
            if array_path in new_row:
                del new_row[array_path]
            new_row[f"{array_path}{sep}_index"] = idx
            if isinstance(item, dict):
                flat_item = flatten_object(item, array_path, sep, keep_lists=True)
                new_row.update(flat_item)
            elif isinstance(item, list):
                new_row[array_path] = str(item)
            else:
                new_row[array_path] = item
            new_rows.append(new_row)

    return new_rows


def _sort_paths_by_depth(paths: List[str]) -> List[str]:
    return sorted(paths, key=lambda p: p.count('.'))


def flatten_to_rows(
    data: Any,
    expand_arrays: bool = True,
    sep: str = '.',
    primary_array: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not isinstance(data, (dict, list)):
        return [{'value': data}]

    if isinstance(data, list):
        rows: List[Dict[str, Any]] = []
        for idx, item in enumerate(data):
            if isinstance(item, dict):
                flat = flatten_object(item, sep=sep, keep_lists=True)
                flat['_index'] = idx
                rows.append(flat)
            else:
                rows.append({'_index': idx, 'value': item})

        if expand_arrays:
            array_paths = _dedup_paths(_find_array_paths(data))
            array_paths = _sort_paths_by_depth(array_paths)
            for ap in array_paths:
                rows = _expand_array_in_rows(rows, data, ap, sep)

        for row in rows:
            for k, v in list(row.items()):
                if isinstance(v, (list, dict)):
                    row[k] = str(v)
        return rows

    base_flat = flatten_object(data, sep=sep, keep_lists=True)

    if not expand_arrays:
        for k, v in list(base_flat.items()):
            if isinstance(v, (list, dict)):
                base_flat[k] = str(v)
        return [base_flat]

    array_paths = _dedup_paths(_find_array_paths(data))
    array_paths = _sort_paths_by_depth(array_paths)

    if primary_array and primary_array in array_paths:
        array_paths.remove(primary_array)
        array_paths.insert(0, primary_array)

    rows = [dict(base_flat)]

    for array_path in array_paths:
        rows = _expand_array_in_rows(rows, data, array_path, sep)

    for row in rows:
        for k, v in list(row.items()):
            if isinstance(v, (list, dict)):
                row[k] = str(v)

    return rows


def pivot_object_array(
    rows: List[Dict[str, Any]],
    key_col: str,
    value_col: str,
) -> List[Dict[str, Any]]:
    if not rows:
        return rows

    group_cols = [c for c in rows[0].keys() if c not in (key_col, value_col)]

    grouped: Dict[Tuple, Dict[str, Any]] = {}
    for row in rows:
        key = tuple(row.get(c) for c in group_cols)
        if key not in grouped:
            grouped[key] = {c: row.get(c) for c in group_cols}
        pivot_key = str(row.get(key_col, ''))
        grouped[key][_sanitize_key(pivot_key)] = row.get(value_col)

    return list(grouped.values())


def rows_to_csv(rows: List[Dict[str, Any]], delimiter: str = ',') -> str:
    if not rows:
        return ''

    all_keys: List[str] = []
    seen = set()
    for row in rows:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                all_keys.append(k)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=all_keys, delimiter=delimiter, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue()


def apply_mapping(
    rows: List[Dict[str, Any]],
    mapping: Dict[str, str],
) -> List[Dict[str, Any]]:
    if not mapping:
        return rows

    result: List[Dict[str, Any]] = []
    for row in rows:
        new_row: Dict[str, Any] = {}
        for new_col, source_expr in mapping.items():
            if source_expr in row:
                new_row[new_col] = row[source_expr]
            else:
                try:
                    from path_parser import evaluate_expression
                    new_row[new_col] = evaluate_expression(source_expr, row)
                except Exception:
                    new_row[new_col] = source_expr
        result.append(new_row)
    return result
