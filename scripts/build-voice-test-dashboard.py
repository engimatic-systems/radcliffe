#!/usr/bin/env python3
# ruff: noqa: E501 - the embedded standalone HTML/CSS/JS intentionally keeps source lines intact.
from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def _latest_report(artifacts: Path, suite: str) -> tuple[Path, dict[str, Any]] | None:
    candidates = sorted((artifacts / suite).glob("*/report.json"), reverse=True)
    for path in candidates:
        try:
            return path, json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
    return None


def _link_audio(source: Path, assets: Path, prefix: str) -> str:
    target = assets / f"{prefix}-{source.name}"
    if target.exists():
        target.unlink()
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    return f"assets/{target.name}"


def _voice_suite(report_path: Path, report: dict[str, Any], assets: Path) -> dict[str, Any]:
    artifact = report.get("artifacts", {})
    stream_name = artifact.get("review_timeline") or artifact.get("agent_timeline")
    stream = None
    if isinstance(stream_name, str) and (report_path.parent / stream_name).is_file():
        stream = _link_audio(report_path.parent / stream_name, assets, "livekit-full-stream")
    tests = []
    for check in report.get("checks", []):
        tests.append(
            {
                "id": str(check.get("name", "unnamed-check")),
                "passed": bool(check.get("passed")),
                "detail": str(check.get("detail", "")),
                "measured": check.get("measured", {}),
                "audio": stream,
                "audio_label": "Full duplex run"
                if artifact.get("review_timeline")
                else "Agent run",
            }
        )
    channels = artifact.get("review_channels")
    note = "Complete real LiveKit → local Whisper → Ollama → local TTS loop."
    if isinstance(channels, dict):
        note += " Stereo review: microphone on the left, assistant on the right."
    return {
        "id": "livekit",
        "title": "Full voice loop",
        "created_at": report.get("created_at"),
        "commit": report.get("commit"),
        "passed": bool(report.get("passed")),
        "note": note,
        "stream": stream,
        "tests": tests,
    }


def _rejection_suite(report_path: Path, report: dict[str, Any], assets: Path) -> dict[str, Any]:
    tests = []
    for case in report.get("cases", []):
        audio_name = case.get("audio")
        audio = None
        if isinstance(audio_name, str) and (report_path.parent / audio_name).is_file():
            audio = _link_audio(
                report_path.parent / audio_name,
                assets,
                f"rejection-{case.get('id', 'case')}",
            )
        expected = "accept" if case.get("expected_accept") else "reject"
        observed = "accepted" if case.get("accepted") else "rejected"
        transcript = str(case.get("transcript", ""))
        detail = f"Expected {expected}; observed {observed}."
        if transcript:
            detail += f" Transcript: {transcript!r}"
        tests.append(
            {
                "id": str(case.get("id", "unnamed-case")),
                "passed": bool(case.get("passed")),
                "detail": detail,
                "measured": {
                    "transcription_calls": case.get("transcription_calls"),
                    "expected_text": case.get("expected_text"),
                },
                "audio": audio,
                "audio_label": "Complete test input",
            }
        )
    return {
        "id": "rejection",
        "title": "Noise and false-turn rejection",
        "created_at": report.get("created_at"),
        "commit": report.get("commit"),
        "passed": bool(report.get("passed")),
        "note": "Each player is the complete microphone stream evaluated by the endpoint.",
        "stream": None,
        "tests": tests,
    }


def _stt_suite(report_path: Path, report: dict[str, Any], assets: Path) -> dict[str, Any]:
    corpus = {str(item.get("id")): item for item in report.get("corpus", [])}
    profiles = report.get("profiles", [])
    profile = profiles[0] if profiles else {}
    tests = []
    for case in profile.get("cases", []):
        case_id = str(case.get("id", "unnamed-case"))
        fixture = corpus.get(case_id, {})
        audio_name = fixture.get("audio")
        audio = None
        if isinstance(audio_name, str) and (report_path.parent / audio_name).is_file():
            audio = _link_audio(report_path.parent / audio_name, assets, f"stt-{case_id}")
        tests.append(
            {
                "id": case_id,
                "passed": bool(case.get("passed")),
                "detail": (
                    f"Reference: {case.get('reference', '')!r} "
                    f"Transcript: {case.get('hypothesis', '')!r}"
                ),
                "measured": {
                    "word_error_rate": case.get("word_error_rate"),
                    "keyword_recall": case.get("keyword_recall"),
                    "latency_seconds": case.get("latency_seconds"),
                    "noise_snr_db": fixture.get("noise_snr_db"),
                },
                "audio": audio,
                "audio_label": "Complete test input",
            }
        )
    return {
        "id": "stt",
        "title": "Speech recognition quality",
        "created_at": report.get("created_at"),
        "commit": report.get("commit"),
        "passed": bool(profile.get("passed")),
        "note": "Deterministic local Whisper corpus, including phone-like noise and pauses.",
        "stream": None,
        "tests": tests,
    }


