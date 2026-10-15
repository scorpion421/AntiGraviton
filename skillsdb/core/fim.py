"""
Fill-in-the-Middle (FIM) surgical code extraction and context formatting.
Inspired by Mistral AI's Codestral architecture.
Slashes context bloat on file edits by up to 90% by providing minimal prefix and suffix windows.
"""

import re
from pathlib import Path
from typing import Dict, Any, Optional
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
    t_idx = max(0, min(total_line - 1 if (total_line := total_lines) else 0, target_line - 1))

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
        "target_line": target_line,
        "prefix_start_line": prefix_start + 1,
        "prefix_end_line": t_idx,
        "suffix_start_line": t_idx + 2,
        "suffix_end_line": suffix_end,
        "total_file_lines": total_lines,
        "prefix": prefix_chunk,
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

    # Search for def, class, func, const, let, var symbol patterns
    pat = re.compile(rf"\b(?:def|class|function|var|let|const|val|interface|type)\s+{re.escape(symbol_name)}\b")
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
