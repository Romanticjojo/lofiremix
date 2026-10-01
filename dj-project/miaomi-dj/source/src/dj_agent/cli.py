"""User-facing entry points; failed or missing inputs never become successful demos."""
import argparse
import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
import webbrowser
from datetime import UTC, datetime
from pathlib import Path

from .analysis import analyze, discover
from .planner import plan_set
from .render import render_set

PROJECT = Path(__file__).resolve().parents[2]


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def doctor():
    report = {"python": sys.version, "platform": platform.platform(),
              "ffmpeg_available": shutil.which("ffmpeg") is not None,
              "ffprobe_available": shutil.which("ffprobe") is not None,
              "real_weeknd_audio_test": "not_run", "hardware": {"ddj200_test": "not_run"},
              "weeknd_files": len(discover(PROJECT / "weeknd")) if (PROJECT / "weeknd").exists() else 0}
    versions = {}
    for name in ["numpy", "scipy", "librosa", "soundfile", "torch", "beat-this", "mido", "python-rtmidi"]:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not-installed"
    report["packages"] = versions
    if shutil.which("nvidia-smi"):
        result = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                                capture_output=True, text=True, timeout=20, check=False)
        report["hardware"]["gpu"] = result.stdout.strip() if result.returncode == 0 else "unavailable"
    try:
        from .mixxx import diagnose
        report["mixxx"] = diagnose()
    except (ImportError, RuntimeError, OSError) as error:
        report["mixxx"] = {"status": "not_ready", "detail": str(error)}
    return report


def _analyze_paths(paths, backend):
    tracks = []
    for index, path in enumerate(paths):
        print(f"分析 {index+1}/{len(paths)}：{path.name}", flush=True)
        tracks.append(analyze(path, PROJECT / "data" / "analysis", backend))
    return tracks


