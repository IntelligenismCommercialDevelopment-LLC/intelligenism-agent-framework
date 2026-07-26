"""
explore_tools.py — 任务型探索工具集（带路径围栏）

不导出顶层 TOOLS。由 dispatch.py 调用 make_tools() 工厂函数，
注入 free_search_path / output_path 后生成带围栏的工具。
"""
import os
import re


def make_tools(read_roots: list, write_root: str) -> dict:
    """
    工厂函数：生成带路径围栏的探索工具集。

    Args:
        read_roots: 允许读取的根目录列表（已 realpath）
        write_root: 允许写入的根目录（已 realpath）

    Returns:
        TOOLS dict，结构同 file_tools.py，可直接被 dispatch 使用
    """

    # ── 围栏校验 ──

    def _validate_read(path):
        """校验路径在读取围栏内，返回 realpath"""
        real = os.path.realpath(path)
        for root in read_roots:
            if real == root or real.startswith(root + os.sep):
                return real
        raise ValueError(f"读取路径越界: {path} (解析为 {real})")

    def _validate_write(path):
        """校验路径在写入围栏内，返回 realpath"""
        real = os.path.realpath(path)
        if real == write_root or real.startswith(write_root + os.sep):
            return real
        raise ValueError(f"写入路径越界: {path} (解析为 {real})")

    # ── 工具 handler ──

    def _list_dir(args):
        path = args.get("path", read_roots[0] if read_roots else ".")
        real = _validate_read(path)
        if not os.path.isdir(real):
            return f"Error: not a directory: {path}"
        entries = []
        for name in sorted(os.listdir(real)):
            full = os.path.join(real, name)
            tag = "[DIR]" if os.path.isdir(full) else "[FILE]"
            entries.append(f"{tag} {name}")
        return "\n".join(entries) if entries else "(empty directory)"

    def _read_file(args):
        path = args["path"]
        real = _validate_read(path)
        if not os.path.isfile(real):
            return f"Error: file not found: {path}"
        if real.endswith(".pdf"):
            try:
                import fitz
                doc = fitz.open(real)
                return "\n".join(page.get_text() for page in doc)
            except ImportError:
                return "Error: fitz (PyMuPDF) not installed"
        if real.endswith(".docx"):
            try:
                from docx import Document
                doc = Document(real)
                return "\n".join(p.text for p in doc.paragraphs)
            except ImportError:
                return "Error: python-docx not installed"
        with open(real, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    def _grep(args):
        pattern = args["pattern"]
        search_path = args.get("path", read_roots[0] if read_roots else ".")
        real = _validate_read(search_path)
        max_results = args.get("max_results", 50)

        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return f"Error: invalid regex: {e}"

        results = []
        # 若 search_path 是文件则只搜该文件
        if os.path.isfile(real):
            file_list = [real]
        else:
            file_list = []
            for dirpath, _, filenames in os.walk(real):
                for fname in filenames:
                    fpath = os.path.join(dirpath, fname)
                    try:
                        _validate_read(fpath)
                    except ValueError:
                        continue
                    file_list.append(fpath)

        for fpath in sorted(file_list):
            try:
                size = os.path.getsize(fpath)
                if size > 1_000_000:  # 1MB 上限
                    continue
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    for lineno, line in enumerate(f, 1):
                        if regex.search(line):
                            display = fpath
                            for root in read_roots:
                                if fpath.startswith(root + os.sep):
                                    display = fpath[len(root)+1:]
                                    break
                            results.append(f"{display}:{lineno}: {line.rstrip()}")
                            if len(results) >= max_results:
                                results.append(f"... (已达 {max_results} 条上限)")
                                return "\n".join(results)
            except (IOError, UnicodeDecodeError):
                continue

        if not results:
            return f"No matches for pattern: {pattern}"
        return "\n".join(results)

    def _write_file(args):
        path = args["path"]
        real = _validate_write(path)
        os.makedirs(os.path.dirname(real) or ".", exist_ok=True)
        with open(real, "w", encoding="utf-8") as f:
            f.write(args["content"])
        return f"Written to {path}"

    # ── TOOLS dict ──

    return {
        "list_dir": {
            "description": "列出指定目录的内容。每行标注 [DIR] 或 [FILE]。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径"}
                }
            },
            "handler": _list_dir,
        },
        "read_file": {
            "description": "读取指定文件的完整内容。支持 .md, .txt, .py, .json, .pdf, .docx 等格式。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"}
                },
                "required": ["path"]
            },
            "handler": _read_file,
        },
        "grep": {
            "description": "在指定路径内搜索匹配正则表达式的行。返回 文件:行号: 内容 格式。优先用此工具定位关键内容，再用 read_file 精读。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "正则表达式"},
                    "path": {"type": "string", "description": "搜索根目录或文件路径（默认探索根）"},
                    "max_results": {"type": "integer", "description": "最大返回条数，默认 50"}
                },
                "required": ["pattern"]
            },
            "handler": _grep,
        },
        "write_file": {
            "description": "将内容写入指定路径的文件（仅可写入输出目录）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "输出文件路径"},
                    "content": {"type": "string", "description": "要写入的内容"}
                },
                "required": ["path", "content"]
            },
            "handler": _write_file,
        },
    }
