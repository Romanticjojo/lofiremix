# LofiRemix 流水线详解

每一步的参数、原理和调参依据。DSP 路线全在本机跑，无外部服务依赖。

## 前置

| 依赖 | 版本验证 | 说明 |
|---|---|---|
| Python 3.11 + venv | `uv venv .venv --python 3.11` | demucs 官方支持 3.8-3.11，3.11 最稳 |
| demucs | 4.0.1 | `uv pip install demucs numpy`（numpy 必须显式装，torch 缺它报错） |
| ffmpeg | 8.0.1 (brew) | 后期主引擎 |
| macOS MPS | Apple Silicon | demucs 自动用 MPS，比 CPU 快 ~9x |

## [1] demucs 分轨

```bash
bin/separate.sh work/<song>
# 等价于:
demucs -n htdemucs --two-stems=vocals -o work/<song>/stems work/<song>/src.mp3
```

**参数理由：**
- `htdemucs`：Meta 官方混合 Transformer 模型（v4 旗舰），对人声/伴奏分离质量最好之一
- `--two-stems=vocals`：只要 vocals / no_vocals 两轨。四轨（drums/bass/other）对 lofi
  改编没有必要——lofi 处理里鼓和贝斯走同一条"变暗+降速"链，拆开反而增加回混失真
- 输出 44.1kHz 16bit WAV（demucs 默认），正好匹配后续 `asetrate=44100*x` 链

**性能实测**（M2 Max 级 MacBook，3:32 歌）：
分轨 ~15s，内存峰值 ~2.5GB。

**已知坑：**
- torch 没有 numpy 时会 Warning 但能跑；装上避免诡异崩溃
- 首次运行自动从 HF Hub 下载 htdemucs 权重（~80MB），需网络
- 输出目录结构固定为 `stems/htdemucs/src/{vocals,no_vocals}.wav`，finish.sh 依赖此路径

## [2] lofi 化（bin/finish.sh）

### 2.1 变速变调（33rpm 转盘感）

```
asetrate=44100*0.92,aresample=44100
```

把采样率视为 44100×0.92 再重采样回 44100：速度 ×0.92，音调同步降 ~1.5 半音，
时长 ×1.087（3:32 → 3:50）。这是"33rpm 唱机播 45rpm 母带"的经典效果。
**不要用 atempo**——atempo 保调变速，lofi 要的恰恰是调也降。

### 2.2 分轨音色

| 轨 | 滤波 | 理由 |
|---|---|---|
| vocals | `lowpass=f=8000` | 人声气声裁掉，"收音机/磁带"感但不糊词 |
| vocals | `acompressor=threshold=-18dB:ratio=2.5:attack=8:release=120` | 收动态，模拟广播压缩 |
| no_vocals | `lowpass=f=7000` | 伴奏可以比人声更暗 |
| no_vocals | `equalizer=f=120:w=1:g=3` | 低频隆起=温暖/木质感 |
| no_vocals | `equalizer=f=3000:w=1.4:g=-2` | 3k 是"塑料感"频段，收掉 |

### 2.3 磁带抖动（wow & flutter 近似）

```
tremolo=f=0.8:d=0.12
```

0.8Hz 深度 12% 的缓慢振幅调制，模拟磁带走带不匀。超过 0.2 深度会晕船。

### 2.4 黑胶底噪床

```
anoisesrc=color=pink:amplitude=0.045 → highpass 200 + lowpass 7000 → 混入 0.55
```

粉噪（能量随频率递减）+ 带通到中频，贴着黑胶表面噪声的频谱形状。
`amplitude` 每加 0.025 听感翻一倍，0.045 是"听得见但不抢戏"档。

### 2.5 母带

```
lowpass 9500 + highpass 55 + 200Hz +1.5dB + alimiter 0.9 + loudnorm I=-16 + 尾部 6s fadeout
```

- 目标 **−16 LUFS**（比流媒流行歌的 −14 轻，lofi 审美是"背景感"）
- `alimiter=limit=0.9` 在 loudnorm 前防回混叠波

## [3] 质检（每首成品必过）

```bash
# 响度（目标 −16±0.5 LUFS）
ffmpeg -i out/x.wav -af ebur128 -f null - 2>&1 | grep -A2 "Integrated"
# 频谱质心（应比源曲低 20%+）
python3 -c "import librosa;y,sr=librosa.load('out/x.wav');print(librosa.feature.spectral_centroid(y=y,sr=sr).mean())"
```

Less Than Zero 实测：1847→1397 Hz（−24%），−15.7 LUFS。

## 调参档案 presets/lofi.txt

预设值即 finish.sh 内置默认。扩展方向：
- **45rpm 档**：×0.96 更轻快，低频少抬
- **warble 档**：tremolo 1.6Hz d=0.18（卡带受损感）
- **rainy 档**：vinyl 床换 brown 噪声 + 更高电平

## 神经路线（ACE-Step，未接入）

- 仓库：ACE-Step（3.5B 音乐生成模型），cover 模式输入源曲 + 风格 tag
- 优势：能改配器质感（钢琴换电钢、加磁带钢琴采样），DSP 做不到
- 劣势：3.5B 模型 Mac CPU 不可用；GPU 单曲 ~1-2min；人声可能被重造（版权/听感风险）
- 接入位：`bin/cover.sh`（占位），与 finish.sh 串联或并联二选一
