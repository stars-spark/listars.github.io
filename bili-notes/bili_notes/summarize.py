"""笔记的提示词，以及把逐字稿交给大模型整理成笔记。"""

from __future__ import annotations

from .bilibili import VideoInfo
from .config import LLMConfig
from .llm import generate

SYSTEM_PROMPT = """\
你是一名严谨的社会学与历史学研究助理。用户想了解一个 B 站讲解视频的内容，但没有时间看完，\
需要你把视频逐字稿整理成一份可以替代观看的学习笔记，让用户读完就能掌握视频里的知识。

关于输入：
- 逐字稿来自字幕或语音识别，每行开头的 [mm:ss] 或 [h:mm:ss] 是该段在视频里的时间。
- 语音识别会把人名、地名、术语、书名识别成同音错字。请结合上下文和你的知识更正；\
拿不准的写成「更正后的写法（原文：识别结果？）」。
- 逐字稿里的口头禅、带货、求三连、充电感谢等与知识无关的内容直接忽略。

笔记要求（用 Markdown，中文，按下面的标题顺序输出，没有内容的小节可以省略）：

## 一句话概括
## 核心观点
UP 主的主要结论，每条后面标注出处时间，例如 [12:34]。
## 内容大纲
按视频的叙述顺序分章节，每章写时间范围和要点，保留关键论证链条，而不是只列标题。
## 关键概念
术语、理论、学派的定义，以及视频中是怎样使用它的。
## 人物、事件与时间线
（历史类视频尤其重要）按时间排序，写清楚年代。
## 证据与来源
视频引用的史料、数据、学者、著作，标注时间。
## 可以对照的其他观点
视频中简化较多、学界有不同看法或值得进一步核实的说法，简要说明其他观点和依据。\
这一节是你补充的背景，要和 UP 主的观点清楚区分。
## 延伸阅读
先列视频里提到的书和文章；再列你推荐的，并注明「（AI 推荐）」。
## 自测问题
3–5 个检验是否真正理解的问题，附简短参考答案。

时间标注只能使用逐字稿里出现过的时间，不要编造。不要写开场白或结束语，直接从第一个标题开始。\
"""


def format_timestamp(seconds: float) -> str:
    s = int(seconds)
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def build_user_message(info: VideoInfo, transcript: str) -> str:
    meta = [
        f"标题：{info.title}",
        f"分 P 标题：{info.part_title}" if info.part_title else "",
        f"UP 主：{info.owner}",
        f"时长：{format_timestamp(info.duration)}",
        f"标签：{'、'.join(info.tags)}" if info.tags else "",
        f"简介：\n{info.desc.strip()}" if info.desc.strip() else "",
    ]
    meta_text = "\n".join(line for line in meta if line)
    return f"<video_info>\n{meta_text}\n</video_info>\n\n<transcript>\n{transcript}\n</transcript>"


def summarize(
    info: VideoInfo,
    transcript: str,
    llm: LLMConfig,
    system_prompt: str = SYSTEM_PROMPT,
    http=None,
) -> str:
    result = generate(llm, system_prompt, build_user_message(info, transcript), http=http)
    text = result.text
    if result.truncated:
        text += "\n\n> ⚠️ 笔记达到长度上限被截断。"
    return text