def _comparison_page(destination, manifest):
    import html
    rows = []
    for pair in manifest["pairs"]:
        pid = html.escape(pair["id"])
        candidates = pair.get("candidates", {})
        a = html.escape(candidates.get("A", {}).get("audio", f'baseline/{pair["id"]}.wav'), quote=True)
        b = html.escape(candidates.get("B", {}).get("audio", f'enhanced/{pair["id"]}.wav'), quote=True)
        rows.append(f'<section><h2>{pid}</h2><p>版本 A</p><audio controls preload="none" src="{a}"></audio>'
                    f'<p>版本 B</p><audio controls preload="none" src="{b}"></audio></section>')
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>DJ Agent · A/B 试听</title><style>body{background:#101419;color:#e5edf4;font:16px/1.8 system-ui;margin:32px auto;max-width:900px;padding:0 20px}section{background:#1b232e;padding:20px;margin:20px 0;border-radius:12px}audio{width:100%}a{color:#75d4c2}</style>
<h1>同一组歌曲，两种过渡</h1><p>请比较节拍、人声重叠、低频和整体自然度。两种版本使用同一源区间和曲序；技术指标不能替代您的听感。</p>'''
    page += ('<p>每组 A/B 顺序随机，请先听片段再选择。不同组的 A 不一定对应同一种处理方式。</p>'
             if manifest.get("schema_version") == 2 else '<p>旧版比较使用固定 A/B 顺序。</p>')
    page += f'<p><strong>音源标记：{html.escape(manifest.get("source_kind", "本地音频，艺人身份未自动验证"))}</strong></p>'
    page += "".join(rows)
    page += '<p>播放一个版本会自动暂停其他版本。</p>'
    page += '<p>可用偏好标签：A、B、tie（接近）、neither（都不好）。使用项目反馈命令保存选择；系统不会自动生成您的评价。</p>'
    page += '''<script>
document.addEventListener('play', (event) => {
  if (event.target.tagName !== 'AUDIO') return;
  for (const audio of document.querySelectorAll('audio')) {
    if (audio !== event.target) audio.pause();
  }
}, true);
</script></html>'''
    (destination / "comparison.html").write_text(page, encoding="utf-8")


def build_parser():
    parser = argparse.ArgumentParser(description="喵秘 DJ Agent：分析、自动混音与试听比较")
    commands = parser.add_subparsers(dest="command", required=True)
    diagnostic = commands.add_parser("doctor", help="检查当前环境和外部设备状态")
    diagnostic.add_argument("--output", type=Path)
    for name in ["analyze", "mix", "compare"]:
        p = commands.add_parser(name)
        if name == "compare":
            p.add_argument("song_a", type=Path)
            p.add_argument("song_b", type=Path)
        else:
            p.add_argument("folder", type=Path)
        p.add_argument("--backend", choices=["auto", "librosa", "beat-this"], default="auto")
        p.add_argument("--output", type=Path)
        if name != "analyze":
            p.add_argument("--minutes", type=float, default=25 if name == "mix" else 4)
            p.add_argument("--max-tracks", type=int, default=10)
            p.add_argument("--open", action="store_true", help="完成后打开本地试听页面")
    feedback = commands.add_parser("feedback")
    feedback.add_argument("session", type=Path)
    feedback.add_argument("pair_id")
    feedback.add_argument("preference", choices=["A", "B", "tie", "neither"])
    listen = commands.add_parser("listen", help="打开带结构化反馈按钮的本地试听页")
    listen.add_argument("session", type=Path)
    listen.add_argument("--port", type=int, default=0)
    listen.add_argument("--open", action="store_true")
    train = commands.add_parser("train", help="检查试听数据并训练实验性转场偏好模型")
    train.add_argument("--sessions", type=Path, default=PROJECT / "outputs")
    train.add_argument("--identity-map", type=Path, default=PROJECT / "data" / "song-identities.json")
    train.add_argument("--output", type=Path)
    train.add_argument("--check-only", action="store_true")
    reference = commands.add_parser("train-reference", help="训练参考视频曲序的实验性选曲模型")
    reference.add_argument("references", type=Path)
    reference.add_argument("--output", type=Path, required=True)
    reference.add_argument("--open", action="store_true")
    tuning = commands.add_parser("tune-reference", help="按视频嵌套验证参考选曲模型参数")
    tuning.add_argument("references", type=Path)
    tuning.add_argument("--output", type=Path, required=True)
    sample = commands.add_parser("sample-view", help="打开整场混音与切歌可视化")
    sample.add_argument("directory", type=Path)
    sample.add_argument("--port", type=int, default=0)
    sample.add_argument("--open", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "listen":
            from .listening_server import serve
            serve(args.session, args.port, args.open)
            return 0
        if args.command == "sample-view":
            from .sample_server import serve
            serve(args.directory, args.port, args.open)
            return 0
        if args.command == "doctor":
            report = doctor()
            if args.output:
                _write_json(args.output, report)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        if args.command == "feedback":
            from .feedback import record_preference
            print(record_preference(args.session, args.pair_id, args.preference))
            return 0
        if args.command == "train-reference":
            from .reference_experiment import run_reference_training
            report = run_reference_training(args.references, args.output)
            print(json.dumps({k: v for k, v in report.items() if k != 'evaluation'}, ensure_ascii=False, indent=2))
            print(f"模型验收页面：{args.output / 'acceptance.html'}")
            if args.open:
                webbrowser.open((args.output / 'acceptance.html').resolve().as_uri())
            return 0
        if args.command == "tune-reference":
            from .reference_tuning import run_parameter_study
            report = run_parameter_study(args.references, args.output)
            print(json.dumps({k: report[k] for k in ['candidate_config', 'selection_agreement',
                                                    'macro_by_video', 'elapsed_seconds']}, indent=2))
            print(f"参数测试报告：{args.output / 'summary.md'}")
            return 0
        if args.command == "train":
            from .training import run_training
            destination = args.output or PROJECT / "outputs" / datetime.now(UTC).strftime("training-%Y%m%d-%H%M%S-%f")
            report = run_training(args.sessions, args.identity_map, destination, args.check_only)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            print(f"训练检查报告：{destination / 'report.json'}")
            if report["status"] == "needs_data":
                print("尚未训练：缺少合格的真人试听数据或独立歌曲分组。", file=sys.stderr)
                return 0 if args.check_only else 2
            return 0
        paths = [args.song_a, args.song_b] if args.command == "compare" else discover(args.folder)
        if len(paths) < (1 if args.command == "analyze" else 2):
            raise ValueError("至少需要两首不同歌曲才能混音；请把实际音乐文件放入曲库。" if args.command != "analyze"
                             else "曲库为空，请先放入音频文件。")
        destination = args.output or PROJECT / "outputs" / datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        tracks = _analyze_paths(paths, args.backend)
        if args.command == "analyze":
            _write_json(destination / "library.json", [t.to_dict() for t in tracks])
            print(f"分析完成：{destination / 'library.json'}")
            return 0
        plan = plan_set(tracks, args.max_tracks, args.minutes)
        plan["warnings"].extend(sorted({warning for track in tracks for warning in track.warnings}))
        print(f"计划 {len(plan['tracks'])} 首，预计 {plan['duration_seconds']/60:.1f} 分钟。", flush=True)
        for warning in plan["warnings"]:
            print(f"提示：{warning}")
        if args.command == "mix":
            result = render_set(plan, destination)
            report_path = Path(result["report_path"])
        else:
            render_set(plan, destination / "baseline", "baseline")
            render_set(plan, destination / "enhanced", "enhanced")
            from .preferences import build_comparison
            manifest = build_comparison(destination, plan)
            _write_json(destination / "comparison.json", manifest)
            _comparison_page(destination, manifest)
            report_path = destination / "comparison.html"
        print(f"完成，试听报告：{report_path}")
        if args.open:
            if args.command == "compare":
                from .listening_server import serve
                serve(destination, open_browser=True)
            else:
                webbrowser.open(report_path.resolve().as_uri())
        return 0
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f"未完成：{error}", file=sys.stderr)
        return 2
