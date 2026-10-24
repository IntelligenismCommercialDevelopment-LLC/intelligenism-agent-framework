"""
voice.py —— Fish Audio TTS 客户端（集群级共享）

与 llm_client.py / jev_client.py 同层：纯客户端，只管 HTTP 与重试。
语气标注由调用方拼进文本（表演模型读文本开头的方括号标注），本文件不做词表映射。
连接参数（url / api_key / model / voices）住各单元自己的 config.json 的
services.fish_audio 节，**由调用方把 config 路径传进来** —— 本文件不猜它在哪，
猜了就会在换单元时静默读错。

使用方式：
    from voice import generate_speech, load_fish_config
"""

import asyncio
import json
import os

import httpx


def load_fish_config(config_path: str) -> dict:
    """从指定的 config.json 读取 services.fish_audio 节。

    ★路径必须由调用方给：本文件住集群 lib/，旁边没有 config.json，
      按 __file__ 猜会读到不存在的文件或别的单元的那一份。
    ★model 当必需键、不给默认值：Fish Audio 的模型靠请求头选，省略时服务端用它自己的
      默认模型，而方括号表演标注在非表演模型上不生效 —— 请求照样 200、音频照样出、
      时长也对，只是整期变成平读，没有任何一处报错。这种缺配置必须在这里当场炸。
      「哪些键是必需的」只在这一处知道，两个单元不各判一遍。
    """
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    cfg = config["services"]["fish_audio"]
    if not cfg.get("model"):
        raise KeyError("services.fish_audio.model 未配置：%s" % config_path)
    return cfg


# ─────────────────────────────────────────────
# TTS 生成（共用核心）
# ─────────────────────────────────────────────

async def generate_speech(
    text: str,
    voice_id: str,
    output_path: str,
    api_url: str,
    api_key: str,
    model: str,
    max_retries: int = 3,
):
    """生成单条语音（Fish Audio），失败时指数退避重试。

    ★语气由调用方拼进 text —— 表演模型读文本开头的方括号标注
      （如 "[quiet, deliberate] 原话"）决定怎么念。本函数不认识任何情绪词表，
      也不替调用方做映射：中间加一层词表映射，就得维护「哪些词认、哪些词悄悄落空」。
    ★model 是必需参数、刻意不给默认值：漏传就该在调用处报 TypeError，
      而不是悄悄落到服务端的默认模型上去念一期平读的节目。
    """
    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    api_url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        # ★模型靠请求头选，不在请求体里。请求体里写 model 不会报错，
                        #   只是被忽略 —— 然后服务端照它自己的默认模型念。
                        "model": model,
                    },
                    json={
                        "text": text,
                        "reference_id": voice_id,
                        "format": "mp3",
                    },
                )
                response.raise_for_status()

                with open(output_path, "wb") as f:
                    f.write(response.content)

        except Exception as e:
            if os.path.exists(output_path):
                os.remove(output_path)
            if attempt < max_retries:
                wait = 2 ** (attempt + 1)
                print(f"  [Voice] TTS error: {e}, retrying ({attempt + 1}/{max_retries}), wait {wait}s")
                await asyncio.sleep(wait)
                continue
            raise RuntimeError(f"TTS error after {max_retries} retries: {os.path.basename(output_path)}: {e}")

        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return  # 成功

        if os.path.exists(output_path):
            os.remove(output_path)
        if attempt < max_retries:
            wait = 2 ** (attempt + 1)
            print(f"  [Voice] Empty output, retrying ({attempt + 1}/{max_retries}), wait {wait}s")
            await asyncio.sleep(wait)

    raise RuntimeError(f"TTS produced empty file after {max_retries} retries: {os.path.basename(output_path)}")


# ---------------------------------------------------------------------------
# Charlie Presents — single-voice TTS
# ---------------------------------------------------------------------------

async def generate_charlie_speeches(
    presentation: dict,
    audio_dir: str,
    config_path: str,
    language: str = "en",
):
    """
    Generate TTS audio for all narration segments in a presentation.

    Uses Charlie's voice from config.json based on language.
    Tone tags are prepended to the text as bracket performance directives.

    Args:
        presentation: presentation.json dict
        audio_dir:    output directory for audio files
        config_path:  调用方那个单元的 config.json 绝对路径
        language:     "cn" or "en"
    """
    fish_cfg = load_fish_config(config_path)
    api_url = fish_cfg["url"]
    api_key = fish_cfg["api_key"]
    model = fish_cfg["model"]        # load_fish_config 已保证它非空
    voice_map = fish_cfg.get("voices", {})

    # Resolve Charlie's voice_id based on language
    charlie_voices = voice_map.get("charlie", {})
    if isinstance(charlie_voices, dict):
        charlie_voice_id = charlie_voices.get(language, "")
    else:
        charlie_voice_id = charlie_voices or ""

    if not charlie_voice_id or charlie_voice_id in ("voice_id_en", "voice_id_cn"):
        print("  ⚠ Charlie voice_id not configured in config.json, skipping TTS")
        return

    os.makedirs(audio_dir, exist_ok=True)

    narration_segments = [
        s for s in presentation.get("segments", [])
        if s.get("type") == "narration"
    ]

    # Build tasks (skip existing files for resume support)
    tasks = []
    for seg in narration_segments:
        output_path = os.path.join(audio_dir, f"seg_{seg['id']:02d}.mp3")
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            continue

        # 语气标注拼在台词前面 —— Fish Audio 读它决定怎么念
        tone_tag = seg.get("tone_tag", "")
        line_text = seg.get("line_cn" if language == "cn" else "line_en", "")
        text = f"{tone_tag} {line_text}".strip()

        tasks.append({
            "text": text,
            "voice_id": charlie_voice_id,
            "output_path": output_path,
            "seg_id": seg["id"],
        })

    if not tasks:
        print(f"  All {len(narration_segments)} audio files already exist")
        return

    print(f"  Generating {len(tasks)} audio files...")

    # Fish Audio 的并发上限按累计消费分档：未满 $100 是 5 个并发请求。
    # 5 是起步档的顶，不是随手取的数 —— 账户升档后这里才有调大的余地。
    batch_size = 5
    for i in range(0, len(tasks), batch_size):
        batch = tasks[i:i + batch_size]
        results = await asyncio.gather(*[
            generate_speech(
                t["text"], t["voice_id"], t["output_path"],
                api_url, api_key, model,
            )
            for t in batch
        ], return_exceptions=True)

        for t, result in zip(batch, results):
            if isinstance(result, Exception):
                print(f"    FAILED seg_{t['seg_id']:02d}: {result}")

        done = min(i + batch_size, len(tasks))
        print(f"    Progress: {done}/{len(tasks)}")

    # Write audio paths back to presentation segments
    for seg in narration_segments:
        audio_path = os.path.join(audio_dir, f"seg_{seg['id']:02d}.mp3")
        if os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
            seg["audio_file"] = f"audio/seg_{seg['id']:02d}.mp3"

    print(f"  ✓ {len(narration_segments)} audio files processed")
