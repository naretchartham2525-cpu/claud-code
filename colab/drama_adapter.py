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
PRESETS = {"claude": "🎨 ฉากที่ Claude วาด (อนุมัติแล้ว)", "auto": "สำเร็จรูป (เดาจากชื่อ)", "market": "ตลาดสด", "rented_room": "ห้องเช่า", "garage": "อู่ซ่อมรถ"}


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


def apply_backgrounds(pdir: Path, series: dict, choice: dict, lib_dir: Path | None = None) -> None:
    """ตั้งฉากของแต่ละสถานที่ (bg/<place>.py/.png ที่ engine อ่าน) ตามที่เลือก
    claude = ฉากที่ Claude วาดและอนุมัติแล้ว (bg/_approved) · lib:<id> = จากคลังฉาก · auto = engine เดา · อื่นๆ = ฉากสำเร็จรูป"""
    import os
    import shutil

    bg = pdir / "bg"
    bg.mkdir(exist_ok=True)
    for i, pl in enumerate(series.get("places", [])):
        pid = pl.get("id")
        if not pid or not re.fullmatch(r"[\w-]+", pid):
            continue
        key = choice.get(pid, "claude")
        py, png = bg / f"{pid}.py", bg / f"{pid}.png"
        src = None
        if key == "claude":
            src = bg / "_approved" / pid
        elif key.startswith("lib:") and lib_dir:
            src = lib_dir / key[4:] / "draw"
        if src is not None:
            if src.with_suffix(".py").exists() and src.with_suffix(".png").exists():
                shutil.copy(src.with_suffix(".py"), py)
                shutil.copy(src.with_suffix(".png"), png)
                os.utime(png)  # png ใหม่กว่า .py -> engine ใช้ภาพเลย ไม่รันโค้ดซ้ำ
            else:  # ยังไม่มีฉาก: ให้ engine ใช้ฉากสำเร็จรูปที่เดาจากชื่อ
                py.unlink(missing_ok=True)
                png.unlink(missing_ok=True)
            continue
        if key == "auto" or key not in PRESETS:
            py.unlink(missing_ok=True)
            png.unlink(missing_ok=True)
            continue
        mark = f"# clipstudio-preset: {key}\n"
        if py.exists() and py.read_text("utf-8").startswith(mark):
            continue  # ตั้งไว้แบบนี้อยู่แล้ว
        py.write_text(mark + "from scene_kit import PRESETS\n\n\ndef draw():\n"
                      f"    return PRESETS[{key!r}](seed={101 + i * 37})\n", "utf-8")
        png.unlink(missing_ok=True)


def render(root: Path, pdir: Path, ep_name: str, voice_cfg: dict, still: int = 0, on_line=None) -> Path:
    if voice_cfg.get("engine") == "eleven":  # ข้อความใน engine เขียนว่า edge-tts เสมอ แก้ให้ตรงกับที่ใช้จริง
        _cb = on_line

        def on_line(line, _cb=_cb):
            if _cb:
                _cb(line.replace("with edge-tts", "with ElevenLabs"))
    cfg = pdir / ".voice_cfg.json"
    cfg.write_text(json.dumps(voice_cfg, ensure_ascii=False), "utf-8")
    args = [str(pdir / ep_name)]
    if still:
        args += ["--still", str(still)]
    out = _run([sys.executable, str(RUNNER), str(root), str(cfg), "--"] + args, pdir, on_line)
    if voice_cfg.get("engine") == "eleven":
        out = out.replace("with edge-tts", "with ElevenLabs")
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


