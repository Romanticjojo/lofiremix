# DJ Agent

在 Windows 笔记本上分析本地音乐、自动编排和生成混音录音，并为 Mixxx / DDJ-200 提供接入基础。

**2026-09-25 最新试听版 v4：** DJ 试听室已更新为 15 首、约 19 分 39 秒，加入 Creepin'、Cry For Me，排除 Earned It、A Lonely Night；保留封面、情绪标签、波形、逐曲跳转和 14 次转场控制回放。本地双击 `Start-20-Minute-Sample.cmd`，产物在 `outputs/weeknd-20min-20260925-v4`；原 romanticjojo.com 随机链接已更新，www 仍有待登录 ESA 清除的旧缓存。详情见 [云端交付](docs/cloud-deployment.md)。

**真实参考音轨训练：** 8 个完整 MP4 和音轨位于 `reference_videos/20260925`；新增两首后曲库 25 首。已从音频提取 30 条弱转场示范，训练六参数声学选曲、650 参数曲序和七参数切点模型，权重在 `outputs/reference-audio-model-20260925-v3`。声学模型值得继续试验，切点误差仍过大；三个新模型均未接管线上播放。见 [模型结果和测试结论](docs/reference-audio-training.md)；[新名称候选](docs/naming-options.md)。

**当前交付是可运行的第一版离线自动混音核心。** 已接入 Beat This! 预训练节拍模型；播放曲序与转场仍由可解释规则选择。另已训练参考视频曲序的实验性选曲模型，尚未训练个人听感偏好模型，也没有接入付费 LLM。Mixxx 桥接和真实控制器验收单独记录，不能把离线混音当作完整的实时 DDJ-200 演出。

## 直接使用

1. 将至少两首实际音频放入 `D:\DJ_agent\weeknd`。支持 MP3、FLAC、WAV、M4A 等可解码文件。源文件只读。
2. 双击 `D:\DJ_agent\Start-DJ.cmd`。
3. 程序分析曲库，规划约 25 分钟的混音，并打开试听报告。录音和转场片段保存在 `outputs` 的新会话文件夹。

曲库不足 25 分钟时，报告实际可生成的时长，不重复填充。空曲库会明确报错，不产生假测试结果。首次运行预训练分析会加载模型；本机已安装 CPU 推理环境并缓存权重，分析在播放前完成，不占用实时音频线程。

**首轮曾导入用户提供的 23 首 FLAC，完成混音，并收到真实评分：01=A、02=B、03=B。** 第一场 4 首、5 分 33 秒及 3 组 A/B 位于 `outputs/weeknd-first-listen-20260924/listening`。03 已按“后曲开唱前，前曲人声退出”的意见另存修订版，用户确认“新版更好”，见 [人声交接实验](docs/vocal-handoff.md)。历史官方音源核查见 [docs/music-sources.md](docs/music-sources.md)。

**试听评分：** 双击 `Start-Feedback.cmd`，可直接用按钮评价选曲搭配、切入点、混音衔接、人声交接和整体偏好，再点“保存反馈”。数据写入本地，刷新恢复；四条既有选择保留，未评价的细项为空。

**模型验收：** 双击 `Review-Model.cmd`。三段参考视频已整理成 61 条弱曲序示范，42 首标题身份；模型、JSONL、划分和评测存于 `outputs/reference-choice-20260924-v1`。这是选曲实验模型，未自动启用；详见 [参考数据与模型](docs/reference-data.md)。

## 开发与复现

依赖锁定在 `uv.lock`，当前支持 Windows x64 / Python 3.11–3.12。已验证环境为 Python 3.11.15、FFmpeg 8.1（带 Rubber Band）、PyTorch 2.8 CPU。

```powershell
uv sync --locked --extra dev --extra midi --extra beat
.venv\Scripts\python.exe -m dj_agent doctor --output outputs\environment.json
.venv\Scripts\python.exe -m pytest -q
node integrations\mixxx\bridge_harness.js
```

自定义时长：

```powershell
.\scripts\run_dj.ps1 -MusicFolder 'D:\DJ_agent\weeknd' -Minutes 25
```

比较两首歌的同一过渡：

