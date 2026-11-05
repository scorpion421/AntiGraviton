"""
Fill-in-the-Middle (FIM) surgical code extraction and context formatting.
Inspired by Mistral AI's Codestral architecture.
Slashes context bloat on file edits by up to 90% by providing minimal prefix and suffix windows.
"""

import ast
import glob
import re
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Iterator
from ..config import estimate_tokens


def _node_start_lineno(node: ast.AST) -> int:
    """Returns the 1-indexed start line of an AST node including any leading @decorators."""
    base_line = int(getattr(node, "lineno", 1) or 1)
    decs = getattr(node, "decorator_list", None) or []
    if not decs:
        return base_line
    return min([base_line] + [int(getattr(d, "lineno", base_line) or base_line) for d in decs])


def _format_decorator_prefix(node: ast.AST) -> str:
    """Returns compact @decorator tags for key Python decorators (@staticmethod, @classmethod, @property, @dataclass)."""
    decs = getattr(node, "decorator_list", None) or []
    tags: List[str] = []
    for d in decs[:2]:
        raw = _format_ast_expr(d)
        if not raw or raw == "...":
            continue
        short = raw.split("(")[0].strip()
        if short:
            tags.append(f"@{short}")
    return (" ".join(tags) + " ") if tags else ""


def _iter_module_top_nodes(stmts: List[ast.stmt]) -> Iterator[ast.stmt]:
    """
    Yields top-level module statements, descending into top-level conditional/try/with
    blocks (e.g. 'if sys.platform == \"win32\":' or 'try: ... except ImportError:') so
    platform-specific functions and classes are never missed by AST contract mapping.
    """
    for node in stmts:
        yield node
        if isinstance(node, ast.If):
            yield from _iter_module_top_nodes(node.body)
            yield from _iter_module_top_nodes(node.orelse)
        elif isinstance(node, ast.Try):
            yield from _iter_module_top_nodes(node.body)
            for handler in node.handlers:
                yield from _iter_module_top_nodes(handler.body)
            yield from _iter_module_top_nodes(node.orelse)
            yield from _iter_module_top_nodes(node.finalbody)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            yield from _iter_module_top_nodes(node.body)


def slice_fim(
    file_path: Path,
    target_line: int,
    prefix_lines: int = 25,
    suffix_lines: int = 25
) -> Dict[str, Any]:
    """
    Extracts a surgical Fill-in-the-Middle window around a target line.
    Returns prefix lines, suffix lines, full file token count, and sliced token count.
    """
    fpath = Path(file_path)
    if not fpath.exists() or not fpath.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")

    content = fpath.read_text(encoding="utf-8", errors="replace")
    all_lines = content.splitlines(keepends=True)
    total_lines = len(all_lines)

    # 1-indexed target line adjusted to 0-index
    prefix_lines = max(0, prefix_lines)
    suffix_lines = max(0, suffix_lines)
    t_idx = max(0, min(total_lines - 1 if total_lines else 0, target_line - 1))

    prefix_start = max(0, t_idx - prefix_lines)
    suffix_end = min(total_lines, t_idx + suffix_lines + 1)

    prefix_chunk = "".join(all_lines[prefix_start:t_idx])
    target_line_content = all_lines[t_idx] if total_lines > 0 else ""
    suffix_chunk = "".join(all_lines[t_idx + 1:suffix_end])

    full_tokens = estimate_tokens(content)
    sliced_tokens = estimate_tokens(prefix_chunk + target_line_content + suffix_chunk)
    tokens_saved = max(0, full_tokens - sliced_tokens)

    return {
        "file": str(fpath),
        "target_line": (t_idx + 1) if total_lines > 0 else 0,
        "prefix_start_line": (prefix_start + 1) if (total_lines > 0 and t_idx > 0 and prefix_chunk) else 0,
        "prefix_end_line": max(prefix_start + 1, t_idx) if (total_lines > 0 and t_idx > 0 and prefix_chunk) else 0,
        "suffix_start_line": min(suffix_end, t_idx + 2) if (total_lines > 0 and (t_idx + 1) < suffix_end and suffix_chunk) else 0,
        "suffix_end_line": suffix_end if (total_lines > 0 and (t_idx + 1) < suffix_end and suffix_chunk) else 0,
        "total_file_lines": total_lines,
        "total_lines": total_lines,
        "prefix": prefix_chunk,
        "prefix_code": prefix_chunk,
        "middle": target_line_content,
        "target_line_content": target_line_content,
        "suffix": suffix_chunk,
        "suffix_code": suffix_chunk,
        "full_file_tokens": full_tokens,
        "sliced_tokens": sliced_tokens,
        "tokens_saved": tokens_saved,
    }