# ---------------------------------------------------------------- ฉากหลังที่ Claude ออกแบบจากบท
BG_BUILD = r"""
import sys, importlib.util
sys.path.insert(0, sys.argv[1])
spec = importlib.util.spec_from_file_location("bg", sys.argv[2]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
img = m.draw()
img = img[0] if isinstance(img, tuple) else img
import numpy as np, cv2
from PIL import Image
img = np.clip(np.asarray(img), 0, 255).astype(np.uint8)
if img.ndim != 3 or img.shape[2] != 3:
    raise SystemExit("draw() ต้องคืนภาพ RGB")
Image.fromarray(cv2.resize(img, (1080, 1920), interpolation=cv2.INTER_AREA)).save(sys.argv[3])
"""


def used_place_ids(pdir: Path) -> set[str]:
    ids = set()
    for name in episodes(pdir):
        for sh in load(pdir, name).get("shots", []):
            if sh.get("type") == "scene" and sh.get("place"):
                ids.add(sh["place"])
    return ids


def _bg_context(root: Path) -> str:
    parts = []
    for rel in ("reference/scenes.md", "engine/scene_kit.py", "engine/drama_sets.py"):
        f = root / rel
        if f.exists():
            parts.append(f"=== {rel} ===\n{f.read_text('utf-8')}")
    for ex in sorted(root.glob("examples/*/bg/*.py"))[:2]:
        parts.append(f"=== ตัวอย่าง {ex.relative_to(root)} ===\n{ex.read_text('utf-8')}")
    return "\n\n".join(parts)


def _place_brief(pl: dict, pdir: Path, series: dict) -> str:
    acts = []
    for name in episodes(pdir):
        for sh in load(pdir, name).get("shots", []):
            if sh.get("place") == pl["id"]:
                acts += [c.get("action", "") for c in sh.get("chars", []) if c.get("action")]
                if sh.get("sfx"):
                    acts.append("sfx: " + sh["sfx"])
    return (f"สถานที่: {json.dumps(pl, ensure_ascii=False)}\n"
            f"เรื่อง: {series.get('plot', '')[:600]}\n"
            f"สิ่งที่เกิดในสถานที่นี้ตามบท: {'; '.join(dict.fromkeys(acts))[:1500]}")


def _extract_code(text: str) -> str:
    m = re.search(r"```(?:python)?\s*(.*?)```", text, re.S)
    code = (m.group(1) if m else text).strip()
    if "def draw" not in code:
        raise DramaError("Claude ไม่ได้ส่งโค้ดฉากกลับมา")
    return code


def _build_png(root: Path, py: Path, png: Path) -> None:
    r = subprocess.run([sys.executable, "-c", BG_BUILD, str(root / "engine"), str(py), str(png)],
                       capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout)[-1500:])


def cand_paths(pdir: Path, pid: str) -> tuple[Path, Path]:
    d = pdir / "bg" / "_cand"
    return d / f"{pid}.py", d / f"{pid}.png"


