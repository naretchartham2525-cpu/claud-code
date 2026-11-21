"""เทมเพลตแบบ "ละครการ์ตูน" (engine แบบ thai-drama-script: scripts/render_ep.py + engine/)

โปรเจกต์ = 1 ซีรีส์: series.json + epNN.json + bg/ + .voice_cache/ (ใช้โครงเดียวกับที่ engine ต้องการ)
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

RUNNER = Path(__file__).with_name("drama_runner.py")
PRESETS = {"auto": "อัตโนมัติ (เดาจากชื่อสถานที่)", "market": "ตลาดสด", "rented_room": "ห้องเช่า", "garage": "อู่ซ่อมรถ"}


class DramaError(Exception):
    pass


def detect(root: Path) -> bool:
    return (root / "scripts" / "render_ep.py").is_file() and (root / "engine").is_dir()


def parse_json(text: str, what: str) -> dict:
    text = (text or "").strip()
    # รองรับกรณีวางมาพร้อม ```json ... ```
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise DramaError(f"{what}: JSON ไม่ถูกต้อง บรรทัด {e.lineno} คอลัมน์ {e.colno} ({e.msg})")
    if not isinstance(data, dict):
        raise DramaError(f"{what}: ต้องเป็น JSON object {{...}}")
    return data


def save_series(pdir: Path, text: str) -> dict:
    s = parse_json(text, "series.json")
    if not s.get("cast") or not s.get("places"):
        raise DramaError("series.json ต้องมี cast และ places")
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "series.json").write_text(json.dumps(s, ensure_ascii=False, indent=2), "utf-8")
    return s


def save_episode(pdir: Path, text: str) -> tuple[str, dict]:
    ep = parse_json(text, "บทตอน (epNN.json)")
    if not isinstance(ep.get("ep"), int) or not ep.get("shots"):
        raise DramaError("บทตอนต้องมี \"ep\" (ตัวเลข) และ \"shots\"")
    name = f"ep{ep['ep']:02d}.json"
    (pdir / name).write_text(json.dumps(ep, ensure_ascii=False, indent=2), "utf-8")
    return name, ep


def episodes(pdir: Path) -> list[str]:
    return sorted(p.name for p in pdir.glob("ep[0-9]*.json"))


def load(pdir: Path, name: str) -> dict:
    return json.loads((pdir / name).read_text("utf-8"))


def voice_slots(series: dict) -> list[tuple[str, str, str]]:
    """ช่องเลือกเสียง: (id, ชื่อ, ประเภทเสียง)"""
    out = [(c["id"], c.get("name", c["id"]), c.get("voice", "")) for c in series.get("cast", []) if "id" in c]
    if not any(i == "hero" for i, _, _ in out):
        out.insert(0, ("hero", "ผม (ผู้เล่า)", "man"))
    out.append(("system", "เสียงระบบ", "system"))
    return out


def _run(cmd, cwd, on_line=None, timeout=3600) -> str:
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    lines = []
    for line in p.stdout:
        lines.append(line.rstrip())
        if on_line:
            on_line(line.rstrip())
    p.wait(timeout=timeout)
    out = "\n".join(lines)
    if p.returncode != 0:
        raise DramaError(out[-3000:] or f"exit {p.returncode}")
    return out


def check(root: Path, pdir: Path, ep_name: str, sec: int = 0) -> tuple[bool, str]:
    cmd = [sys.executable, str(root / "scripts" / "drama_tool.py"), "check", str(pdir / ep_name)]
    if sec:
        cmd += ["--sec", str(sec)]
    try:
        out = _run(cmd, pdir)
    except DramaError as e:
        out = str(e)
    last = out.strip().splitlines()[-1] if out.strip() else ""
    return last.startswith("PASS"), out


def apply_backgrounds(pdir: Path, series: dict, choice: dict) -> None:
    """เขียน bg/<place>.py ให้ใช้ฉากสำเร็จรูปที่เลือก (auto = ให้ engine เดาเอง)"""
    bg = pdir / "bg"
    bg.mkdir(exist_ok=True)
    for i, pl in enumerate(series.get("places", [])):
        pid = pl.get("id")
        if not pid or not re.fullmatch(r"[\w-]+", pid):
            continue
        key = choice.get(pid, "auto")
        py, png = bg / f"{pid}.py", bg / f"{pid}.png"
        mark = f"# clipstudio-preset: {key}\n"
        if key == "auto":
            if py.exists() and py.read_text("utf-8").startswith("# clipstudio-preset"):
                py.unlink()
                png.unlink(missing_ok=True)
            continue
        if py.exists() and py.read_text("utf-8").startswith(mark):
            continue  # ตั้งไว้แบบนี้อยู่แล้ว
        py.write_text(mark + "from scene_kit import PRESETS\n\n\ndef draw():\n"
                      f"    return PRESETS[{key!r}](seed={101 + i * 37})\n", "utf-8")
        png.unlink(missing_ok=True)


def render(root: Path, pdir: Path, ep_name: str, voice_cfg: dict, still: int = 0, on_line=None) -> Path:
    cfg = pdir / ".voice_cfg.json"
    cfg.write_text(json.dumps(voice_cfg, ensure_ascii=False), "utf-8")
    args = [str(pdir / ep_name)]
    if still:
        args += ["--still", str(still)]
    out = _run([sys.executable, str(RUNNER), str(root), str(cfg), "--"] + args, pdir, on_line)
    if "ERROR" in out and "DONE" not in out and "still saved" not in out:
        raise DramaError(out[-3000:])
    if still:
        m = re.search(r"still saved: (.+\.png)", out)
        if not m:
            raise DramaError(out[-3000:])
        p = Path(m.group(1))
        return p if p.is_absolute() else pdir / p
    ep = load(pdir, ep_name)
    mp4 = pdir / f"ep{int(ep.get('ep', 0)):02d}.mp4"
    if not mp4.exists():
        raise DramaError(out[-3000:])
    if "WARNING voice failed" in out:
        line = next(x for x in out.splitlines() if "WARNING voice failed" in x)
        raise DramaError("เรนเดอร์ได้แต่ไม่มีเสียง: " + line + f"\n(ไฟล์เงียบอยู่ที่ {mp4.name})")
    return mp4
