# The Weeknd · DJ 试听室云端部署

2026-09-25；独立随机网址的静态试听室。页面暂用中性名称，永久品牌待用户选定。

## v4 交付

- 目录：`outputs/weeknd-20min-20260925-v4`；15 首、1178.935292 秒（19:38.94），48 kHz 双声道 PCM 24-bit WAV 和 256 kbps MP3。
- 曲序：How Do I Make You Love Me? → Save Your Tears → Sacrifice → Cry For Me → Dancing In The Flames → Wake Me Up → Creepin' → Love Me Harder → Niagara Falls → Reminder → Blinding Lights → Ordinary Life → True Colors → Die For You → Less Than Zero。
- 排除《A Lonely Night》《Earned It》；两首新增歌曲已加入。新版曲库共 25 首，排除后恰有 15 首节拍置信度为 high，不使用低置信节拍曲或重复曲目凑数。
- 单个片段约 86–96 秒，包含与相邻歌曲的重叠；整场平均每首占时约 79 秒。14 次过渡、10 次前曲人声让位，实际写入 7 次滤波、5 次回声、2 次短混响。
- 从导出 WAV 重新解码检测：削波 0，估计真峰值 −4.43 dBFS；低于 −60 dBFS 的最长 20 ms 窗口连续段为 0.52 秒。人声分离可能残留，人类听感验收尚待进行。
- 页面显示实际采样时序、真实波形、15 首与逐次转场跳转、7 张封面、实际控制回放与 MP3 下载；手机曲名字号、触控区域和底部播放器已调整，播放位置按音频哈希保存。
- v3（10 首、1197.965104 秒）仍保留作回滚；源 FLAC 和所有本地无损导出保留。

## 标签与模型边界

`resources/weeknd-moods.json` 覆盖 25 首。标签标记具体评论与专辑背景推断，保留来源；气氛强度始终是编辑经验值，不是假造的真人训练标签或观众反馈。新增两首的标签属于背景推断。没有现场观众感知。

v4 音频使用已存在的 `outputs/reference-parameters-20260925-v1/candidate-model.json`：上下文 1 首、L2 0.2、1806 参数。先前按参考视频划分的外层 Top 3 为 16.79%，原参数 14.23%；三轮选参不同、MRR 几乎不变，未证明最佳。新参考音频训练结果独立评估，未将尚未验证的新权重写入本次音频。不能把 v4 的听感归因于参考模型改进。

DDJ-200 参考官方 [产品页](https://www.pioneerdj.com/en/product/dj-controllers/ddj-200/) 与 [硬件图](https://www.pioneerdj.com/-/media/pioneerdj/software-info/controller/ddj-200/ddj-200_hardwarediagram_rekordboxdj_e1.pdf)。本页为录音参数回放；滤波、回声和短混响是软件实现，不是硬件接入或原厂 DSP 仿真。实时 Mixxx 留待后续。

## 云端布局与升级

- 地址保持不变，位于 romanticjojo.com 与 www 的既有 HTTPS 随机路径；完整地址在 `outputs/deployment-20260925-v4/release.json`。路径不等于身份认证。
- 当前发布目录 `/var/www/miaomi-dj/releases/20260925-v4`；`/var/www/miaomi-dj/current` 指向当前版本。
- 公开只上传 HTML、7 张封面和 MP3，共 9 文件；不上传源 FLAC、WAV、训练音频。
- 代码/计划/标签/已用模型备份在 `/var/lib/miaomi-dj/20260925-v4/project.tar.gz`，不映射为网站；回滚目标记录在同目录 `rollback.json`。
- 本次只原子替换 DJ 的 `current` 符号链接，未修改 nginx、Syrinx/Alnazar 路由或主页，也无需 reload。
- `scripts/upgrade_static_sample.py` 校验当前目标与部署前主页/配置哈希，按精确文件白名单解包并核对大小和 SHA-256，保存回滚记录，再原子切换。版本目录不可覆盖。
- 初次 v3 安装证据仍在 `outputs/deployment-20260925/`；其中 `install.py` 只用于初次安装，不用于升级。
- v4 部署证据：`outputs/deployment-20260925-v4/public-manifest.json`、`deployment-result.json`、`public-verification.json`。apex 已通过 HTML 哈希、7 张封面和 MP3 首/中/尾 Range 206。www 被 ESA 命中旧 v3（10 首、TTL 30 天），添加查询参数也不绕过；控制台需重新登录，当前无可用 API/CLI。仅 www 的本 DJ 随机目录待获有效会话后刷新，未擅改全站缓存规则。
- 部署始终使用显式 SSH 参数 `BatchMode=yes`、`StrictHostKeyChecking=yes`、`UserKnownHostsFile=C:/Users/54219/.ssh/known_hosts`，覆盖旧主机配置里的宽松默认值。

## 复现

```powershell
.venv\Scripts\python.exe scripts\build_visual_sample.py --library outputs\weeknd-library-20260925\library.json --model outputs\reference-parameters-20260925-v1\candidate-model.json --output outputs\new-session --exclude 'A Lonely Night' --exclude 'Earned It' --tracks 15 --minutes 20 --moods resources\weeknd-moods.json --effects
```

新输出必须使用未存在目录。`artwork.json` 按 track id 关联 `covers/` 图片，准备完成后调用 `sample_visualization.export_visualization()`。`Start-20-Minute-Sample.cmd` 启动已有 v4，不重复渲染。

版本回滚只需在校验当前目标为 v4 后，创建一个指向 `/var/www/miaomi-dj/releases/20260925-v3` 的临时符号链接并用 `os.replace` 替换 DJ 的 `current`。不回滚 nginx 配置，不操作 Syrinx 发布入口，不删除任何版本。

## 本轮浏览器验收

桌面 Chrome 与 390×844 手机布局已核对；无横向溢出。原 apex 网址实际点击 Creepin' 跳转到 489.4 秒，播放推进至 502 秒、readyState=4；暂停并刷新后恢复到约 504.2 秒，保持暂停。Chrome 未捕获脚本错误或警告。应用内浏览器在操作原生音频静音控件时崩溃，未将其计为成功播放；播放结论来自 Chrome。