def draw_candidate(root: Path, pdir: Path, series: dict, place: dict, model: str,
                   review: bool = True, feedback: str = "", log=print) -> Path:
    """ให้ Claude วาดฉากตัวเลือก (ยังไม่ใช้จริงจนกว่าจะอนุมัติ) คืน path ภาพ"""
    import base64
    import io
    import os

    import anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise DramaError("ยังไม่ได้ใส่ ANTHROPIC_API_KEY (Colab: เมนู 🔑 Secrets) จึงให้ Claude วาดฉากไม่ได้")
    client = anthropic.Anthropic()
    system = [
        {"type": "text", "text":
            "คุณวาดฉากหลังการ์ตูนแนวตั้งด้วยโค้ด Python ตามคู่มือด้านล่างเท่านั้น (ใช้ได้แค่ฟังก์ชันใน scene_kit)\n"
            "ตอบเป็นโค้ดไฟล์เดียวใน ```python``` เริ่มด้วย `from scene_kit import *` และมีฟังก์ชัน `draw()` ที่ return room.finish(...)\n"
            "ฉากต้องเป็นสถานที่ว่าง ไม่มีคน ไม่มีตัวหนังสือ เว้นพื้นตรงกลางล่างให้ตัวละครยืน แสงและบรรยากาศตรงกับเวลา/อากาศของสถานที่"},
        {"type": "text", "text": _bg_context(root), "cache_control": {"type": "ephemeral"}},
    ]
    kwargs = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} if model in ("claude-sonnet-5-5", "claude-opus-5-5") else {}

    def ask(messages):
        try:
            res = client.beta.messages.create(model=model, max_tokens=16000, output_config={"effort": "medium"},
                                              system=system, messages=messages, **kwargs)
        except anthropic.AuthenticationError:
            raise DramaError("Anthropic API key ไม่ถูกต้อง")
        except anthropic.RateLimitError:
            raise DramaError("Claude ถูกจำกัดการใช้งานชั่วคราว ลองใหม่อีกสักครู่")
        except anthropic.APIStatusError as e:
            raise DramaError(f"Claude API {e.status_code}: {e.message}")
        except anthropic.APIConnectionError:
            raise DramaError("เชื่อมต่อ Claude ไม่ได้")
        if res.stop_reason == "refusal":
            raise DramaError("Claude ปฏิเสธการวาดฉากนี้")
        return res.content, "".join(b.text for b in res.content if b.type == "text")

    py, png = cand_paths(pdir, place["id"])
    py.parent.mkdir(parents=True, exist_ok=True)
    ask_text = "วาดฉากหลังสำหรับสถานที่นี้\n" + _place_brief(place, pdir, series)
    if feedback.strip():
        prev = py.read_text("utf-8") if py.exists() else ""
        ask_text += f"\n\nผู้ใช้อยากให้ปรับ: {feedback.strip()}"
        if prev:
            ask_text += f"\n\nโค้ดฉบับก่อนหน้า (แก้จากอันนี้):\n```python\n{prev}\n```"
    log(f"🎨 Claude กำลังวาดฉาก: {place.get('name', place['id'])}")
    msgs = [{"role": "user", "content": ask_text}]
    content, last_err = None, ""
    for attempt in range(3):  # โค้ดพัง: ส่ง error กลับให้แก้ (สูงสุด 2 ครั้ง)
        content, text = ask(msgs)
        py.write_text(_extract_code(text) + "\n", "utf-8")
        try:
            _build_png(root, py, png)
            break
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            log(f"   โค้ดฉากพัง ให้ Claude แก้ (ครั้งที่ {attempt + 1})")
            msgs += [{"role": "assistant", "content": content},
                     {"role": "user", "content": f"รันแล้ว error:\n{e}\nส่งโค้ดทั้งไฟล์ที่แก้แล้ว"}]
    else:
        py.unlink(missing_ok=True)
        png.unlink(missing_ok=True)
        raise DramaError(f"Claude วาดฉากไม่สำเร็จ: {last_err[-300:]}")
    if review:  # ให้ Claude ดูภาพที่ได้ 1 รอบ แล้วแก้ถ้าไม่ตรง
        from PIL import Image
        buf = io.BytesIO()
        Image.open(png).convert("RGB").resize((540, 960)).save(buf, "JPEG", quality=80)
        msgs += [{"role": "assistant", "content": content}, {"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(buf.getvalue()).decode()}},
            {"type": "text", "text": "นี่คือภาพที่ได้ ตรงกับสถานที่ไหม (ของในฉาก แสง บรรยากาศ พื้นกลางว่าง) "
                                     "ถ้าดีแล้วตอบคำเดียวว่า OK ถ้าควรแก้ ส่งโค้ดทั้งไฟล์ที่แก้แล้ว"}]}]
        _c, text = ask(msgs)
        if "def draw" in text:
            old = py.read_text("utf-8")
            py.write_text(_extract_code(text) + "\n", "utf-8")
            try:
                _build_png(root, py, png)
                log("   Claude ดูภาพแล้วปรับให้อีกรอบ")
            except Exception:  # noqa: BLE001 — ฉบับแก้พัง ใช้ฉบับแรก
                py.write_text(old, "utf-8")
                _build_png(root, py, png)
    log(f"   ✅ วาดเสร็จ รออนุมัติ: {place.get('name', place['id'])}")
    return png