```powershell
.venv\Scripts\python.exe -m dj_agent compare 'D:\music\song-a.flac' 'D:\music\song-b.flac' --minutes 4 --output outputs\comparison-001 --open
.venv\Scripts\python.exe -m dj_agent feedback outputs\comparison-001 transition-01 B
```

标签支持 `A`、`B`、`tie`（接近）、`neither`（都不好）。反馈只保存显式的人类选择。

训练入口已就绪：

```powershell
.venv\Scripts\python.exe -m dj_agent train --check-only
.venv\Scripts\python.exe -m dj_agent train
```

它训练本地 CPU 小型转场偏好排序器，先审计真实评分与歌曲隔离，再拟合和评测。首轮曾收到 3 条 A/B 评分，另有 1 条单独保存的人声修订偏好；比较共享歌曲，且修订反馈未接入当前训练格式，不满足独立训练/测试门槛，没有生成个人偏好权重。歌曲身份表、实验门槛与操作说明见 [训练文档](docs/training.md)。

工程测试（不是真实音乐）：

```powershell
.venv\Scripts\python.exe scripts\smoke_test.py
```

该脚本生成明确标为 SYNTHETIC 的节奏信号，经过真实模型分析、变速、混音、24-bit WAV 导出、再次解码检测，生成 A/B 报告。它用于验证程序链路，不能证明真人音乐听感。

## 已实现

- 文件指纹缓存、损坏/静音音频拒绝、预训练节拍检测、调性/能量/候选段落分析。
- 不重复选曲、能量走势、速度上限、实际节拍窗口对齐和后续速度修正。
- 节拍不确定时保守短过渡；主要混音模板为低频交接和普通淡化。
- 保持音高的变速、立体声渲染、输出余量、完整录音和转场上下文片段。
- A/B 试听、导出文件重新解码后的峰值/静音检查、偏好记录和按歌曲隔离评测数据。
- 随机 A/B、试听文件与标签绑定、重复/冲突反馈审计、小型偏好模型训练与独立评测入口；实验权重不自动用于播放。
- 带结构化按钮的本地反馈服务，版本冲突检查、跨进程保存锁和追加式历史；最新整体偏好接入原训练审计。
- 参考视频曲序 JSONL / chat messages 导出；CPU 选曲模型训练、按视频留出并清除重复曲对、离线交互验收页。
- 可选预训练 Demucs 本地人声分离及整场离线人声交接，支持前曲高通、半拍回声与短混响软件效果。所有效果均有实际参数与输出时间记录。试听页同一时间只播放一个版本。
- 官方 Mixxx 2.5.6 项目内隔离安装、受限 MIDI 命令桥、人工接管逻辑、有序 M3U8 输出。

## 尚未验收或尚未实现

- **真实歌曲听感：已收到首轮评价，03 人声修订版获用户偏好。** 尚无专业 DJ 水平或“效果最好”的证据。
- **DDJ-200、MIDI 回环、音频输出：缺少已连接设备/端口。** Mixxx 进程启动和协议模拟已执行，不能代替实机。
- Mixxx 稳定版不能通过当前桥接按任意路径动态装载歌曲；多首播放目前需要一次手动导入 M3U8 到 Auto DJ。其转场属于 Mixxx 内置算法，与离线增强渲染不同。
- 离线 EQ、滤波、回声与短混响已用于试听样例；自动循环、复杂搓碟、完整实时 Agent 执行器尚未交付。
- 整场规划的人声密度仍是频谱代理，段落是候选边界，调性是全曲估计；可选分离实验用持续能量估计开唱点，不能保证歌词识别或完全消除人声残留。
- 尚未连接 GLM/Open-Jev、训练审美模型或采集现场气氛。参考曲序模型只学习下一首选择，不能代替真实曲目 A/B 听评。

## 项目文档

- [设计方案](docs/design.md)
- [实施计划](docs/superpowers/plans/2026-09-23-dj-agent.md)
- [实施状态与测试证据](docs/status.md)
- [Mixxx 与 DDJ-200 接入](docs/mixxx.md)
- [训练数据与反馈](docs/training.md)
- [参考视频数据与选曲模型](docs/reference-data.md)
- [真实歌曲来源](docs/music-sources.md)

音频、模型、工具与输出均忽略于 Git。代码保留本地开发分支；试听网页与 MP3 已按用户要求部署云端，原始 FLAC 和无损 WAV 保留本地。
