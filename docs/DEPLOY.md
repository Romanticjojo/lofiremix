# LofiRemix 部署说明

成品音频 + 播放页部署在 romanticjojo.com（阿里云 $DJ_SERVER_IP）。

## 布局

```
服务器 /var/www/syrinx/
├── lofi-room-<token>.html        # 播放页 (deploy/lofi-room.html 渲染后)
├── lofi-<song>-<token>.mp3       # 成品音频 (每首一个独立 token 文件名)
└── covers-au/lofi-<song>.jpg     # 封面 (复用 DJ 页封面目录)
```

nginx 站点：`/etc/nginx/sites-enabled/romanticjojo`
每首歌两个 location（页面 + 音频），模板：

```nginx
location = /vl-<token> {
    add_header Cache-Control "no-cache";
    add_header X-Robots-Tag "noindex, nofollow";
    try_files /lofi-room-<token>.html =404;
}
location = /lofi-<song>-<token>.mp3 {
    add_header Cache-Control "no-cache";
    add_header Accept-Ranges bytes;    # 移动端 seek 必须
    try_files /lofi-<song>-<token>.mp3 =404;
}
```

## URL 规范（保密）

- 页面：`https://romanticjojo.com/vl-<12位hex>`（vl = lofi 房；v 前缀家族：vx 看板/vf 费用/va DJ 房）
- 音频：文件名自带 token，**页面和音频 token 保持一致**（`vl-abc123` ↔ `lofi-ltz-abc123.mp3`）
- 全部 noindex + nofollow + no-cache
- token = `openssl rand -hex 6`

## 添加新歌（完整操作序）

```bash
# 1. 本地出成品
cd ~/LLM_work/lofiremix && source .venv/bin/activate
bin/separate.sh work/<song> && bin/finish.sh work/<song>
ffmpeg -i out/<song>_lofi.wav -c:a libmp3lame -q:a 3 out/<song>_lofi.mp3

# 2. token + 上传
T=$(openssl rand -hex 6)
scp out/<song>_lofi.mp3 $DJ_SERVER (见本地 ~/.ssh/config):/var/www/syrinx/lofi-<song>-$T.mp3
scp cover.jpg $DJ_SERVER (见本地 ~/.ssh/config):/var/www/syrinx/covers-au/lofi-<song>.jpg

# 3. 播放页加曲目
# deploy/lofi-room.html 的 TRACKS 数组加一项 (src 带新 token)
sed '' s/__TOKEN__/$T/g deploy/lofi-room.html > /tmp/room.html
scp /tmp/room.html $DJ_SERVER (见本地 ~/.ssh/config):/var/www/syrinx/lofi-room-$T.html

# 4. nginx 加两个 location → nginx -t && systemctl reload nginx
# 5. 验证: curl 页面 200 + mp3 Range 206
```

## 坑位记录

- **ESA 边缘缓存**：同 URL 换内容会被旧缓存坑（mp3 尤其），**每次更新换文件名/token**，
  不要原地覆盖。旧版直接换 v2/v3 后缀也行（DJ 页踩过：源站已新、边缘 31 分钟旧）
- **Accept-Ranges bytes 必须显式加**：不加的话部分安卓机 audio seek 起手要下全文件
- **播放页 JS 的安卓坑**（从 DJ 页迁移的修复）：
  - `preload="none"` 下切歌必须 `removeAttribute("src") + load()` 重置状态机再赋新 src
  - src 带 media fragment（`#t=秒`）让浏览器直接发对应 Range 请求
  - timeupdate 改 DOM 要节流（≥250ms），否则手机端 UI 抖动
- 音频绝对路径引用（`/lofi-xxx.mp3`），不要相对路径——SPA 兜底会喂 HTML 给 audio 解码器

## 播放页（deploy/lofi-room.html）

- 单文件无依赖，深色 + 琥珀色 lofi 视觉
- TRACKS 数组驱动：加歌只需加一项 {name, artist, src, cover, dur, tags}
- 吸底播放器 + 进度条拖拽 + 队列标签（处理参数展示）
- 移动端适配（safe-area / 触摸高亮 / 2x 图）
