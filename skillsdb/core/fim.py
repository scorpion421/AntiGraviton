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
from typing import Dict, Any, Optional, List
from ..config import estimate_tokens


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
        "prefix_start_line": (prefix_start + 1) if total_lines > 0 else 0,
        "prefix_end_line": max(prefix_start + 1, t_idx) if total_lines > 0 else 0,
        "suffix_start_line": min(suffix_end, t_idx + 2) if total_lines > 0 else 0,
        "suffix_end_line": suffix_end,
        "total_file_lines": total_lines,
        "prefix": prefix_chunk,
        "middle": target_line_content,
        "target_line_content": target_line_content,
        "suffix": suffix_chunk,
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
    Finds the definition line of a symbol (function, class, variable) and slices the FIM window.
    """
    fpath = Path(file_path)
    if not fpath.exists() or not fpath.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")

    content = fpath.read_text(encoding="utf-8", errors="replace")
    all_lines = content.splitlines()

    # Search for def, async def, class, func, fn, struct, enum, const, let, var symbol patterns
    pat = re.compile(rf"\b(?:(?:async\s+)?def|class|function|func|fn|struct|enum|var|let|const|val|interface|type)\s+{re.escape(symbol_name)}\b")
    match_line = None

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

    res = slice_fim(fpath, match_line, prefix_lines=prefix_lines, suffix_lines=suffix_lines)
    res["symbol"] = symbol_name
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
    """Parses Python source into a compact interface & line-offset contract map."""
    tree = ast.parse(content)
    filt = (symbol_filter or "").strip().lower()

    imports: List[str] = []
    constants: List[str] = []
    classes: List[Dict[str, Any]] = []
    functions: List[Dict[str, Any]] = []

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            names = ", ".join(a.name for a in node.names[:6])
            if len(node.names) > 6:
                names += ", ..."
            imports.append(f"{mod}: {{{names}}}" if mod else names)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    name = t.id
                    if not include_private and name.startswith("_") and not name.startswith("__"):
                        continue
                    if filt and filt not in name.lower():
                        continue
                    if name.isupper() or name.startswith("__"):
                        constants.append(f"{name} [L{node.lineno}]")
        elif isinstance(node, ast.ClassDef):
            cname = node.name
            end_l = getattr(node, "end_lineno", node.lineno)
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
                    m_end = getattr(item, "end_lineno", item.lineno)
                    prefix = "async def" if isinstance(item, ast.AsyncFunctionDef) else "def"
                    sig = _format_ast_signature(item)
                    doc_s = _first_doc_line(item)
                    doc_tag = f"  # {doc_s}" if doc_s else ""
                    methods.append(f"{prefix} {mname}{sig} [L{item.lineno}-L{m_end}]{doc_tag}")
            if filt and filt not in cname.lower() and not methods:
                continue
            classes.append({
                "header": f"class {cname}{base_str} [L{node.lineno}-L{end_l}]",
                "doc": _first_doc_line(node),
                "methods": methods,
            })
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fname = node.name
            if not include_private and fname.startswith("_") and not fname.startswith("__"):
                continue
            if filt and filt not in fname.lower():
                continue
            end_l = getattr(node, "end_lineno", node.lineno)
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            sig = _format_ast_signature(node)
            doc_s = _first_doc_line(node)
            doc_tag = f"  # {doc_s}" if doc_s else ""
            functions.append(f"{prefix} {fname}{sig} [L{node.lineno}-L{end_l}]{doc_tag}")

    return {
        "imports": imports[:16],
        "constants": constants[:20],
        "classes": classes,
        "functions": functions,
    }


def _extract_generic_regex_contracts(
    content: str,
    symbol_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """Fallback structure & line-offset extractor for PowerShell, TS/JS, Go, Rust, C#, Java, Kotlin, Dart."""
    filt = (symbol_filter or "").strip().lower()
    lines = content.splitlines()
    functions: List[str] = []
    classes: List[Dict[str, Any]] = []

    cls_re = re.compile(r"^\s*(?:export\s+)?(?:public\s+|private\s+|abstract\s+)?(?:class|interface|struct|enum)\s+([A-Za-z_][A-Za-z0-9_]*)")
    fn_re = re.compile(
        r"^\s*(?:export\s+)?(?:async\s+)?(?:function|def|func|fn)\s+([A-Za-z_][A-Za-z0-9_-]*)\s*(\([^)]*\))?"
    )

    for idx, raw_line in enumerate(lines, 1):
        cm = cls_re.match(raw_line)
        if cm:
            cname = cm.group(1)
            if not filt or filt in cname.lower():
                classes.append({
                    "header": f"{raw_line.strip().rstrip('{').strip()} [L{idx}]",
                    "doc": "",
                    "methods": [],
                })
            continue
        fm = fn_re.match(raw_line)
        if fm:
            fname = fm.group(1)
            if not filt or filt in fname.lower():
                functions.append(f"{raw_line.strip().rstrip('{').strip()} [L{idx}]")

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
) -> Dict[str, Any]:
    """
    Builds a multi-file AST Contract & Line-Offset Map so Gemini 4 Argon can inspect
    exact function signatures, class hierarchies, and Lstart-Lend ranges at ~95-98%
    token compression before executing surgical read_file / edit_file calls.
    """
    start_t = time.perf_counter()
    resolved_files: List[Path] = []
    for pattern in (files or []):
        p_obj = Path(pattern)
        if p_obj.exists() and p_obj.is_file():
            resolved_files.append(p_obj)
        else:
            for m in glob.glob(pattern, recursive=True):
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
            parsed_map = _extract_generic_regex_contracts(content, symbol_filter=symbol_filter)

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
