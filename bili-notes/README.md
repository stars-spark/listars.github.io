# bili-notes：把 B 站讲解视频变成「读完就够」的知识笔记

几十分钟的社会学、历史讲解视频，换成一篇 3–5 分钟就能读完的结构化笔记：核心观点、分章大纲、
关键概念、人物与时间线、引用的史料和著作、**值得存疑的地方**、延伸阅读和自测题。
每个要点都带着时间戳链接，点一下就跳到视频的对应位置，想核对原话或细看某一段时再去看。

## 工作原理

```
视频链接 / BV 号
   │
   ├─①  有字幕？（人工字幕 > B 站 AI 字幕，AI 字幕需要登录 Cookie）
   │       └─ 有 → 直接用，几秒钟完成
   │
   ├─②  没有 → 用你的 Cookie 只下载最小码率的音频流（充电视频需要已充电账号）
   │       └─ 本地 faster-whisper 语音识别 → 带时间戳的逐字稿
   │
   ├─③  逐字稿按约 30 秒一段合并，每段一个时间戳，缓存在 notes/.transcripts/
   │
   └─④  逐字稿 + 标题、简介、标签 → Claude 整理成笔记 → notes/<标题> [BV号].md
```

整理时会让模型结合上下文更正语音识别的同音错字（人名、地名、书名最常见），拿不准的会标出原文。
「需要存疑的地方」一节是模型的独立判断，和 UP 主的观点分开写，方便你批判地阅读。

## 安装

需要 Python 3.10+。

```bash
cd bili-notes
pip install -e '.[all]'      # 只用字幕、不需要语音识别和网页：pip install -e .
cp .env.example .env         # 然后填写 ANTHROPIC_API_KEY 和 BILI_COOKIE
```

**获取 B 站 Cookie**：浏览器登录 bilibili.com → F12 打开开发者工具 → Application（应用）→
Cookies → `https://www.bilibili.com` → 复制 `SESSDATA` 的值填进 `BILI_COOKIE`。

- 不填也能用，但拿不到 AI 字幕，会更多地走语音识别（慢）。
- 充电专属视频必须用**已经给该 UP 主充电的账号**的 Cookie。
- Cookie 等同于登录状态，只放在自己电脑的 `.env` 里，不要提交到 Git、不要发给别人。

## 使用

```bash
# 单个或多个视频（BV 号、链接、av 号、b23.tv 短链都可以；多 P 视频用 ?p=2 指定）
bili-notes BV1ThZnYxEZi "https://www.bilibili.com/video/BV1BtZJYBE4G/?p=1"

# 把「稍后再看」里的视频全部整理一遍（需要 Cookie）
bili-notes --watchlater

# 本地网页：粘贴链接 → 生成 → 在浏览器里读，手机在同一局域网也能用（见下方安全提示）
bili-notes serve             # 打开 http://127.0.0.1:8765
```

常用参数：

| 参数 | 说明 |
|---|---|
| `--model` | Claude 模型，默认 `claude-opus-5`；想省钱可以用 `claude-sonnet-5` |
| `--effort` | 思考深度 `low` / `medium` / `high`（默认）/ `xhigh` / `max` |
| `--whisper-model` | 语音识别模型。有 NVIDIA 显卡用默认的 `large-v3-turbo`；只有 CPU 建议 `small` 或 `medium` |
| `--prompt-file` | 用你自己的整理要求替换默认提示词（比如只要时间线、或者要 Anki 卡片） |
| `--force` | 忽略缓存的逐字稿重新获取；不加时，重复整理同一个视频只花模型费用 |
| `-o` | 笔记输出目录，默认 `notes/`，可以直接指向 Obsidian 仓库 |

输出的 Markdown 带 YAML frontmatter（标题、链接、UP 主、时长、来源），可以直接放进 Obsidian / Logseq。

## 速度与费用（粗略估计）

- **有字幕**：获取逐字稿只要几秒。
- **语音识别**：1 小时的视频，显卡上 `large-v3-turbo` 大约几分钟；纯 CPU 用 `small` 可能要 20–40 分钟，
  而且小模型错字更多。所以**强烈建议填 Cookie 优先使用 B 站 AI 字幕**。
- **模型费用**：1 小时中文讲解的逐字稿大约 1.5–2 万字。按 `claude-opus-5` 的价格（输入 $5 / 输出 $25 每百万 token），
  一个小时的视频大约 0.2–0.5 美元；`claude-sonnet-5` 约为其 40%。

## 注意事项

- 这个工具只是替你「看」你**本来就有权观看**的内容：用你自己的账号、你自己付过费的视频，笔记仅供个人学习。
  请不要把逐字稿或笔记公开转载——那是 UP 主的劳动成果，充电专属内容更是如此。
- 下载音频时只取最小码率的音轨、识别完就删除，不保存视频文件。
- 如果只拿到了试看片段（音频明显短于视频时长），程序会直接报错提示，而不是基于不完整内容生成笔记。
- `bili-notes serve` 默认只监听本机。若要 `--host 0.0.0.0` 或部署到服务器，务必在前面加鉴权
  （例如反向代理的 Basic Auth），否则别人可以用你的 Cookie 和 API Key 跑任务。
- 频繁批量请求可能触发 B 站风控（HTTP 412），遇到时隔一段时间再试。

## 开发

```bash
pip install -e '.[all,dev]'
pytest
```

代码结构：

| 文件 | 作用 |
|---|---|
| `bili_notes/bilibili.py` | 链接解析、WBI 签名、视频信息、字幕、音频流、稍后再看 |
| `bili_notes/transcribe.py` | faster-whisper 语音识别 |
| `bili_notes/summarize.py` | 提示词与 Claude 调用 |
| `bili_notes/pipeline.py` | 串起整个流程、缓存、时间戳链接、写 Markdown |
| `bili_notes/cli.py` / `web.py` | 命令行与本地网页服务 |

可以继续扩展的方向：处理收藏夹、定时自动整理新的「稍后再看」、对多期系列视频做合并笔记、
把笔记同步到 Notion / 飞书。
