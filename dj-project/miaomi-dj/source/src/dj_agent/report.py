"""Portable, escaped HTML report; music stays on the local machine."""
import html
from pathlib import Path


def write_report(plan: dict, metrics: dict, output_dir: Path, variant: str, clips: list) -> Path:
    def esc(value):
        return html.escape(str(value), quote=True)
    rows = "".join(f'<tr><td>{i+1}</td><td>{esc(t["title"])}</td><td>{esc(t.get("artist", ""))}</td>'
                   f'<td>{t.get("bpm", 0):.1f}</td><td>{t["source_start"]:.1f}–{t["source_end"]:.1f}s</td>'
                   f'<td>{t["rate"]:.3f}×</td></tr>' for i, t in enumerate(plan["tracks"]))
    segments = "".join(f'<article><h3>{esc(c["id"])}</h3><p>{esc(c["description"])}</p>'
                       f'<audio controls preload="none" src="{esc(c["path"])}"></audio></article>' for c in clips)
    warnings = "".join(f"<li>{esc(w)}</li>" for w in plan.get("warnings", []))
    content = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>DJ Agent · 混音报告</title>
<style>body{{font:16px/1.7 system-ui,sans-serif;background:#101419;color:#e5edf4;margin:0;padding:28px}}
main{{max-width:1050px;margin:auto}}h1{{font-size:32px}}.muted{{color:#abb8c7}}article,.card{{background:#1b232e;padding:20px;border-radius:12px;margin:16px 0}}
audio{{width:100%}}table{{width:100%;border-collapse:collapse}}td,th{{text-align:left;padding:10px;border-bottom:1px solid #344250}}
a{{color:#75d4c2}}.scroll{{overflow:auto}}code{{overflow-wrap:anywhere}}</style>
<main><p class="muted">喵秘 DJ / 本地混音实验</p><h1>本场混音与试听记录</h1>
<p>版本：{esc(variant)} · {metrics['duration_seconds']/60:.1f} 分钟 · {len(plan['tracks'])} 首</p>
<p>音源标记：<strong>{esc(plan.get('source_kind', 'unverified-local-audio'))}</strong>。技术检查与真实听感评价分开记录。</p>
<div class="card"><h2>完整录音</h2><audio controls preload="metadata" src="master.wav"></audio>
<p><a href="master.wav" download>保存 WAV</a> · <a href="plan.json">查看计划</a> · <a href="metrics.json">查看检测结果</a></p></div>
<div class="card"><h2>客观检查</h2><p>峰值 {metrics['sample_peak_dbfs']:.1f} dBFS · 估计真峰值 {metrics['estimated_true_peak_dbfs']:.1f} dBFS
 · 削波样本 {metrics['clipped_samples']} · 最长低于 −60 dBFS 区间 {metrics['longest_silence_seconds']:.2f} 秒</p>
<p class="muted">人工听评：尚未完成。峰值与静音检查不能证明音乐衔接好听。</p></div>
<div class="card scroll"><h2>曲目顺序</h2><table><tr><th>#</th><th>歌曲</th><th>艺人</th><th>BPM</th><th>源区间</th><th>速度</th></tr>{rows}</table></div>
<h2>转场片段</h2>{segments}<div class="card"><h2>分析限制</h2><ul>{warnings}</ul></div>
</main></html>'''
    destination = output_dir / "report.html"
    destination.write_text(content, encoding="utf-8")
    return destination
