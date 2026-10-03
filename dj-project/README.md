# Lofi × The Weeknd DJ 项目

Agent 自主 DJ 混音系统：从曲目分析、编排决策到技巧渲染的全流水线，以及配套的线上播放页。

> 本仓库为私有存档：不含任何歌曲音频/原文件，仅代码、数据结构与页面。

## 目录结构

```
dj-project/
├── miaomi-dj/              # 核心 DJ Agent（Python 包，Mixxx 集成）
│   ├── source/             # 完整源码 (src/dj_agent/ 30+ 模块)
│   │   ├── planner.py      # 编排决策（选曲/接歌/效果规划）
│   │   ├── transition_fx.py# 切歌技巧 DSP
│   │   ├── mixxx.py        # Mixxx 运行时控制
│   │   ├── render.py       # 音频渲染
│   │   ├── analysis.py     # BPM/Key/Energy 分析
│   │   └── ...             # mood/stems/feedback/evaluation 等
│   └── releases/           # 历史版本播放页 (v3/v4 index.html + 封面)
├── lofi-engine/            # Lofi Night Shift 串烧引擎（Hermis 实现）
│   ├── agent_dj.py         # Agent DJ：每个切点决策技巧并真实渲染
│   │                       #   磁带刹停/回声抽离/高通滑入/长板交叉
│   ├── build_set.py        # v1 编排：能量缓降 + 12s 三角交叉
│   ├── feats.json          # 25 曲特征（librosa 提取）
│   ├── timeline*.json      # 四版串烧时间轴（v1 缓降/v2 技巧/v3 精华/v4 飞行）
│   ├── build_v3.py         # v3 精华 15min 节奏型编排
│   ├── build_v4.py         # v4 长途飞行 89min 三幕编排
│   └── render_v*.sh        # 渲染脚本（wav 中间层 + acrossfade 链）
├── auret-legacy/           # Auret 第一代渲染脚本 + 历史页面
│   ├── make_set.py         # set17 基础版
│   ├── model_set.py/v2     # 模型选择版（acoustic/choice/cue）
│   └── *.html              # 第一版/当前版播放页
deploy/                     # 线上页面源码
├── sleep-dj.html           # Night Shift 调音台版（DDJ-200 风格监控）
└── lofi-room.html          # Lofi House 双房间播放页
docs/architecture/          # 架构图（Archify）
└── .archify/               # AI DJ 参考架构（GPT 方案三层次）
presets/                    # DSP 参数（lofi-v2-sleep.txt）
```

## 架构

三层：**决策层**（DJ Agent：personality/memory/planning）→ **执行层**（Execution Engine → Mixxx → DDJ-200）→ **感知层**（ONNX/DSP：BPM/Key/Energy → phrase/mood/stems）。

详见 `docs/architecture/` 的交互图。

## 技巧体系（agent_dj.py）

| 技巧 | 触发条件 | 实现 |
|---|---|---|
| 磁带刹停 tape_stop | 能量骤降 ≥20% | atempo 0.72² 降速 + afade |
| 回声抽离 echo_out | 远调过渡（调性距离≥2） | aecho 反馈衰减 + highpass |
| 高通滑入 filter_build | 同调/近调连锁 | 220Hz highpass 渐开 |
| 长板交叉 blend | 后半夜（3AM+） | acrossfade 10s 慢板 |
| 循环蓄势 loop_roll | 高能进高峰 | 2 拍 loop ×4 递进 |

## 播放页（线上）

- Lofi Night Shift 双 tab：NIGHT SHIFT（技巧版 53:50）+ FLIGHT（长途飞行版 89:15），调音台监控（DECK A/B LED VU + 交叉推子 + 接歌理由 + SET 包络）
- Lofi House：黑胶房/助眠房双房间
- Weeknd DJ 串烧：v1/v4 双版本

## 依赖

- Python 3.11+，librosa/numpy/scipy/soundfile
- 可选：demucs（分轨）、beat-this（节拍）、mido/python-rtmidi（DDJ-200 MIDI）
- ffmpeg（渲染）

## 版本谱系

| 版本 | 时长 | 编排哲学 |
|---|---|---|
| v1 | 55min | 能量缓降（睡眠向） |
| v2 | 54min | Agent 技巧决策（磁带刹停×4/回声抽离×9/高通滑入×6/长交叉×6） |
| v3 | 15min | 精华节奏型（蓄势-峰值-呼吸-二峰-落地） |
| v4 | 89min | 长途飞行三幕（起飞巡航→平流层→夜降，The Abyss 收尾） |


## 自反馈闭环 (auto-feedback, 2026-10)

按"What Makes a Good DJ Transition"框架 (docs/references/) 落地, 人标链原样保留:

- `transition_score.py` — 9 分量客观评分 (节拍对齐+漂移/和声/BPM折叠/能量弧/低频碰撞/人声冲突/段落边界/技巧匹配/QC) + §14 硬质量门
- `auto_feedback.py` — 客观分自动出 A/B 标签 (source=auto, 溯源完整, 分差小记 tie)
- `training.collect_auto_preferences` — 自动标签训练路径, 与人标永不混流, 另出 agreement_report
- `lookahead.py` — beam search (宽3深4) 整场曲序规划, 替代贪心选曲

CLI: `dj-agent auto-label <session> [--max-pairs N] [--dry-run]` / `dj-agent plan-lookahead <folder> --minutes N`

实测 (lofi 25 曲库取 8 首): lookahead 总奖励 3.18 vs 贪心 3.00 (**+6.0%**), 曲序更顺 (能量弧贴合)。

数据先验: djmix-dataset 5,040 真实 mix → 9,671 条带时间戳转场 (transitions.jsonl), 曲目级 BPM/key 分析后喂 reference_choice。

## v3 验收报告 (2026-10-03, tag v3)

| 项目 | 结果 |
|---|---|
| 单元测试 | **170 passed / 1 skipped** |
| 可复现性 | 同参数 3 次重跑 total_score 完全一致 (6.2463) |
| A/B 提升 | 贪心 2.997 → +先验校准 3.287 (+9.7%) → beam 3.397 (+13.4%) |
| 规模扩展 | 10曲每曲均值 0.625 / 25曲 0.634 (长程无衰减) |
| 音乐学达成 | 同圈+邻圈 75%(25曲) / 能量跳变 p50 0.006 (远优于先验 p75 0.108) |
| 已知偏差 | 半音内 46-56% vs 先验 97.6%: mismatch 线性罚 vs 半音阶跃形态差异; 听感影响=pitch 拉伸≤8%, 可接受 |

先验数据: 2,027 曲特征 / 427 对真实 DJ 转场 (transition_prior v3, 分位数带宽版)。
验收脚本: `qa-scripts/acceptance_v3.py`。
