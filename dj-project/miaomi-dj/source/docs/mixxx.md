# Mixxx 2.5.6 本地接入

## 当前能力和边界

本项目使用 Mixxx 官方的 MIDI 控制器映射与 JavaScript `engine.getValue`、`engine.setValue`、`engine.getParameter`、`engine.setParameter` 接口。Mixxx 2.5 稳定版没有已确认的通用 REST/OSC 服务，也没有稳定的控制器脚本 `loadTrack(path)`。因此桥接只能装载 **Mixxx 当前高亮曲目到空闲甲板**；不能收到本地路径后动态定位歌曲。脚本会拒绝正在播放的甲板和已装载的甲板。

Python 的 `prepare_playlist(paths, output)` 可生成保留顺序的 M3U8，供 Mixxx 手动导入。输出文件必须不存在，函数拒绝覆盖歌曲源文件或已有播放列表。Mixxx 自带 Auto DJ 可以接管多首曲目的依次装载和淡化，但其算法与本项目增强版离线渲染不同，不能把它的播放结果当成离线混音的实机验收。

## 安装和隔离运行

在项目根目录运行：

```powershell
.\scripts\setup_mixxx.ps1 -Install -Diagnose
.\scripts\setup_mixxx.ps1 -Launch
```

脚本从 [Mixxx 官方下载站](https://mixxx.org/download/)取得 Windows 64 位稳定版 2.5.6 MSI，核对官方 SHA-256 `0d1f01a1f5c2e4d4180cd462e60365d0230b405808e7bd2b6625b62a53a29c72`，用 MSI administrative extraction 放在 `tools/mixxx`，不会执行全局安装。`-Launch` 指定 `--settingsPath tools/mixxx-settings`；首次启动会要求选择音乐库目录。这个目录、库数据库、日志、分析缓存均留在项目的忽略目录中，不改用户默认 Mixxx 设置。`-Diagnose` 只检查进程、文件和 PnP 名称，不代表声音或控制器测试通过。

也可用 `dj_agent.mixxx.launch_mixxx(executable, settings_dir, initial_tracks)` 在启动时由 Mixxx 官方命令行装载最多两首文件；先确保另一个 Mixxx 实例没有占用设置目录。`initial_tracks` 不适用于运行中的 Mixxx，也不能代替大于两首的曲序管理。

## 连接 MIDI 桥

桥接需要 **专用的双向 MIDI 回环端点**：Python 的输出进入 Mixxx，Mixxx 的输出回到 Python。项目不安装全局虚拟 MIDI 驱动。DDJ-200 是硬件控制器，不是 Python 与 Mixxx 之间的回环端点，也不应替代专用桥接端点。

1. 先准备可见于 Windows 的双向 MIDI 回环端点；不要在同一个输入端口上让 Python 和 Mixxx 争抢独占访问。
2. 运行 `-Install` 或 `-Launch`，把 `DJ Agent.midi.xml` 和 `dj-agent-bridge.js` 复制到隔离设置的 `controllers` 文件夹。
3. 在 Mixxx 的 **Preferences → Controllers** 中为专用端点启用 **DJ Agent bridge** 映射。DDJ-200 仍使用它自己的映射，不能选这个桥接映射。
4. 用 `.venv\Scripts\python.exe` 和项目 `midi` 可选依赖打开端点：

```python
from dj_agent.mixxx import MidoPort, MixxxBridge, diagnose

print(diagnose())
port = MidoPort(input_name="回环端点的 Mixxx 输出", output_name="回环端点的 Mixxx 输入")
bridge = MixxxBridge(port)
print(bridge.connect())  # 必须收到真实 HELLO 回执后才 ready
print(bridge.read(1, "track_loaded"))
```

`mido` 和 `python-rtmidi` 在项目的 `midi` optional dependencies 中。未收到桥接回执、端口断开或超时后，桥会转为未就绪；重连需重新创建端口或再次 `connect()`。通道 1、2 可调用 `load_selected`、`read`、`play`、`stop`、`level(0..1)`；主交叉推子调用 `crossfade(-1..1)`。`read` 只允许 `track_loaded`、`play`、`volume`、`crossfader`、`owner`，每条写入都由 Mixxx 脚本回读或明确拒绝。回执不保证扬声器输出成功，声音必须单独验收。

若希望人工接管，先调用 `bridge.take(deck)`，再触碰 DDJ-200 控件；`bridge.release(deck)` 才恢复 AI 写入。Auto DJ 正在运行时，`take` 会先关闭 Mixxx 的全局 Auto DJ 控件；只有回读确认为关闭后才返回 `MANUAL_BOTH`，两侧甲板都转为人工控制，分别 `release(1)`、`release(2)` 后才允许桥接重新写主交叉推子。若 Mixxx 拒绝关闭，接管会报错且不会声称成功。已开始的淡化和声音仍需现场确认。甲板的外部播放/音量变化及交叉推子变化也会触发自动接管。物理推子没有电动追随，恢复前应看清软件当前值，避免突变。`owner` 为 `AUTO`、`MANUAL`、`MIX`；`MIX` 表示 Mixxx Auto DJ 自己控制，桥接的直接推子/甲板写入被拒绝。

### 多首曲目：Mixxx Auto DJ

```python
from pathlib import Path
from dj_agent.mixxx import prepare_playlist

prepare_playlist([Path(r"D:\music\first.wav"), Path(r"D:\music\second.wav")], Path(r"D:\DJ_agent\outputs\set.m3u8"))
```

在隔离 Mixxx 中创建播放列表并使用 **Import playlist** 导入 M3U8，然后在该列表的菜单选择 **Add to Auto DJ**。确认顺序、甲板左右交叉推子分配、过渡模式与时间，关闭随机添加和 shuffle。队列准备好后调用 `bridge.autodj_enable()`，可用 `bridge.autodj_fade_now()` 触发下一次淡化；这两个回执仅确认 Mixxx 控件状态/触发，不证明队列有歌、具体歌曲路径或音频成功播放。歌曲标题、路径和主输出仍应在 Mixxx 界面及录音中核对。Auto DJ 自行掌管播放时钟，模型只提前制定计划。

## 私有线格式

每帧是 MIDI SysEx：`F0 7D 44 4A 01` + 可打印 ASCII + `F7`，最多 96 个 ASCII 字节。Mixxx 2.5 将这类输入送到脚本对象的 `incomingData(data, length)`；不能依赖 XML `<key>` 指定的普通按钮回调。命令正文 `DJ1|seq|OP|deck|value`，`seq` 在 1..2147483647 循环。回执为 `DJ1|seq|OK|value` 或 `DJ1|seq|ERR|reason`。`7D` 是非商业/教育用途的 SysEx ID，仅用于这对专用端点，不发送到 DDJ-200。错误或旧序号的回执被忽略。支持 `HELLO`、`READ`、`LOAD_SELECTED`、`PLAY`、`STOP`、`LEVEL`、`CROSSFADE`、`TAKE`、`RELEASE`、`AUTODJ_ENABLE`、`AUTODJ_FADE`。映射初始化时触发 Auto DJ 状态回调，状态未知时拒绝命令。装载在空甲板上请求后轮询 `track_loaded`，超时即报错；没有可靠的路径身份反馈。

## 设备与验收

本机 2026-09-23 的 `mido` 扫描：输入端点为空；输出端点仅有 Microsoft GS Wavetable Synth。PnP 未检测到 DDJ-200。官方 Mixxx 2.5.6 已通过 SHA-256 校验、在 `tools/mixxx` 解包，隔离进程到达首次运行的“选择音乐库目录”窗口；当时未配置库目录、MIDI 映射或音频设备。因此协议模拟测试与进程启动证据不能视为真实 MIDI 回读、DDJ 操作、声音输出或完整混音验收。

DDJ-200 连接后应单独检查：USB/PnP 出现、Mixxx 自带 DDJ-200 映射启用、两个甲板操作和软件值回读、人工接管与恢复、音频设备主输出和耳机监听。DDJ-200 没有电动推子，也没有内置音频接口；声卡/分线方案需按实际设备设置。

官方参考：[2.5.6 下载与校验](https://mixxx.org/download/)、[控制器脚本](https://github.com/mixxxdj/mixxx/wiki/midi-scripting)、[Mixxx 控件](https://manual.mixxx.org/2.5/en/chapters/appendix/mixxx_controls)、[命令行选项](https://manual.mixxx.org/2.5/en/chapters/appendix.html#launching-mixxx-from-the-command-line)、[播放列表与 Auto DJ](https://manual.mixxx.org/2.5/en/chapters/library)、[DDJ-200 手册](https://manual.mixxx.org/2.5/en/hardware/controllers/pioneer_ddj_200.html)。