def slice_fim_symbol(
    file_path: Path,
    symbol_name: str,
    prefix_lines: int = 20,
    suffix_lines: int = 20
) -> Dict[str, Any]:
    """
    Finds the definition line of a symbol (function, class, Class.method, or variable) and slices the FIM window.
    Uses Python's native ast module as an exact fast-path for .py files before falling back to regex.
    """
    fpath = Path(file_path)
    if not fpath.exists() or not fpath.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")

    content = fpath.read_text(encoding="utf-8", errors="replace")
    all_lines = content.splitlines()
    match_line = None
    symbol_start_line = None
    symbol_end_line = None

    if fpath.suffix.lower() == ".py":
        try:
            tree = ast.parse(content)
            target_cls, target_member = (symbol_name.split(".", 1) if "." in symbol_name else (None, symbol_name))
            for node in _iter_module_top_nodes(tree.body):
                if isinstance(node, ast.ClassDef):
                    if target_cls is None and node.name == target_member:
                        symbol_start_line = _node_start_lineno(node)
                        symbol_end_line = getattr(node, "end_lineno", node.lineno)
                        match_line = node.lineno
                        break
                    if target_cls is None or node.name == target_cls:
                        for item in node.body:
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == target_member:
                                symbol_start_line = _node_start_lineno(item)
                                symbol_end_line = getattr(item, "end_lineno", item.lineno)
                                match_line = item.lineno
                                break
                        if match_line is not None:
                            break
                elif target_cls is None and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target_member:
                    symbol_start_line = _node_start_lineno(node)
                    symbol_end_line = getattr(node, "end_lineno", node.lineno)
                    match_line = node.lineno
                    break
                elif target_cls is None and isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for t in targets:
                        if isinstance(t, ast.Name) and t.id == target_member:
                            symbol_start_line = node.lineno
                            symbol_end_line = getattr(node, "end_lineno", node.lineno)
                            match_line = node.lineno
                            break
                    if match_line is not None:
                        break
        except Exception:
            pass

    if match_line is None and "." in symbol_name:
        target_cls, target_member = symbol_name.split(".", 1)
        cls_pat = re.compile(rf"\b(?:class|struct|interface|enum|impl|object|extension)\s+{re.escape(target_cls)}\b")
        next_cls_pat = re.compile(r"^\s*(?:export\s+|public\s+|private\s+|internal\s+|abstract\s+|final\s+)*(?:class|struct|interface|enum|impl|object)\s+\w+")
        mem_pat = re.compile(rf"\b{re.escape(target_member)}\s*(?:\(|=|:|<)")
        in_target_cls = False
        for idx, line in enumerate(all_lines, 1):
            if not in_target_cls:
                if cls_pat.search(line):
                    in_target_cls = True
            else:
                if next_cls_pat.search(line) and not cls_pat.search(line):
                    in_target_cls = False
                    continue
                if mem_pat.search(line) or re.search(rf"\b{re.escape(target_member)}\b", line):
                    match_line = idx
                    break

    if match_line is None:
        # Search for def, async def, class, func, fn, fun, struct, enum, const, let, var symbol patterns
        pat = re.compile(rf"\b(?:(?:async\s+)?def|class|function|func|fn|fun|struct|enum|var|let|const|val|interface|type)\s+{re.escape(symbol_name)}\b")
        for idx, line in enumerate(all_lines, 1):
            if pat.search(line):
                match_line = idx
                break

    if match_line is None:
        # Fallback to direct symbol substring search
        for idx, line in enumerate(all_lines, 1):
            if symbol_name in line:
                match_line = idx
                break

    if match_line is None:
        raise ValueError(f"Symbol '{symbol_name}' not found in {file_path}")

    effective_prefix = prefix_lines
    if symbol_start_line is not None and symbol_start_line < match_line:
        effective_prefix = max(prefix_lines, (match_line - symbol_start_line) + max(0, prefix_lines))

    res = slice_fim(fpath, match_line, prefix_lines=effective_prefix, suffix_lines=suffix_lines)
    res["symbol"] = symbol_name
    if symbol_start_line is not None:
        res["symbol_start_line"] = symbol_start_line
    if symbol_end_line is not None:
        res["symbol_end_line"] = symbol_end_line
    return res


