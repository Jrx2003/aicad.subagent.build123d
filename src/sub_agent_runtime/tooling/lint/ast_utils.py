from __future__ import annotations

import ast
import io
import tokenize


def _ast_expr_is_zero_like(expr: ast.AST) -> bool:
    if isinstance(expr, ast.Constant):
        value = expr.value
        return isinstance(value, (int, float)) and float(value) == 0.0
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, (ast.UAdd, ast.USub)):
        return _ast_expr_is_zero_like(expr.operand)
    return False


def _ast_expr_is_plane_like(expr: ast.AST, plane_names: set[str]) -> bool:
    if isinstance(expr, ast.Name):
        return str(expr.id or "").strip() in plane_names
    if isinstance(expr, ast.Attribute):
        return (
            isinstance(expr.value, ast.Name)
            and str(expr.value.id or "").strip() == "Plane"
            and str(expr.attr or "").strip() in {"XY", "XZ", "YZ"}
        )
    if isinstance(expr, ast.Call):
        func = expr.func
        if isinstance(func, ast.Name):
            return str(func.id or "").strip() == "Plane"
        if isinstance(func, ast.Attribute):
            method_name = str(func.attr or "").strip()
            if method_name in {"offset", "move", "shift_origin", "rotated"}:
                return _ast_expr_is_plane_like(func.value, plane_names)
    return False


def _ast_expr_is_face_plane_constructor(expr: ast.AST) -> bool:
    if not isinstance(expr, ast.Call):
        return False
    func = expr.func
    if not (isinstance(func, ast.Name) and str(func.id or "").strip() == "Plane"):
        return False
    if expr.keywords or len(expr.args) != 1:
        return False
    return isinstance(expr.args[0], (ast.Name, ast.Attribute, ast.Call, ast.Subscript))


def _build_parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parent_map: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_map[child] = parent
    return parent_map


def _subscript_index_value(node: ast.Subscript) -> int | None:
    slice_node = node.slice
    if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, int):
        return int(slice_node.value)
    if (
        isinstance(slice_node, ast.UnaryOp)
        and isinstance(slice_node.op, ast.USub)
        and isinstance(slice_node.operand, ast.Constant)
        and isinstance(slice_node.operand.value, int)
    ):
        return -int(slice_node.operand.value)
    return None


def _ast_dotted_name(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return str(node.id or "").strip() or None
    if isinstance(node, ast.Attribute):
        parent = _ast_dotted_name(node.value)
        attr = str(node.attr or "").strip()
        if parent and attr:
            return f"{parent}.{attr}"
        if attr:
            return attr
    return None


def _ast_is_mode_subtract(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "SUBTRACT" and _ast_name_matches(
        node.value, "Mode"
    )


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name) and func.id:
        return str(func.id)
    if isinstance(func, ast.Attribute) and func.attr:
        return str(func.attr)
    return None


def _call_uses_mode_subtract(node: ast.Call) -> bool:
    for keyword in node.keywords:
        if str(getattr(keyword, "arg", "") or "").strip() != "mode":
            continue
        if _ast_is_mode_subtract(keyword.value):
            return True
    return False


def _call_uses_mode_private(node: ast.Call) -> bool:
    for keyword in node.keywords:
        if str(getattr(keyword, "arg", "") or "").strip() != "mode":
            continue
        value = keyword.value
        if (
            isinstance(value, ast.Attribute)
            and value.attr == "PRIVATE"
            and isinstance(value.value, ast.Name)
            and value.value.id == "Mode"
        ):
            return True
    return False


def _call_materializes_additive_host(node: ast.Call) -> bool:
    if _call_uses_mode_subtract(node) or _call_uses_mode_private(node):
        return False
    call_name = _call_name(node)
    if call_name in {"Box", "Cylinder", "Cone", "Sphere", "Torus"}:
        return True
    if call_name in {"extrude", "revolve", "loft", "sweep"}:
        return True
    if call_name == "add":
        return True
    return False


def _call_subtractive_without_host_operation_name(node: ast.Call) -> str | None:
    call_name = _call_name(node)
    if not call_name:
        return None
    if call_name in {"Box", "Cylinder", "Cone", "Sphere", "Torus", "extrude"}:
        return call_name if _call_uses_mode_subtract(node) else None
    if call_name in {"Hole", "CounterBoreHole", "CounterSinkHole"}:
        return call_name
    return None


def _with_context_builder_name(node: ast.AST) -> str | None:
    context_expr = node
    if isinstance(context_expr, ast.Call):
        context_expr = context_expr.func
    for builder_name in ("BuildPart", "BuildSketch", "BuildLine"):
        if _ast_name_matches(context_expr, builder_name):
            return builder_name
    return None


def _with_context_is_locations(node: ast.AST) -> bool:
    context_expr = node
    if isinstance(context_expr, ast.Call):
        context_expr = context_expr.func
    return _ast_name_matches(context_expr, "Locations")


def _looks_like_plane_expr(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute):
        return isinstance(node.value, ast.Name) and node.value.id == "Plane"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr not in {"offset", "rotated"}:
            return False
        return _looks_like_plane_expr(node.func.value)
    return False


def _looks_like_scalar_coordinate_expr(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return True
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, (ast.UAdd, ast.USub))
        and _looks_like_scalar_coordinate_expr(node.operand)
    ):
        return True
    if isinstance(node, (ast.Name, ast.Attribute, ast.Subscript)):
        return True
    if isinstance(node, ast.BinOp) and isinstance(
        node.op,
        (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow),
    ):
        return _looks_like_scalar_coordinate_expr(node.left) and _looks_like_scalar_coordinate_expr(
            node.right
        )
    return False