def _history(artifacts: Path, suite: str) -> list[dict[str, Any]]:
    result = []
    for path in sorted((artifacts / suite).glob("*/report.json"), reverse=True):
        try:
            report = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        passed = report.get("passed")
        if passed is None and report.get("profiles"):
            passed = all(bool(item.get("passed")) for item in report["profiles"])
        result.append(
            {
                "run": path.parent.name,
                "created_at": report.get("created_at"),
                "commit": report.get("commit"),
                "passed": bool(passed),
            }
        )
    return result


PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Radcliffe voice test review</title>
  <style>
    :root { color-scheme: dark; --bg:#0b1013; --panel:#141c21; --ink:#f3f7f8;
      --muted:#9fb0b9; --line:#2a3941; --pass:#56d69a; --fail:#ff7d79; --accent:#79b8ff; }
    * { box-sizing:border-box; } body { margin:0; background:radial-gradient(circle at top,#16252c,var(--bg) 42%);
      color:var(--ink); font:15px/1.5 ui-sans-serif,system-ui,sans-serif; }
    main { width:min(1100px,calc(100% - 28px)); margin:0 auto; padding:42px 0 70px; }
    h1 { margin:0; font-size:clamp(30px,6vw,54px); letter-spacing:-.045em; } h2 { margin:0; }
    .lede,.note,.meta { color:var(--muted); } .lede { max-width:760px; font-size:17px; }
    .summary { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin:26px 0; }
    .stat,.suite,.test,dialog { background:color-mix(in srgb,var(--panel) 94%,transparent); border:1px solid var(--line);
      box-shadow:0 18px 55px #0004; } .stat { border-radius:14px; padding:15px; }
    .stat strong { display:block; font-size:25px; } .toolbar { display:flex; gap:10px; flex-wrap:wrap; margin:22px 0; }
    input,select { border:1px solid var(--line); background:#0d1519; color:var(--ink); border-radius:10px; padding:11px 13px; }
    input { flex:1; min-width:220px; } .suite { border-radius:18px; padding:20px; margin:18px 0; }
    .suite-head { display:flex; justify-content:space-between; align-items:flex-start; gap:18px; }
    .badge { display:inline-flex; align-items:center; gap:7px; padding:5px 9px; border-radius:999px; font-weight:750; }
    .pass { color:var(--pass); background:#143526; } .fail { color:var(--fail); background:#3b1f21; }
    audio { width:100%; margin:10px 0 2px; } .tests { display:grid; grid-template-columns:repeat(auto-fill,minmax(270px,1fr)); gap:10px; margin-top:16px; }
    .test { width:100%; text-align:left; color:var(--ink); border-radius:12px; padding:14px; cursor:pointer; }
    .test:hover,.test:focus-visible { border-color:var(--accent); transform:translateY(-1px); }
    .test strong { display:block; margin-bottom:5px; overflow-wrap:anywhere; }
    dialog { color:var(--ink); width:min(680px,calc(100% - 24px)); border-radius:18px; padding:22px; }
    dialog::backdrop { background:#000b; backdrop-filter:blur(4px); } .close { float:right; border:0; color:var(--ink); background:#26343b;
      border-radius:999px; width:34px; height:34px; cursor:pointer; } pre { white-space:pre-wrap; overflow-wrap:anywhere; color:#bfd0d8; }
    .empty { color:var(--muted); padding:24px 0; } footer { color:var(--muted); margin-top:30px; }
  </style>
</head>
<body><main>
  <h1>Radcliffe voice test review</h1>
  <p class="lede">Listen to the exact audio fixtures and the complete full-duplex voice run, then open any check for its result and measurements.</p>
  <section class="summary" id="summary"></section>
  <div class="toolbar"><input id="search" type="search" placeholder="Filter tests…"><select id="status"><option value="all">All results</option><option value="fail">Failures</option><option value="pass">Passes</option></select></div>
  <div id="suites"></div>
  <footer id="generated"></footer>
</main>
<dialog id="detail"><button class="close" aria-label="Close">×</button><h2></h2><p class="badge"></p><p class="detail"></p><div class="player"></div><pre></pre></dialog>
<script id="manifest" type="application/json">__MANIFEST__</script>
<script>
const data=JSON.parse(document.querySelector('#manifest').textContent); const suites=document.querySelector('#suites');
const all=data.suites.flatMap(s=>s.tests.map(t=>({...t,suite:s}))); const passed=all.filter(t=>t.passed).length;
document.querySelector('#summary').innerHTML=`<div class="stat"><span class="meta">Latest checks</span><strong>${all.length}</strong></div><div class="stat"><span class="meta">Passing</span><strong>${passed}</strong></div><div class="stat"><span class="meta">Failing</span><strong>${all.length-passed}</strong></div><div class="stat"><span class="meta">Suites</span><strong>${data.suites.length}</strong></div>`;
const esc=s=>{const e=document.createElement('span');e.textContent=s??'';return e.innerHTML};
function render(){ const q=document.querySelector('#search').value.toLowerCase(), status=document.querySelector('#status').value; suites.innerHTML='';
 data.suites.forEach(s=>{const tests=s.tests.filter(t=>(status==='all'||(status==='pass')===t.passed)&&(`${t.id} ${t.detail}`).toLowerCase().includes(q)); if(!tests.length)return;
 const el=document.createElement('section');el.className='suite'; const stream=s.stream?`<audio controls preload="metadata" src="${esc(s.stream)}"></audio>`:'';
 el.innerHTML=`<div class="suite-head"><div><h2>${esc(s.title)}</h2><div class="meta">${esc(s.created_at)} · ${esc((s.commit||'').slice(0,12))}</div></div><span class="badge ${s.passed?'pass':'fail'}">${s.passed?'● PASS':'● FAIL'}</span></div><p class="note">${esc(s.note)}</p>${stream}<div class="tests"></div>`;
 const grid=el.querySelector('.tests'); tests.forEach(t=>{const b=document.createElement('button');b.className='test';b.innerHTML=`<strong>${esc(t.id)}</strong><span class="badge ${t.passed?'pass':'fail'}">${t.passed?'PASS':'FAIL'}</span><div class="meta">${esc(t.detail)}</div>`;b.onclick=()=>openDetail(t,s);grid.appendChild(b)}); suites.appendChild(el); });
 if(!suites.children.length)suites.innerHTML='<p class="empty">No tests match this filter.</p>'; }
function openDetail(t,s){const d=document.querySelector('#detail');d.querySelector('h2').textContent=t.id;const badge=d.querySelector('.badge');badge.textContent=t.passed?'● PASS':'● FAIL';badge.className=`badge ${t.passed?'pass':'fail'}`;d.querySelector('.detail').textContent=t.detail;d.querySelector('pre').textContent=JSON.stringify(t.measured||{},null,2);d.querySelector('.player').innerHTML=t.audio?`<p class="note">${esc(t.audio_label||'Audio')}</p><audio controls autoplay preload="metadata" src="${esc(t.audio)}"></audio>`:'<p class="note">No audio artifact for this check.</p>';d.showModal()}
document.querySelector('#detail .close').onclick=()=>document.querySelector('#detail').close();document.querySelector('#detail').onclick=e=>{if(e.target.id==='detail')e.target.close()};document.querySelector('#search').oninput=render;document.querySelector('#status').onchange=render;document.querySelector('#generated').textContent=`Generated ${data.generated_at}. Latest artifacts are shown; ${data.history_count} historical run reports were indexed.`;render();
</script></body></html>"""


def run(args: argparse.Namespace) -> int:
    artifacts = Path(args.artifacts).resolve()
    output = Path(args.output).expanduser().resolve()
    assets = output / "assets"
    if assets.exists():
        shutil.rmtree(assets)
    assets.mkdir(parents=True, exist_ok=True)

    suites = []
    builders = (
        ("voice-conformance", _voice_suite),
        ("stt-rejection", _rejection_suite),
        ("stt-conformance", _stt_suite),
    )
    for suite_name, builder in builders:
        latest = _latest_report(artifacts, suite_name)
        if latest is not None:
            suites.append(builder(*latest, assets))

    history = {name: _history(artifacts, name) for name, _builder in builders}
    manifest = {
        "schema": "radcliffe-voice-dashboard-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "suites": suites,
        "history": history,
        "history_count": sum(len(items) for items in history.values()),
    }
    encoded = (
        json.dumps(manifest, separators=(",", ":")).replace("&", "\\u0026").replace("<", "\\u003c")
    )
    (output / "index.html").write_text(PAGE.replace("__MANIFEST__", encoded))
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(output / "index.html")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", default=ROOT / "artifacts")
    parser.add_argument(
        "--output", default=Path.home() / "agent-share" / "radcliffe" / "voice-tests"
    )
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