def format_fim_block(fim_dict: Dict[str, Any]) -> str:
    """Formats FIM slices into standard Codestral-style <fim_prefix>, <fim_suffix> blocks."""
    lines = [
        f"=== CODESTRAL FIM SLICE: {fim_dict['file']} ===",
        f"Target Line: {fim_dict['target_line']} | Lines {fim_dict['prefix_start_line']}-{fim_dict['suffix_end_line']} of {fim_dict['total_file_lines']}",
        f"Token Compression: {fim_dict['sliced_tokens']} tokens (~{fim_dict['tokens_saved']} tokens saved, -{(fim_dict['tokens_saved'] / max(1, fim_dict['full_file_tokens']) * 100):.1f}%)",
        "",
        "<fim_prefix>",
        fim_dict["prefix"].rstrip(),
        "<fim_suffix>",
        fim_dict["suffix"].rstrip(),
        "<fim_middle>",
        f"# Target line to refactor/replace: {fim_dict['target_line_content'].strip()}",
        "===============================================",
    ]
    return "\n".join(lines)


def _format_ast_expr(node: Optional[ast.AST]) -> str:
    """Safely unparses an AST type annotation or default value expression."""
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return "..."


def _format_ast_signature(fn_node: ast.AST) -> str:
    """Formats a compact function/method argument & return signature from an AST FunctionDef/AsyncFunctionDef."""
    args_obj = getattr(fn_node, "args", None)
    if not args_obj:
        return "()"
    parts: List[str] = []
    pos_args = list(getattr(args_obj, "posonlyargs", [])) + list(getattr(args_obj, "args", []))
    defaults = list(getattr(args_obj, "defaults", []))
    num_no_def = len(pos_args) - len(defaults)

    for idx, arg in enumerate(pos_args):
        piece = arg.arg
        if arg.annotation:
            ann_s = _format_ast_expr(arg.annotation)
            if len(ann_s) <= 28:
                piece += f": {ann_s}"
        if idx >= num_no_def:
            def_node = defaults[idx - num_no_def]
            def_s = _format_ast_expr(def_node)
            if len(def_s) <= 20:
                piece += f"={def_s}"
            else:
                piece += "=..."
        parts.append(piece)

    if getattr(args_obj, "vararg", None):
        parts.append(f"*{args_obj.vararg.arg}")
    elif getattr(args_obj, "kwonlyargs", None):
        parts.append("*")

    kw_args = list(getattr(args_obj, "kwonlyargs", []))
    kw_defs = list(getattr(args_obj, "kw_defaults", []))
    for idx, kw in enumerate(kw_args):
        piece = kw.arg
        if idx < len(kw_defs) and kw_defs[idx] is not None:
            def_s = _format_ast_expr(kw_defs[idx])
            piece += f"={def_s}" if len(def_s) <= 20 else "=..."
        parts.append(piece)

    if getattr(args_obj, "kwarg", None):
        parts.append(f"**{args_obj.kwarg.arg}")

    sig = f"({', '.join(parts)})"
    ret_node = getattr(fn_node, "returns", None)
    if ret_node is not None:
        ret_s = _format_ast_expr(ret_node)
        if ret_s and len(ret_s) <= 36:
            sig += f" -> {ret_s}"
    return sig


