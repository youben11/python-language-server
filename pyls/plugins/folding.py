# pylint: disable=len-as-condition
# Copyright 2019 Palantir Technologies, Inc.

import re

import parso
import parso.python.tree as tree_nodes

from pyls import hookimpl

SKIP_NODES = (tree_nodes.Module, tree_nodes.IfStmt, tree_nodes.TryStmt)
IDENTATION_REGEX = re.compile(r'(\s+).+')


@hookimpl
def pyls_folding_range(document):
    program = document.source + '\n'
    lines = program.splitlines()
    tree = parso.parse(program)
    ranges = __compute_folding_ranges(tree, lines)

    results = []
    for (start_line, end_line) in ranges:
        start_line -= 1
        end_line -= 1
        # If start/end character is not defined, then it defaults to the
        # corresponding line last character
        results.append({
            'startLine': start_line,
            'endLine': end_line,
        })
    return results


def __merge_folding_ranges(left, right):
    for start in list(left.keys()):
        right_start = right.pop(start, None)
        if right_start is not None:
            left[start] = max(right_start, start)
    left.update(right)
    return left


def __empty_identation_stack(identation_stack, level_limits,
                             current_line, folding_ranges):
    while identation_stack != []:
        upper_level = identation_stack.pop(0)
        level_start = level_limits.pop(upper_level)
        folding_ranges.append((level_start, current_line))
    return folding_ranges


def __match_identation_stack(identation_stack, level, level_limits,
                             folding_ranges, current_line):
    upper_level = identation_stack.pop(0)
    while upper_level >= level:
        level_start = level_limits.pop(upper_level)
        folding_ranges.append((level_start, current_line))
        upper_level = identation_stack.pop(0)
    identation_stack.insert(0, upper_level)
    return identation_stack, folding_ranges


def __compute_folding_ranges_identation(text):
    lines = text.splitlines()
    folding_ranges = []
    identation_stack = []
    level_limits = {}
    current_level = 0
    current_line = 0
    while lines[current_line] == '':
        current_line += 1
    for i, line in enumerate(lines):
        if i < current_line:
            continue
        i += 1
        identation_match = IDENTATION_REGEX.match(line)
        if identation_match is not None:
            whitespace = identation_match.group(1)
            level = len(whitespace)
            if level > current_level:
                level_limits[current_level] = current_line
                identation_stack.insert(0, current_level)
                current_level = level
            elif level < current_level:
                identation_stack, folding_ranges = __match_identation_stack(
                    identation_stack, level, level_limits, folding_ranges,
                    current_line)
                current_level = level
        else:
            folding_ranges = __empty_identation_stack(
                identation_stack, level_limits, current_line, folding_ranges)
            current_level = 0
        if line.strip() != '':
            current_line = i
    folding_ranges = __empty_identation_stack(
        identation_stack, level_limits, current_line, folding_ranges)
    return dict(folding_ranges)


def __check_if_node_is_valid(node):
    valid = True
    if isinstance(node, tree_nodes.PythonNode):
        kind = node.type
        valid = kind not in {'decorated', 'parameters'}
        if kind == 'suite':
            if isinstance(node.parent, tree_nodes.Function):
                valid = False
    return valid


def __compute_start_end_lines(node, stack):
    start_line, _ = node.start_pos
    end_line, _ = node.end_pos

    last_leaf = node.get_last_leaf()
    last_newline = isinstance(last_leaf, tree_nodes.Newline)
    last_operator = isinstance(last_leaf, tree_nodes.Operator)
    node_is_operator = isinstance(node, tree_nodes.Operator)
    last_operator = last_operator or not node_is_operator

    end_line -= 1

    modified = False
    if isinstance(node.parent, tree_nodes.PythonNode):
        kind = node.type
        if kind in {'suite', 'atom', 'atom_expr', 'arglist'}:
            if len(stack) > 0:
                next_node = stack[0]
                next_line, _ = next_node.start_pos
                if next_line > end_line:
                    end_line += 1
                    modified = True
    if not last_newline and not modified and not last_operator:
        end_line += 1
    return start_line, end_line


def __compute_folding_ranges(tree, lines):
    folding_ranges = {}
    stack = [tree]

    while len(stack) > 0:
        node = stack.pop(0)
        if isinstance(node, tree_nodes.Newline):
            # Skip newline nodes
            continue
        elif isinstance(node, tree_nodes.PythonErrorNode):
            # Fallback to identation-based (best-effort) folding
            start_line, _ = node.start_pos
            start_line -= 1
            padding = [''] * start_line
            text = '\n'.join(padding + lines[start_line:]) + '\n'
            identation_ranges = __compute_folding_ranges_identation(text)
            folding_ranges = __merge_folding_ranges(
                folding_ranges, identation_ranges)
            break
        elif not isinstance(node, SKIP_NODES):
            valid = __check_if_node_is_valid(node)
            if valid:
                start_line, end_line = __compute_start_end_lines(node, stack)
                if end_line > start_line:
                    current_end = folding_ranges.get(start_line, -1)
                    folding_ranges[start_line] = max(current_end, end_line)
        if hasattr(node, 'children'):
            stack = node.children + stack

    folding_ranges = sorted(folding_ranges.items())
    return folding_ranges