def _looks_like_vector_tuple(node: ast.AST) -> bool:
    if not isinstance(node, (ast.Tuple, ast.List)):
        return False
    if len(node.elts) not in {2, 3}:
        return False
    return all(_looks_like_scalar_coordinate_expr(element) for element in node.elts)


def _looks_like_xyz_coordinate_tuple(node: ast.AST) -> bool:
    return isinstance(node, (ast.Tuple, ast.List)) and len(node.elts) == 3


def _ast_name_matches(node: ast.AST, expected: str) -> bool:
    if isinstance(node, ast.Name):
        return node.id == expected
    if isinstance(node, ast.Attribute):
        return node.attr == expected
    return False


def _ast_expr_text(node: ast.AST) -> str:
    try:
        return ast.unparse(node).strip()
    except Exception:
        return ""


def _collect_binding_names_from_target(target: ast.AST, names: set[str]) -> None:
    if isinstance(target, ast.Name) and target.id:
        names.add(str(target.id))
        return
    if isinstance(target, (ast.Tuple, ast.List)):
        for item in target.elts:
            _collect_binding_names_from_target(item, names)
        return
    if isinstance(target, ast.Starred):
        _collect_binding_names_from_target(target.value, names)


def _collect_module_local_binding_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()

    class _Collector(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            if node.name:
                names.add(str(node.name))
            for decorator in node.decorator_list:
                self.visit(decorator)
            if node.returns is not None:
                self.visit(node.returns)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
            self.visit_FunctionDef(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
            if node.name:
                names.add(str(node.name))
            for decorator in node.decorator_list:
                self.visit(decorator)
            for base in node.bases:
                self.visit(base)
            for keyword in node.keywords:
                self.visit(keyword.value)

        def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
            for target in node.targets:
                _collect_binding_names_from_target(target, names)
            self.visit(node.value)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
            _collect_binding_names_from_target(node.target, names)
            if node.value is not None:
                self.visit(node.value)
            self.visit(node.annotation)

        def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802
            _collect_binding_names_from_target(node.target, names)
            self.visit(node.target)
            self.visit(node.value)

        def visit_For(self, node: ast.For) -> None:  # noqa: N802
            _collect_binding_names_from_target(node.target, names)
            self.visit(node.iter)
            for statement in node.body:
                self.visit(statement)
            for statement in node.orelse:
                self.visit(statement)

        def visit_AsyncFor(self, node: ast.AsyncFor) -> None:  # noqa: N802
            self.visit_For(node)

        def visit_With(self, node: ast.With) -> None:  # noqa: N802
            for item in node.items:
                self.visit(item.context_expr)
                if item.optional_vars is not None:
                    _collect_binding_names_from_target(item.optional_vars, names)
            for statement in node.body:
                self.visit(statement)

        def visit_AsyncWith(self, node: ast.AsyncWith) -> None:  # noqa: N802
            self.visit_With(node)

        def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
            for alias in node.names:
                alias_name = str(alias.asname or alias.name or "").strip()
                if alias_name:
                    names.add(alias_name.split(".")[0])

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
            for alias in node.names:
                if alias.name == "*":
                    continue
                alias_name = str(alias.asname or alias.name or "").strip()
                if alias_name:
                    names.add(alias_name.split(".")[0])

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:  # noqa: N802
            if isinstance(node.name, str) and node.name.strip():
                names.add(node.name.strip())
            if node.type is not None:
                self.visit(node.type)
            for statement in node.body:
                self.visit(statement)

        def visit_NamedExpr(self, node: ast.NamedExpr) -> None:  # noqa: N802
            _collect_binding_names_from_target(node.target, names)
            self.visit(node.value)

    _Collector().visit(tree)
    return names


def _collect_module_plane_binding_names(tree: ast.Module) -> set[str]:
    plane_names: set[str] = set()
    for statement in tree.body:
        value: ast.AST | None = None
        targets: list[ast.AST] = []
        if isinstance(statement, ast.Assign):
            value = statement.value
            targets = list(statement.targets)
        elif isinstance(statement, ast.AnnAssign):
            value = statement.value
            targets = [statement.target]
        elif isinstance(statement, ast.AugAssign):
            value = statement.value
            targets = [statement.target]
        if value is None or not _ast_expr_is_plane_like(value, plane_names):
            continue
        for target in targets:
            _collect_binding_names_from_target(target, plane_names)
    return plane_names


def _strip_python_comments_and_strings(code: str) -> str:
    pieces: list[str] = []
    last_line = 1
    last_col = 0
    try:
        token_stream = tokenize.generate_tokens(io.StringIO(code).readline)
        for token in token_stream:
            token_type = token.type
            token_text = token.string
            start_line, start_col = token.start
            end_line, end_col = token.end
            while last_line < start_line:
                pieces.append("\n")
                last_line += 1
                last_col = 0
            if start_col > last_col:
                pieces.append(" " * (start_col - last_col))
            if token_type in {tokenize.COMMENT, tokenize.STRING}:
                pieces.append(" " * len(token_text))
            else:
                pieces.append(token_text)
            last_line = end_line
            last_col = end_col
    except Exception:
        return code
    return "".join(pieces)


__all__ = [name for name in globals() if not name.startswith("__")]