def _first_doc_line(node: ast.AST) -> str:
    """Returns the first summary sentence of an AST docstring if present."""
    try:
        doc = ast.get_docstring(node, clean=True)
        if doc:
            first = doc.strip().splitlines()[0].strip()
            return first[:110]
    except Exception:
        pass
    return ""


def _extract_python_ast_contracts(
    content: str,
    symbol_filter: Optional[str] = None,
    include_private: bool = True,
) -> Dict[str, Any]:
    """Parses Python source into a compact interface & decorator-aware [Lstart-Lend] contract map."""
    tree = ast.parse(content)
    filt = (symbol_filter or "").strip().lower()

    imports: List[str] = []
    constants: List[str] = []
    classes: List[Dict[str, Any]] = []
    functions: List[str] = []
    seen_Imports = set()
    seen_consts = set()

    for node in _iter_module_top_nodes(tree.body):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in seen_Imports:
                    seen_Imports.add(alias.name)
                    imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            names = ", ".join(a.name for a in node.names[:6])
            if len(node.names) > 6:
                names += ", ..."
            imp_s = f"{mod}: {{{names}}}" if mod else names
            if imp_s not in seen_Imports:
                seen_Imports.add(imp_s)
                imports.append(imp_s)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    name = t.id
                    if name in seen_consts:
                        continue
                    if not include_private and name.startswith("_") and not name.startswith("__"):
                        continue
                    if filt and filt not in name.lower():
                        continue
                    if name.isupper() or name.startswith("__"):
                        seen_consts.add(name)
                        end_c = getattr(node, "end_lineno", node.lineno)
                        loc_s = f"[L{node.lineno}-L{end_c}]" if end_c > node.lineno else f"[L{node.lineno}]"
                        constants.append(f"{name} {loc_s}")
        elif isinstance(node, ast.ClassDef):
            cname = node.name
            start_l = _node_start_lineno(node)
            end_l = getattr(node, "end_lineno", node.lineno)
            dec_prefix = _format_decorator_prefix(node)
            bases = [_format_ast_expr(b) for b in node.bases]
            base_str = f"({', '.join(bases)})" if bases else ""
            methods: List[str] = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    mname = item.name
                    if not include_private and mname.startswith("_") and not mname.startswith("__"):
                        continue
                    if filt and (filt not in cname.lower() and filt not in mname.lower()):
                        continue
                    m_start = _node_start_lineno(item)
                    m_end = getattr(item, "end_lineno", item.lineno)
                    m_dec = _format_decorator_prefix(item)
                    prefix = "async def" if isinstance(item, ast.AsyncFunctionDef) else "def"
                    sig = _format_ast_signature(item)
                    doc_s = _first_doc_line(item)
                    doc_tag = f"  # {doc_s}" if doc_s else ""
                    methods.append(f"{m_dec}{prefix} {mname}{sig} [L{m_start}-L{m_end}]{doc_tag}")
            if filt and filt not in cname.lower() and not methods:
                continue
            classes.append({
                "header": f"{dec_prefix}class {cname}{base_str} [L{start_l}-L{end_l}]",
                "doc": _first_doc_line(node),
                "methods": methods,
            })
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fname = node.name
            if not include_private and fname.startswith("_") and not fname.startswith("__"):
                continue
            if filt and filt not in fname.lower():
                continue
            start_l = _node_start_lineno(node)
            end_l = getattr(node, "end_lineno", node.lineno)
            dec_prefix = _format_decorator_prefix(node)
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            sig = _format_ast_signature(node)
            doc_s = _first_doc_line(node)
            doc_tag = f"  # {doc_s}" if doc_s else ""
            functions.append(f"{dec_prefix}{prefix} {fname}{sig} [L{start_l}-L{end_l}]{doc_tag}")

    return {
        "imports": imports[:16],
        "constants": constants[:20],
        "classes": classes,
        "functions": functions,
    }


