# LofiRemix — Lofi 流水线 × DJ Agent

两条产线，同一仓库：**lofiremix 流水线**（歌曲 → lofi 版本）与 **DJ Agent**（自主编排 + 混音决策，主力产线）。

## DJ Agent（主要项目）

Agent 自主 DJ 混音系统：曲库分析 → 数据先验 → 编排决策 → 技巧渲染 → 线上播放页，全链闭环。详见 [`dj-project/README.md`](dj-project/README.md)。

### 核心能力

- **真实 DJ 数据先验**：djmix-dataset 5,040 场真实 mix → 9,671 条带时间戳转场；三轮采样 2,027 首、427 对可评转场拟合出 `transition_prior v3`（分位数带宽：BPM p75=0.070 半音、97.6% 转场半音内；严格同圈 27.1%；能量跳变 p50 0.060）
- **先验校准评分**：planner 权重不拍脑袋，全部由先验数据定标（BPM 匹配 8 / 和声软惩罚 0.3 / 能量弧 1.6）
- **lookahead beam 规划**：宽度 3 深度 4 整场曲序规划，替代贪心——A/B 实测 **+13.4%**（贪心 2.997 → beam 3.397），同参数重跑完全可复现
- **自反馈闭环**：9 分量客观评分器（节拍对齐/和声/BPM 折叠/能量弧/低频碰撞/人声冲突…）自动出 A/B 标签，与人标永不混流，纯 DSP 无 LLM 裁判、可复现审计
- **Agent 技巧引擎**：磁带刹停 / 回声抽离 / 高通滑入 / 循环蓄势 / 长板交叉，ffmpeg 真实渲染非 UI 演示
- **v3 验收**：170 passed / 1 skipped；25 曲 79min 长程规划每曲均值不衰减（0.634）

### 目录速览

```
dj-project/
├── miaomi-dj/source/src/dj_agent/   # 核心 Python 包 (planner/lookahead/transition_score/auto_feedback/...)
├── lofi-engine/                     # 串烧引擎 (agent_dj.py 技巧决策 + build_v1..v4)
├── auret-legacy/                    # 第一代渲染脚本
└── qa-scripts/                      # Playwright 回归 + v3 验收脚本
```

### 版本谱系（DJ Agent 主线）

| 版本 | 里程碑 |
|---|---|
| v1–v4 | 串烧引擎：能量缓降 → Agent 技巧决策 → 精华节奏型 → 89min 长途三幕 |
| 2026-09 | 自反馈闭环（Task 1–6，170 tests）；lookahead beam 上线（+6.0%） |
| **v3 (tag)** | **先验校准 + 验收发布**：三轮采样 2,027 曲 → 先验 v3 → 权重校准（+13.4%）→ 10 曲 demo + 79min 长版上线 |

---

## lofiremix 流水线（配套产线）

热门歌曲 → lofi 版本的自动化改编。两条路线：

- **DSP 路线（已跑通）**：demucs 分轨 + ffmpeg 黑胶化，经典派做法，
  一首 3-4 分钟的歌在 MacBook 上约 40 秒出成品。
- **神经路线（待接入）**：ACE-Step cover 模式风格迁移，需 GPU（5080 笔记本）。

```
输入歌曲 (mp3/m4a/flac/wav)
   │
   ▼
[1] demucs 分轨 ──→ vocals / no_vocals 两轨 (htdemucs, MPS 加速 ~15s)
   │
   ▼
[2] bin/finish.sh ──→ 分轨独立 lofi 化 + 回混 (~20s):
   │     vocals:    lowpass 8k + 压缩(-18dB/2.5:1) + 0.8Hz tremolo 磁带抖动
   │     no_vocals: lowpass 7k + 120Hz +3dB 暖化 + 3kHz -2dB
   │     全局:      0.92x 降速变调(33rpm) + 粉噪黑胶床 + limiter + −16 LUFS
   │
   ▼
[3] 部署 ──→ out/*.mp3 → 云服务器 + 播放页 (deploy/lofi-room.html)

神经路线 (备用): [2'] ACE-Step cover 模式整曲迁移 → [3] 同上
```

## 目录结构

```
lofiremix/
├── README.md            # 本文件
├── docs/
│   ├── PIPELINE.md      # 流程详解: 每步参数、原理、调参指南
│   └── DEPLOY.md        # 服务器部署: nginx 配置、播放页、URL 规范
├── bin/
│   ├── separate.sh      # demucs 分轨封装
│   └── finish.sh        # lofi 后期主脚本 (demucs 输出 → 成品)
├── presets/
│   └── lofi.txt         # 风格参数档 (33rpm 档)
├── deploy/
│   └── lofi-room.html   # 线上播放页模板 (token 占位符 __TOKEN__)
├── work/<song>/         # 每首歌工作目录: src + stems + 中间产物
└── out/                 # 最终成品
```

## 快速开始

```bash
# 0. 环境 (一次性)
uv venv .venv --python 3.11 && source .venv/bin/activate
uv pip install demucs numpy

# 1. 放入源曲
mkdir -p work/<song> && cp "/path/to/song.mp3" work/<song>/src.mp3

# 2. 分轨 (Mac MPS ~15s / 3.5min 歌)
bin/separate.sh work/<song>

# 3. lofi 化 + 回混 (~20s)
bin/finish.sh work/<song>

# 4. 部署件
ffmpeg -i out/<song>_lofi.wav -c:a libmp3lame -q:a 3 out/<song>_lofi.mp3
```

## 成品档案

| 歌 | 源 | 输出 | 关键指标 |
|---|---|---|---|
| Less Than Zero — The Weeknd | 3:32, 143.6 BPM | `out/less_than_zero_lofi.wav` 3:50 | 质心 1847→1397Hz, −15.7 LUFS |

线上试听：慢速黑胶房（隐藏 URL，见 `docs/DEPLOY.md`）

## 调参速查

| 想要的效果 | 改哪里 |
|---|---|
| 更慢更糊 | `bin/finish.sh` 里 `asetrate=44100*0.92` → `0.88` |
| 黑胶噪更重 | vinyl 床 `amplitude=0.045` → `0.07` |
| 高频更暗 | vocals lowpass `8000`→`6000`；伴奏 `7000`→`5500` |
| 磁带抖动更强 | tremolo `d=0.12` → `0.2` |
| 去掉变速只留音色 | 删 `asetrate=44100*0.92,aresample=44100` |

## 状态 / 路线图

- [x] 骨架搭建
- [x] demucs 本地跑通（MPS 加速，3:32 歌 15s 分完 vocals/no_vocals）
- [x] ffmpeg lofi 后期（0.92x 降速变调 / lowpass 7-8k / tremolo 抖动 / vinyl 粉噪床 / −16 LUFS）
- [x] 端到端样片：Less Than Zero → `out/less_than_zero_lofi.wav` (3:50)
- [x] 线上播放页（慢速黑胶房）
- [ ] ACE-Step 接入（5080 笔记本跑 cover 模式，神经风格迁移版）
- [ ] 批量模式：曲库整批过流水线
- [ ] 自动响度分档（人声主导 vs 伴奏主导歌用不同 preset）
