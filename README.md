# LofiRemix — lofi remix 生产流水线

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
