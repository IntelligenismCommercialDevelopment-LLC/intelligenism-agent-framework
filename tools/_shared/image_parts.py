"""
image_parts.py — 把本机图片文件拼成多模态消息的 content parts（纯函数·零网络·零 LLM）。

★为什么住 shell 的 tools/_shared/ 而不是某个 dispatch 的 tools/ 里：
  图片必须进【user 回合】的 content 数组。dispatch 的 tools/*_tools.py 产出的是
  role="tool" 的消息，而 Chat Completions 的 tool 消息只认文本 —— 图片走不了工具那条路。
  拼 user 回合的是 shell 的 chain.py，所以这份工具归 chain 用；chain 启动时已把
  tools/_shared 放进 sys.path（见 chain.py 顶部），直接 `import image_parts` 即可。
  它不参与 sync_config 的工具下发（那边只铺 explore_tools.py / status_log.py），
  所以不会被当成 dispatch 工具装载，也不会出现在 LLM 看得见的工具清单里。

产出形状 = OpenAI Chat Completions 的多模态 content parts，DeepSeek / Anthropic 两条
端点都认（见 lib/llm_client.py：chat-completions 分支把 messages 逐字透传，
anthropic 分支透传 content blocks）：

    [{"type": "text", "text": "..."},
     {"type": "image_url", "image_url": {"url": "data:image/png;base64,...."}}]

⚠ 走 OpenAI 的 /v1/responses 端点时形状不同（图片 part 叫 input_image），本模块不管那条。

判型按【文件内容的魔数】而不是扩展名：服务端也是按内容判型，而 data URL 里的 mime
声明必须和真实内容对得上。扩展名写错不会被发现，魔数对不上则当场报错。
"""

import base64
import os

# 服务端只认这四种。键 = 魔数判别函数，值 = data URL 里要声明的 mime。
_MIME_PNG = "image/png"
_MIME_JPEG = "image/jpeg"
_MIME_GIF = "image/gif"
_MIME_WEBP = "image/webp"

# 单图上限（内联 base64 / 外部 URL 都是这个数）。Files API 的上限更高，本模块不走那条。
MAX_IMAGE_BYTES = 32 * 1024 * 1024
# 整个请求体上限 48 MiB，而 base64 把体积放大到 4/3 ⇒ 原始字节总量必须留在 36 MiB 以内。
MAX_TOTAL_RAW_BYTES = 36 * 1024 * 1024
# 单次请求的图片张数上限。
MAX_IMAGES = 600


def sniff_mime(data: bytes) -> str:
    """按魔数判图片类型。认不出就抛 —— 这是刻意的：
    服务端按内容判型，喂一个它不认的格式只会换成一个难读的 400。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return _MIME_PNG
    if data.startswith(b"\xff\xd8\xff"):
        return _MIME_JPEG
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return _MIME_GIF
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _MIME_WEBP
    raise ValueError(
        "无法识别的图片格式（只支持 PNG / JPEG / GIF / WebP，按文件内容判型，"
        "不看扩展名）。前 12 字节：%r" % (data[:12],)
    )


def image_part(path: str, detail: str | None = None) -> dict:
    """读一个本机图片文件 → 一个 image_url part（base64 data URL 内联）。

    detail: 服务端的取图精度旋钮（如 "auto"）。不传则不下发该键，用服务端默认。
    """
    full = os.path.abspath(path)
    if not os.path.isfile(full):
        raise FileNotFoundError("图片不存在: %s" % full)
    size = os.path.getsize(full)
    if size == 0:
        raise ValueError("图片是空文件: %s" % full)
    if size > MAX_IMAGE_BYTES:
        raise ValueError("单图超过 %d 字节上限（本图 %d）: %s"
                         % (MAX_IMAGE_BYTES, size, full))
    with open(full, "rb") as f:
        data = f.read()
    mime = sniff_mime(data)
    url = "data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii"))
    part = {"type": "image_url", "image_url": {"url": url}}
    if detail:
        part["image_url"]["detail"] = detail
    return part


def build_content(text: str, image_paths=None, detail: str | None = None):
    """拼一条 user 回合的 content。

    没有图片 ⇒ 返回**字符串**（与不带图时的老形状逐字一致，下游一个字不用改）。
    有图片   ⇒ 返回 parts 列表：文本在前、图片按给定顺序在后。

    文本为空且有图时不塞空 text part：空字符串的 part 在部分端点上会被当成非法入参。
    """
    paths = [p for p in (image_paths or []) if p]
    if not paths:
        return text
    if len(paths) > MAX_IMAGES:
        raise ValueError("单次请求最多 %d 张图（本次 %d）" % (MAX_IMAGES, len(paths)))
    total = sum(os.path.getsize(p) for p in paths if os.path.isfile(p))
    if total > MAX_TOTAL_RAW_BYTES:
        raise ValueError("图片原始字节总量 %d 超过 %d —— base64 会放大到 4/3，"
                         "再加文本就会顶破请求体上限" % (total, MAX_TOTAL_RAW_BYTES))
    parts = []
    if text:
        parts.append({"type": "text", "text": text})
    for p in paths:
        parts.append(image_part(p, detail=detail))
    return parts


def content_to_text(content) -> str:
    """从 content（字符串或 parts 列表）里取出纯文本，供只会读文本的旁路使用
    （会话标题、token 估算、把历史渲染成 markdown 这类地方）。
    图片不贡献文本 —— 它在账上按服务端的每图计费口径算，不按字符数算。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content
                         if isinstance(p, dict) and p.get("type") == "text")
    return ""
