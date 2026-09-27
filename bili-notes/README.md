# bili-notes：B 站视频个人知识库

在 B 站 App 里**收藏一下**，几分钟后手机收到推送，一篇结构化笔记已经进了你的知识库：核心观点、分章大纲、
关键概念、人物与时间线、引用的史料和著作、可以对照的其他观点、延伸阅读和自测题。
每个要点都带时间戳，点一下跳回视频对应位置。所有看过的视频都能全文搜索。

专为社科、历史类讲解视频设计；自己部署，数据、Cookie 和 API Key 都只在你自己的服务器上。

## 工作方式

```
B 站 App：收藏到「待整理」 ──┐
网页 / 分享菜单：粘贴链接 ────┼─→ 队列 ─→ ① 获取内容：人工字幕 > B 站 AI 字幕 > 语音识别（faster-whisper）
                              │           ② 处理内容：DeepSeek 整理成笔记（只接触文字）
每 10 分钟自动同步 ───────────┘           ③ 存入 SQLite 知识库 + 导出 Markdown ─→ 推送通知（Bark / 微信）
```

## 部署到服务器（阿里云 ECS 等）

以下命令都在**你自己的电脑**上运行，前提是 `ssh ecs` 能登录服务器。

```bash
git clone -b claude/bilibili-video-knowledge-extract-xatnbk https://github.com/stars-spark/listars.github.io.git
cd listars.github.io/bili-notes
./deploy/deploy.sh            # 第一次运行会在服务器上生成 /opt/bili-notes/.env，然后停下
ssh ecs vi /opt/bili-notes/.env   # 填写配置（见下一节）
./deploy/deploy.sh            # 再运行一次：安装依赖、注册 systemd 服务并启动
```

最后在**阿里云控制台 → 安全组 → 入方向**放行 TCP 8765，手机浏览器打开 `http://服务器公网IP:8765`。

以后更新代码也是运行 `./deploy/deploy.sh`，服务器上的 `data/` 和 `.env` 不会被覆盖。
脚本会自动：找 Python 3.10+（没有时尝试用 dnf / apt 安装）、用阿里云 PyPI 镜像装依赖、注册开机自启。

常用运维命令（在服务器上）：

```bash
journalctl -u bili-notes -f          # 看日志
systemctl restart bili-notes         # 改完 .env 后重启
cp /opt/bili-notes/data/bili-notes.db ~/backup.db   # 备份：整个知识库就是这一个文件
```

## 配置（.env）

| 配置 | 说明 |
|---|---|
| `APP_PASSWORD` | 网页登录密码，**必填** |
| `DEEPSEEK_API_KEY` | [DeepSeek 开放平台](https://platform.deepseek.com/api_keys) 的 API Key |
| `BILI_COOKIE` | 电脑浏览器登录 bilibili.com → F12 → Application → Cookies → 复制 `SESSDATA` 的值 |
| `BILI_FAV_FOLDER` | 要同步的收藏夹名称，如 `待整理`（也可以填 media_id 或收藏夹链接） |
| `BARK_KEY` / `SERVERCHAN_KEY` / `PUSHPLUS_TOKEN` | 推送通知，配哪个用哪个：Bark 推 iPhone，Server 酱 / PushPlus 推微信 |
| `PUBLIC_URL` | 通知里「打开笔记」的链接前缀，如 `http://1.2.3.4:8765` |

更多可选项（同步「稍后再看」、同步间隔、多 P 上限、换模型、语音识别模型）见 `.env.example` 里的注释。

**关于 Cookie**：它等于你的登录状态，只放在服务器的 `.env` 里（权限 600）。
有了它才能读你的私密收藏夹、拿到 B 站 AI 字幕（绝大多数视频都有，比语音识别快得多也准得多），
充电专属视频需要已充电账号。Cookie 大约半年过期，过期后同步会失败并推送提醒，更新 `.env` 后重启即可。

## 手机上怎么用

1. **收藏即入库（推荐）**：在 B 站 App 里新建收藏夹「待整理」，看到想学的视频就收藏进去。
   服务每 10 分钟同步一次，第一次只取最近收藏的 10 个，之后只处理新收藏的。多 P 合集会自动逐 P 整理。
2. **添加到主屏幕**：用手机浏览器打开网页 → 分享 → 添加到主屏幕，之后像 App 一样打开。看过的笔记断网也能读。
3. **分享链接**：B 站 App 里「分享 → 复制链接」，粘贴到网页顶部的输入框。
   - iPhone 可以做一个快捷指令放进分享菜单：接收「URL / 文本」→ 用「URL 编码」处理输入 →
     打开 `http://服务器IP:8765/share?text=编码后的输入`。
   - 安卓的「分享到本应用」需要 HTTPS（见下方），目前用复制粘贴。

网页里可以：浏览全部笔记、全文搜索（逐字稿命中会显示时间点，点击直接跳转）、
重新生成笔记、重新获取字幕、下载 Markdown。每篇笔记也会导出到 `data/notes/*.md`，可以同步进 Obsidian。

## 速度与费用

- **有字幕**：几秒钟拿到逐字稿，整理大约 1 分钟。
- **没字幕走语音识别**：服务器没有显卡，默认用 `small` 模型，大约是视频时长的 1/3 到 1 倍，内存占用约 1GB。
  反正是后台跑完再推送，慢一点没关系。第一次会从 hf-mirror.com 下载模型（约 500MB）。
- **DeepSeek 费用**：1 小时视频的逐字稿约 2 万 token，整理一次大约几分钱（以 DeepSeek 官方价格为准）。

## 本地开发

```bash
pip install -e '.[all,dev]'
cp .env.example .env     # 本地可以不设 APP_PASSWORD
bili-notes serve         # http://127.0.0.1:8765
pytest
```

命令行也能用：`bili-notes add <链接>`、`bili-notes sync`、`bili-notes folders`（列出收藏夹）、`bili-notes search 张居正`。

| 文件 | 作用 |
|---|---|
| `bili_notes/bilibili.py` | 链接解析、WBI 签名、视频信息、字幕、音频流、收藏夹、稍后再看 |
| `bili_notes/pipeline.py` | 获取内容 / 处理内容两步，时间戳链接，Markdown 导出 |
| `bili_notes/llm.py` | DeepSeek 与任意 OpenAI 兼容接口（通义、Kimi、Ollama），以及 Claude |
| `bili_notes/store.py` | SQLite 知识库与全文搜索（FTS5 trigram，两个字的词回退到 LIKE） |
| `bili_notes/service.py` | 后台同步与处理队列 |
| `bili_notes/notify.py` | Bark / Server 酱 / PushPlus 推送 |
| `bili_notes/web.py`、`templates/`、`static/` | 手机网页（PWA） |
| `deploy/` | 一键部署脚本与 systemd 服务 |

## 已知限制与后续计划

- **HTTPS**：目前是 `http://IP:端口` 访问，登录密码明文传输。要用 HTTPS（也是安卓「分享到本应用」和完整 PWA 安装的前提），
  需要一个域名；在中国大陆的服务器上用 80/443 端口还需要 ICP 备案。之后可以加 Caddy 自动申请证书。
- **下一步**：人物 / 概念页（自动汇总不同视频里对同一个人物的讲法）、跨视频问答、合集合并笔记、复习提醒、电脑端浏览器插件。

## 使用须知

只处理你自己有权观看的内容，笔记仅供个人学习；请不要公开转载逐字稿或笔记，那是 UP 主的劳动成果，充电专属内容更是如此。
工具只下载最小码率的音轨用于识别，识别完立即删除，不保存视频。