def approve_candidate(pdir: Path, place: dict, lib_dir: Path, series_title: str = "") -> str:
    """ใช้ฉากตัวเลือกกับซีรีส์นี้ + เก็บเข้าคลังฉาก คืน id ในคลัง"""
    import secrets
    import shutil
    from datetime import datetime

    py, png = cand_paths(pdir, place["id"])
    if not (py.exists() and png.exists()):
        raise DramaError("ยังไม่มีฉากที่รออนุมัติ")
    ap = pdir / "bg" / "_approved"
    ap.mkdir(parents=True, exist_ok=True)
    shutil.copy(py, ap / f"{place['id']}.py")
    shutil.copy(png, ap / f"{place['id']}.png")
    lid = secrets.token_hex(5)
    d = lib_dir / lid
    d.mkdir(parents=True)
    shutil.move(str(py), d / "draw.py")
    shutil.move(str(png), d / "draw.png")
    (d / "meta.json").write_text(json.dumps({
        "id": lid, "name": place.get("name", place["id"]), "place": place, "series": series_title,
        "createdAt": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False, indent=2), "utf-8")
    return lid


def discard_candidate(pdir: Path, pid: str) -> None:
    for f in cand_paths(pdir, pid):
        f.unlink(missing_ok=True)


def library(lib_dir: Path) -> list[dict]:
    if not lib_dir.exists():
        return []
    items = []
    for d in lib_dir.iterdir():
        m = d / "meta.json"
        if m.exists() and (d / "draw.png").exists():
            items.append(json.loads(m.read_text("utf-8")))
    return sorted(items, key=lambda x: x["createdAt"], reverse=True)


def delete_library_item(lib_dir: Path, lid: str) -> None:
    import shutil
    if re.fullmatch(r"[a-f0-9]{6,20}", lid or ""):
        shutil.rmtree(lib_dir / lid, ignore_errors=True)


def suggest_from_library(place: dict, lib: list[dict]) -> dict | None:
    """หาฉากในคลังที่ชื่อ/คำบรรยายใกล้เคียงสถานที่นี้"""
    import difflib

    def text(p):
        return f"{p.get('name', '')} {p.get('id', '')} {p.get('image_prompt', '')[:120]}".lower()

    best, score = None, 0.0
    for item in lib:
        r = difflib.SequenceMatcher(None, text(place), text(item.get("place", {}))).ratio()
        if r > score:
            best, score = item, r
    return best if score >= 0.55 else None


def bg_status(pdir: Path, series: dict, choice: dict, lib_dir: Path) -> list[dict]:
    """สถานะฉากของทุกสถานที่ที่บทใช้"""
    used = used_place_ids(pdir)
    lib = library(lib_dir)
    out = []
    for pl in series.get("places", []):
        pid = pl.get("id")
        if not pid or not re.fullmatch(r"[\w-]+", pid):
            continue
        key = choice.get(pid, "claude")
        approved = pdir / "bg" / "_approved" / f"{pid}.png"
        cand = cand_paths(pdir, pid)[1]
        if key == "claude":
            state = "approved" if approved.exists() else "missing"
            preview = approved if approved.exists() else None
        elif key.startswith("lib:"):
            p = lib_dir / key[4:] / "draw.png"
            state, preview = ("library", p) if p.exists() else ("missing", None)
        else:
            state, preview = "preset", None
        out.append({"place": pl, "key": key, "state": state, "used": pid in used,
                    "preview": str(preview) if preview else None,
                    "cand": str(cand) if cand.exists() else None,
                    "suggest": suggest_from_library(pl, lib) if state == "missing" else None})
    return out