def _strip_strings_and_comments_for_braces(line: str) -> str:
    """Strips string literals and single-line comments so brace counting is never skewed by quoted '{' or '}'."""
    out: List[str] = []
    in_str: Optional[str] = None
    escape = False
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if escape:
            escape = False
            i += 1
            continue
        if ch == "\\":
            escape = True
            i += 1
            continue
        if in_str:
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ('"', "'", "`"):
            in_str = ch
            i += 1
            continue
        if ch == "/" and i + 1 < n and line[i + 1] == "/":
            break
        if ch == "#" and (i == 0 or line[i - 1].isspace()):
            break
        out.append(ch)
        i += 1
    return "".join(out)


def _find_block_end_line(lines: List[str], start_idx: int, is_sql: bool = False) -> int:
    """
    Deterministically computes the 1-indexed closing line [Lend] of a brace-delimited
    or SQL statement block starting at 0-indexed start_idx.
    """
    total = len(lines)
    if start_idx >= total:
        return start_idx + 1

    if is_sql:
        in_begin = False
        for i in range(start_idx, min(total, start_idx + 800)):
            up = lines[i].strip().upper()
            if re.search(r"\bBEGIN\b", up):
                in_begin = True
            if up == "GO" and i > start_idx:
                return i
            if in_begin and re.search(r"\bEND\s*;?\s*$", up):
                return i + 1
            if not in_begin and up.endswith(";"):
                return i + 1
        return start_idx + 1

    depth = 0
    saw_open = False
    for i in range(start_idx, min(total, start_idx + 2500)):
        cleaned = _strip_strings_and_comments_for_braces(lines[i])
        opens = cleaned.count("{")
        closes = cleaned.count("}")
        if opens > 0:
            saw_open = True
        depth += opens - closes
        if saw_open and depth <= 0:
            return i + 1
        if not saw_open and i > start_idx + 4 and cleaned.strip().endswith(";"):
            return i + 1
    return start_idx + 1


