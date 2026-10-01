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
│   └── timeline*.json      # 两版串烧时间轴（含切点/技巧/理由）
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

- Lofi Night Shift：调音台监控（DECK A/B LED VU + 交叉推子 + 接歌理由 + SET 包络）
- Lofi House：黑胶房/助眠房双房间
- Weeknd DJ 串烧：v1/v4 双版本

## 依赖

- Python 3.11+，librosa/numpy/scipy/soundfile
- 可选：demucs（分轨）、beat-this（节拍）、mido/python-rtmidi（DDJ-200 MIDI）
- ffmpeg（渲染）
