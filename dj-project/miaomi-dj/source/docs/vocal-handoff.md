# 03 人声交接实验

2026-09-24，根据用户首轮评价制作。目标是保留《Sacrifice》→《Dancing In The Flames》的选曲和切入点，让第二首开唱时第一首的人声退出。

## 产物与边界

目录：`outputs/weeknd-first-listen-20260924/listening/revisions/transition-03-vocal-exit`。

- `review.html`：原版 B 和新版的本地试听页，同一页面互斥播放。
- `original.wav` / `revised.wav`：27.881 秒的原版与修订片段；原版与用户选中的 03B 完全一致。
- `outgoing-input.wav` / `incoming-input.wav`：保持原渲染增益与变速的输入片段。
- `outgoing-vocal.wav` / `incoming-vocal.wav`：分离模型的估计人声。
- `outgoing-vocal-contribution.wav` / `removed-vocal.wav`：按混音权重对齐的人声及实际减去的分量。
- `revision.json`：时间、哈希、导出后测量及限制。

这是离线修订实验。通用函数位于 `src/dj_agent/vocals.py`，尚未自动用于 `mix`、`compare` 或实时 Mixxx 播放。用户已确认“新版更好”，评价及原版/新版哈希另存 `human-feedback.jsonl`；此片段不替换原比较清单，新评价尚未接入训练。

## 实际处理

采用 [官方 Demucs](https://github.com/facebookresearch/demucs) 的 `htdemucs` 预训练权重，本地 CPU 推理。可选环境为：

```powershell
uv sync --locked --extra dev --extra midi --extra beat --extra stems
```

模型缓存到 `data/models/demucs`。本次下载和两段分离约 32.14 秒；这不是实时播放延迟指标。没有重训练、GPU 环境迁移或云端上传。

1. 根据原计划重新渲染完整的两首入选区间，保留原 RMS 增益、变速及采样数，再截取带上下文的分离窗口。原版 B 重建最大误差 1.19e-7。
2. 两首分别分离人声。48 kHz 输入转换为模型的 44.1 kHz，按官方实现归一化/逆变换后还原输入长度；不单独归一化人声。
3. 用后曲估计人声的 50 ms 能量帧寻找至少连续 200 ms 的活动，结果为转场开始后 4.60 秒，即试听片段 10.60 秒。它不是歌词识别，检测不到时不猜时间。
4. 前曲人声使用原 B 版的等功率淡出权重，并按离散采样位置对齐。片段 9.60–10.35 秒用平滑曲线移除该人声贡献，在估计后曲开唱前预留 0.25 秒。
5. 输出等于原混音减去待移除的人声贡献，原曲序、切入点和变速不变。伴奏可能受分离残留影响，不能保证主观听感。

离散对齐方式：每曲长度为 `round((source_end-source_start)/rate*48000)`；后曲起点为前曲起点加长度再减 `round(overlap*48000)`。试听窗口仍使用原渲染器的起止时间取整，不能直接以浮点时间代替累计采样位置。

## 已验证及下一步

完整 Python 测试 83 项通过。真实修订导出后测得削波 0，估计真峰值 −6.13 dBFS；改动前后边界外逐采样保持一致，原 6 段 A/B 哈希未变化。独立审查复算了人声贡献、入口时间和输出公式。

用户已实际对比并确认新版更好。下一步在更多独立转场验证，继续关注可辨歌词残留、伴奏变薄或分离伪影；本次偏好不能代替其他歌曲的用户听评。