def _extract_generic_regex_contracts(
    content: str,
    symbol_filter: Optional[str] = None,
    suffix: str = "",
) -> Dict[str, Any]:
    """
    Structure & [Lstart-Lend] block-boundary extractor for PowerShell, TypeScript/JavaScript,
    Go, Rust, C#, Java, Kotlin, Dart, and SQL.
    """
    filt = (symbol_filter or "").strip().lower()
    lines = content.splitlines()
    functions: List[str] = []
    classes: List[Dict[str, Any]] = []
    is_sql = suffix.lower() == ".sql"

    cls_re = re.compile(
        r"^\s*(?:export\s+)?(?:public\s+|private\s+|protected\s+|internal\s+|abstract\s+|sealed\s+|final\s+|data\s+)*"
        r"(?:(?:class|interface|struct|enum|trait|record)\s+([A-Za-z_][A-Za-z0-9_]*)"
        r"|type\s+([A-Za-z_][A-Za-z0-9_]*)\s+(?:struct|interface)\b"
        r"|impl(?:\s*<[^>]*>)?\s+(?:[A-Za-z0-9_:<>]+\s+for\s+)?([A-Za-z_][A-Za-z0-9_]*))"
    )
    fn_re = re.compile(
        r"^\s*(?:export\s+)?(?:default\s+)?(?:pub(?:\([^)]*\))?\s+|public\s+|private\s+|protected\s+|internal\s+|static\s+|virtual\s+|override\s+| inline\s+)*"
        r"(?:suspend\s+|async\s+)*"
        r"(?:function|filter|workflow|def|func|fn|fun)\s+"
        r"(?:\([^)]+\)\s*)?"  # Optional Go receiver e.g. (s *Server)
        r"([A-Za-z_][A-Za-z0-9_:-]*)\s*(\([^)]*\))?"
    )
    ts_arrow_re = re.compile(
        r"^\s*(?:export\s+)?(?:const|let)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_][A-Za-z0-9_]*)\s*=>"
    )
    typed_method_re = re.compile(
        r"^\s*(?:(?:public|private|protected|internal|static|virtual|override|abstract|async|synchronized|final|readonly|get|set)\s+)+"
        r"(?:[A-Za-z_][A-Za-z0-9_<>,.\[\]?]*\s+)*([A-Za-z_][A-Za-z0-9_]*)\s*\(([^;{}]*)\)\s*(?::\s*[^{;]+|throws\s+[^{;]+)?\{"
    )
    sql_re = re.compile(
        r"^\s*CREATE\s+(?:OR\s+(?:ALTER|REPLACE)\s+)?(?:TEMP|TEMPORARY\s+)?"
        r"(TABLE|VIEW|PROCEDURE|PROC|FUNCTION|TRIGGER)\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z0-9_.\[\]\"]+)",
        re.IGNORECASE,
    )

    active_cls: Optional[Dict[str, Any]] = None
    active_cls_end = 0
    active_cls_name = ""

    for idx_0, raw_line in enumerate(lines):
        idx = idx_0 + 1
        if active_cls is not None and idx > active_cls_end:
            if not filt or filt in active_cls_name.lower() or active_cls["methods"]:
                classes.append(active_cls)
            active_cls = None
            active_cls_end = 0
            active_cls_name = ""

        if is_sql:
            sm = sql_re.match(raw_line)
            if sm:
                kind = sm.group(1).upper()
                sname = sm.group(2)
                end_l = _find_block_end_line(lines, idx_0, is_sql=True)
                loc = f"[L{idx}-L{end_l}]" if end_l > idx else f"[L{idx}]"
                if not filt or filt in sname.lower() or filt in kind.lower():
                    functions.append(f"CREATE {kind} {sname} {loc}")
            continue

        cm = cls_re.match(raw_line)
        if cm and active_cls is None:
            cname = cm.group(1) or cm.group(2) or cm.group(3) or ""
            end_l = _find_block_end_line(lines, idx_0)
            loc = f"[L{idx}-L{end_l}]" if end_l > idx else f"[L{idx}]"
            hdr_clean = raw_line.strip().rstrip("{").strip()
            active_cls = {
                "header": f"{hdr_clean} {loc}",
                "doc": "",
                "methods": [],
            }
            active_cls_end = end_l
            active_cls_name = cname
            continue

        fm = fn_re.match(raw_line) or ts_arrow_re.match(raw_line)
        if not fm and active_cls is not None:
            fm = typed_method_re.match(raw_line)
        if fm:
            fname = fm.group(1)
            if fname in ("if", "for", "while", "switch", "catch", "using", "lock", "synchronized"):
                continue
            end_l = _find_block_end_line(lines, idx_0)
            loc = f"[L{idx}-L{end_l}]" if end_l > idx else f"[L{idx}]"
            sig_clean = raw_line.strip().rstrip("{").strip()
            if active_cls is not None and idx <= active_cls_end:
                if not filt or filt in active_cls_name.lower() or filt in fname.lower():
                    active_cls["methods"].append(f"{sig_clean} {loc}")
            else:
                if not filt or filt in fname.lower():
                    functions.append(f"{sig_clean} {loc}")

    if active_cls is not None:
        if not filt or filt in active_cls_name.lower() or active_cls["methods"]:
            classes.append(active_cls)

    return {
        "imports": [],
        "constants": [],
        "classes": classes,
        "functions": functions,
    }


