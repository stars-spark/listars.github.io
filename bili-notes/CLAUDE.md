# bili-notes 项目说明（给 Claude Code）

B 站视频个人知识库：用户在 B 站 App 里把视频收藏到指定收藏夹 → 服务定时同步 → 取字幕（没有字幕则本地 Whisper 语音识别）
→ DeepSeek 整理成结构化笔记 → 存入 SQLite 并推送通知 → 在手机网页（PWA）上阅读、搜索。
目标用户是看社科、历史讲解视频的人。当前阶段：开源、自部署、验证需求，以后可能做托管付费版。

用户文档见 README.md；本文件记录开发时需要知道、但 README 里不适合写的东西。

## 结构

- `bili_notes/bilibili.py`：B 站接口（WBI 签名、视频信息、字幕、音频流、收藏夹、稍后再看）
- `bili_notes/pipeline.py`：`fetch_transcript`（获取内容，需要 Cookie）与 `make_note`（处理内容，只接触文字）
- `bili_notes/llm.py`：DeepSeek / OpenAI 兼容接口（httpx 流式 SSE），Claude（anthropic SDK）
- `bili_notes/store.py`：SQLite，FTS5 trigram 全文搜索，少于 3 个字的词回退到 LIKE
- `bili_notes/service.py`：后台线程，负责同步收藏夹和处理队列
- `bili_notes/notify.py`：Bark / Server 酱 / PushPlus
- `bili_notes/web.py` + `templates/` + `static/`：FastAPI + Jinja2 手机网页、密码登录、PWA
- `bili_notes/config.py`：所有配置都从环境变量读，`.env` 由 `load_dotenv` 加载
- `deploy/deploy.sh`（在用户电脑上跑，通过 `ssh ecs` 用 tar 上传）、`deploy/remote-setup.sh`（在服务器上跑：venv + systemd）

## 必须遵守的设计约束

- **获取内容和处理内容保持分离**：`make_note` 及之后的步骤只能接收文字，不能依赖 Cookie 或 BiliClient。
  将来的托管版会把获取内容这一步放到用户本地完成。
- **不在用户之间共享内容**，不保存视频文件（音频识别完立即删除），只处理用户自己有权观看的视频。
- 部署在公网时必须有 `APP_PASSWORD`：`web.run` 在非本机地址且没有密码时会拒绝启动，不要去掉这个检查。
- 笔记提示词里的「可以对照的其他观点」原来叫「需要存疑的地方」，考虑到国内内容合规改成了现在的措辞。

## 环境坑（服务器是阿里云 ECS，在中国大陆）

- 服务器上访问 GitHub、Docker Hub、Hugging Face 都不稳定或不可用：代码用 tar over ssh 上传，
  pip 用阿里云镜像，Whisper 模型通过 `HF_ENDPOINT=https://hf-mirror.com` 下载。不要改成 `git clone` 或 Docker。
- B 站风控：不带签名的 `/x/web-interface/view` 在海外 IP 上会返回 412，所以用的是 `wbi/view`；
  每次处理都新建 BiliClient（WBI key 每天会变）；视频之间 sleep 3 秒。
- 没登录拿不到 AI 字幕，只能走语音识别，慢而且错字多。有 Cookie 时绝大多数视频都有 AI 字幕。
- DeepSeek 模型名（`deepseek-chat`）和 `max_tokens` 上限（默认 8192）以官方文档为准，可能会变。
- 只能通过 `http://IP:8765` 访问：要上 HTTPS 需要域名，大陆服务器用 80/443 端口还需要 ICP 备案。这件事要用户决定。

## 开发约定

- 面向用户的文字和代码注释都用中文；Python 3.10+。
- 测试：`pytest`，全部离线（B 站、LLM、推送都用 fake 或 `httpx.MockTransport`）。新功能要配测试。
- 静态检查：`python -m pyflakes bili_notes tests` 需要零输出。
- 不要提交 `.env`、`data/`、Cookie、API Key。不要在对话里要求用户粘贴密钥；需要时让用户自己编辑服务器上的 `.env`。

## 验证状态（截至 v0.2）

已实测：从真实 B 站公开收藏夹同步、下载音频、Whisper 识别、生成笔记（用的是模拟的 OpenAI 兼容接口）、
网页登录、分享添加、搜索、手机截图；`remote-setup.sh` 除 systemd 以外的步骤。

**尚未实测**：真实调用 DeepSeek、用户自己的 Cookie（私密收藏夹、AI 字幕、充电视频）、三个推送渠道真实送达、
在真实 ECS 上用 systemd 部署。这些是交接后首先要验证的。

## 后续路线（未开始）

HTTPS（需要用户决定域名和备案）→ 人物 / 概念页（跨视频汇总同一人物或概念）→ 跨视频问答（回答附视频和时间出处）
→ 合集合并笔记 → 复习提醒 → 电脑端浏览器插件。
