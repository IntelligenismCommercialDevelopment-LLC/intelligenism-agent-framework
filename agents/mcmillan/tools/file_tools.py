"""
File tools: read, write, list directory.
Default tool set included in every new agent.
"""

import os


def _read_file(args):
    path = args["path"]
    if not os.path.exists(path):
        return f"Error: file not found: {path}"

    if path.endswith(".docx"):
        from docx import Document
        doc = Document(path)
        return "\n".join(p.text for p in doc.paragraphs)

    elif path.endswith(".pdf"):
        import fitz
        doc = fitz.open(path)
        return "\n".join(page.get_text() for page in doc)

    else:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()


def _write_file(args):
    path = args["path"]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(args["content"])
    return f"Written to {path}"


def _list_dir(args):
    path = args.get("path", ".")
    if not os.path.exists(path):
        return f"Error: directory not found: {path}"
    return "\n".join(os.listdir(path))


TOOLS = {
    "read_file": {
        "description": "读取指定路径的文件内容，支持 .docx, .pdf, .md, .txt 等格式",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"}
            },
            "required": ["path"]
        },
        "handler": _read_file
    },
    "write_file": {
        "description": "将文本内容写入指定路径的文件",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目标文件路径"},
                "content": {"type": "string", "description": "要写入的内容"}
            },
            "required": ["path", "content"]
        },
        "handler": _write_file
    },
    "list_dir": {
        "description": "列出目录中的文件和子目录",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径，默认当前目录"}
            }
        },
        "handler": _list_dir
    },
}