def extract_ast_contract_map(
    files: List[str],
    symbol_filter: Optional[str] = None,
    include_private: bool = True,
    project_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Builds a multi-file AST Contract & Line-Offset Map so Gemini 4 Argon can inspect
    exact function signatures, class hierarchies, and Lstart-Lend ranges at ~95-98%
    token compression before executing surgical read_file / edit_file calls.
    """
    start_t = time.perf_counter()
    cwd = Path(project_root) if project_root else Path.cwd()
    resolved_files: List[Path] = []
    for pattern in (files or []):
        p_obj = Path(pattern)
        if p_obj.exists() and p_obj.is_file():
            resolved_files.append(p_obj)
        elif not p_obj.is_absolute() and (cwd / p_obj).exists() and (cwd / p_obj).is_file():
            resolved_files.append(cwd / p_obj)
        else:
            glob_hits = glob.glob(str(pattern), recursive=True)
            if not glob_hits and not p_obj.is_absolute():
                glob_hits = glob.glob(str(cwd / pattern), recursive=True)
            for m in glob_hits:
                mp = Path(m)
                if mp.exists() and mp.is_file():
                    resolved_files.append(mp)

    # Deduplicate preserving order
    seen = set()
    unique_files: List[Path] = []
    for f in resolved_files:
        key = str(f.resolve()).lower()
        if key not in seen:
            seen.add(key)
            unique_files.append(f)

    if not unique_files:
        return {
            "success": False,
            "error": "No matching source files found for ast-map.",
            "files_mapped": [],
        }

    file_maps: List[Dict[str, Any]] = []
    total_raw_tokens = 0
    total_lines = 0

    for fpath in unique_files[:20]:
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        line_cnt = len(content.splitlines())
        raw_tok = estimate_tokens(content)
        total_raw_tokens += raw_tok
        total_lines += line_cnt

        parsed_map = None
        if fpath.suffix.lower() == ".py":
            try:
                parsed_map = _extract_python_ast_contracts(
                    content,
                    symbol_filter=symbol_filter,
                    include_private=include_private,
                )
            except Exception:
                parsed_map = None
        if parsed_map is None:
            parsed_map = _extract_generic_regex_contracts(
                content,
                symbol_filter=symbol_filter,
                suffix=fpath.suffix,
            )

        file_maps.append({
            "file": fpath.name,
            "path": str(fpath),
            "lines": line_cnt,
            "raw_tokens": raw_tok,
            "imports": parsed_map["imports"],
            "constants": parsed_map["constants"],
            "classes": parsed_map["classes"],
            "functions": parsed_map["functions"],
        })

    formatted = format_ast_contract_map({
        "files": file_maps,
        "total_lines": total_lines,
        "total_raw_tokens": total_raw_tokens,
    })
    map_tokens = estimate_tokens(formatted)
    saved_tokens = max(0, total_raw_tokens - map_tokens)
    compression_pct = round((saved_tokens / max(1, total_raw_tokens)) * 100.0, 1)
    latency_ms = int((time.perf_counter() - start_t) * 1000)

    return {
        "success": True,
        "files_mapped": [fm["file"] for fm in file_maps],
        "file_count": len(file_maps),
        "total_lines": total_lines,
        "raw_tokens": total_raw_tokens,
        "map_tokens": map_tokens,
        "saved_agy_tokens": saved_tokens,
        "saved_agy_turns": max(1, len(file_maps)),
        "compression_pct": compression_pct,
        "latency_ms": latency_ms,
        "contract_map": formatted,
        "files": file_maps,
    }


def format_ast_contract_map(data: Dict[str, Any]) -> str:
    """Formats the extracted AST contract map into a compact, line-indexed reference."""
    out: List[str] = []
    for fm in data.get("files", []):
        out.append(f"### File: {fm['file']} ({fm['lines']} lines, ~{fm['raw_tokens']:,} raw tokens)")
        if fm.get("imports"):
            out.append(f"  Imports: {'; '.join(fm['imports'][:10])}")
        if fm.get("constants"):
            out.append(f"  Constants: {', '.join(fm['constants'][:12])}")
        for cls in fm.get("classes", []):
            doc_s = f"  # {cls['doc']}" if cls.get("doc") else ""
            out.append(f"  * {cls['header']}{doc_s}")
            for m in cls.get("methods", []):
                out.append(f"      - {m}")
        for fn in fm.get("functions", []):
            out.append(f"  * {fn}")
        out.append("")
    return "\n".join(out).strip()
