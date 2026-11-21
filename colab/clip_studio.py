"""Clip Studio (Colab / Gradio)

เก็บสกิล -> ให้ Claude คิดบท หรือวางบทเอง -> แก้บทรายฉาก -> พากย์ ElevenLabs -> เรนเดอร์ MP4 (ffmpeg + ซับไทย)

ข้อมูลทั้งหมดเก็บใน CLIPSTUDIO_DIR (ใน Colab ตั้งเป็นโฟลเดอร์ใน Google Drive เพื่อไม่ให้หายตอนปิด)
API keys อ่านจาก env: ANTHROPIC_API_KEY, ELEVENLABS_API_KEY
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

import gradio as gr
import requests
from pydantic import BaseModel, Field

import drama_adapter as D
import viral_adapter as V

DATA = Path(os.environ.get("CLIPSTUDIO_DIR", "clipstudio_data")).resolve()
SKILLS = DATA / "skills"
PROJECTS = DATA / "projects"
for d in (SKILLS, PROJECTS):
    d.mkdir(parents=True, exist_ok=True)

MODELS = {
    "Sonnet 5.5 (สมดุล ~1–2 บาท/บท)": "claude-sonnet-5-5",
    "Haiku 5.5 (ถูกสุด)": "claude-haiku-5-5",
    "Opus 5.5 (เก่งสุด ~2–4 บาท/บท)": "claude-opus-5-5",
}
# โมเดลที่รองรับ server-side fallback (ถ้าโดนปฏิเสธ API สลับโมเดลสำรองให้เอง)
FALLBACK_OK = {"claude-sonnet-5-5", "claude-opus-5-5"}
ELEVEN_MODELS = ["eleven_v3", "eleven_multilingual_v2", "eleven_flash_v2_5"]
SIZES = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
FPS = 30
FONT = os.environ.get("SUB_FONT", "Garuda")
FONTS_DIR = os.environ.get("SUB_FONTS_DIR", "/usr/share/fonts/truetype/tlwg")


class UserError(Exception):
    """ข้อผิดพลาดที่แสดงให้ผู้ใช้เห็นตรงๆ"""


# ---------------------------------------------------------------- storage
def _new_id() -> str:
    return secrets.token_hex(6)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read(p: Path, default=None):
    try:
        return json.loads(p.read_text("utf-8"))
    except FileNotFoundError:
        return default


def _write(p: Path, data) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(p)


def _safe(id_: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{6,32}", id_ or ""):
        raise UserError("id ไม่ถูกต้อง")
    return id_


def list_skills() -> list[dict]:
    items = [_read(f) for f in SKILLS.glob("*.json")]
    return sorted([s for s in items if s], key=lambda s: s["updatedAt"], reverse=True)


def get_skill(sid: str | None) -> dict | None:
    return _read(SKILLS / f"{_safe(sid)}.json") if sid else None


def save_skill(sid, name, desc, content, aspect, bg, fg, voice) -> dict:
    if not (name or "").strip() or not (content or "").strip():
        raise UserError("ใส่ชื่อและเนื้อหาสกิล")
    sid = _safe(sid) if sid else _new_id()
    old = get_skill(sid) or {"createdAt": _now()}
    skill = {
        **old, "id": sid, "name": name.strip()[:120], "description": (desc or "").strip()[:500],
        "content": content,
        "style": {"aspect": aspect if aspect in SIZES else "9:16", "bgColor": bg or "#111827",
                  "textColor": fg or "#FFFFFF", "voiceId": voice or ""},
        "updatedAt": _now(),
    }
    _write(SKILLS / f"{sid}.json", skill)
    return skill


def import_skill_file(path: str) -> dict:
    text = Path(path).read_text("utf-8", errors="replace")
    name, desc = Path(path).stem, ""
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?", text, re.S)
    if m:
        fm = m.group(1)
        if n := re.search(r"^name:\s*(.+)$", fm, re.M):
            name = n.group(1).strip().strip("\"'")
        if d := re.search(r"^description:\s*(.+)$", fm, re.M):
            desc = d.group(1).strip().strip("\"'")
        text = text[m.end():]
    return save_skill(None, name, desc, text, "9:16", "#111827", "#FFFFFF", "")


# ---------------------------------------------------------------- templates (zip)
# เทมเพลตแบบโฟลเดอร์/zip (มีโค้ด+ไฟล์ประกอบ) เก็บที่ templates/<id>/files/ + meta.json
# เทมเพลตแบบข้อความล้วน ใช้ระบบ skills/*.json ด้านบน (type "simple")
TEMPLATES = DATA / "templates"
TEMPLATES.mkdir(parents=True, exist_ok=True)


def _frontmatter(text: str) -> tuple[str, str, str]:
    """คืน (name, description, เนื้อหาที่ตัด frontmatter แล้ว)"""
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?", text, re.S)
    if not m:
        return "", "", text
    fm = m.group(1)
    n = re.search(r"^name:\s*(.+)$", fm, re.M)
    d = re.search(r"^description:\s*(.+)$", fm, re.M)
    strip = lambda x: x.group(1).strip().strip("\"'") if x else ""  # noqa: E731
    return strip(n), strip(d), text[m.end():]


def tdir(tid: str) -> Path:
    return TEMPLATES / _safe(tid)


def template_root(t: dict) -> Path:
    return tdir(t["id"]) / "files" / t.get("root", "")


def list_templates() -> list[dict]:
    """รวมเทมเพลตทุกแบบ (simple + โฟลเดอร์)"""
    items = [{**s, "type": "simple"} for s in list_skills()]
    items += [m for m in (_read(d / "meta.json") for d in TEMPLATES.iterdir() if d.is_dir()) if m]
    return sorted(items, key=lambda t: t["updatedAt"], reverse=True)


def get_template(tid: str | None) -> dict | None:
    if not tid:
        return None
    s = get_skill(tid)
    if s:
        return {**s, "type": "simple"}
    return _read(tdir(tid) / "meta.json")


def _detect_type(root: Path) -> str:
    from drama_adapter import detect
    import viral_adapter
    if viral_adapter.detect(root):
        return "viral"
    return "drama" if detect(root) else "files"


def _safe_extract(zip_path: str, dest: Path) -> None:
    import zipfile
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename
            # ชื่อไฟล์ไทยใน zip บางตัว encode เป็น cp437
            if not info.flag_bits & 0x800:
                try:
                    name = name.encode("cp437").decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
            target = (dest / name).resolve()
            if not str(target).startswith(str(dest.resolve())) or "__MACOSX" in name:
                continue  # กัน zip-slip และไฟล์ขยะของ macOS
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def _find_root(files: Path) -> Path:
    """โฟลเดอร์ที่มี SKILL.md ตื้นที่สุด หรือโฟลเดอร์ชั้นเดียวที่ห่ออยู่"""
    cands = sorted(files.rglob("SKILL.md"), key=lambda p: len(p.parts))
    if cands:
        return cands[0].parent
    kids = [p for p in files.iterdir() if not p.name.startswith(".")]
    return kids[0] if len(kids) == 1 and kids[0].is_dir() else files


def import_template_zip(path: str) -> dict:
    tid = _new_id()
    d = tdir(tid)
    files = d / "files"
    files.mkdir(parents=True)
    try:
        _safe_extract(path, files)
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(d, ignore_errors=True)
        raise UserError(f"แตกไฟล์ zip ไม่ได้: {e}")
    root = _find_root(files)
    skill_md = root / "SKILL.md"
    name, desc, body = _frontmatter(skill_md.read_text("utf-8", errors="replace")) if skill_md.exists() else ("", "", "")
    ttype = _detect_type(root)
    if ttype == "files" and not any(root.rglob("*.py")):
        # มีแค่ข้อความ: เก็บเป็นเทมเพลตแบบ simple ให้แก้ในฟอร์มได้
        shutil.rmtree(d, ignore_errors=True)
        return save_skill(None, name or Path(path).stem, desc, body or "(ว่าง)", "9:16", "#111827", "#FFFFFF", "")
    meta = {"id": tid, "type": ttype, "name": name or Path(path).stem, "description": desc,
            "root": str(root.relative_to(files)) if root != files else "", "createdAt": _now(), "updatedAt": _now()}
    _write(d / "meta.json", meta)
    return meta


def import_template_file(path: str) -> dict:
    if path.lower().endswith(".zip"):
        return import_template_zip(path)
    name, desc, body = _frontmatter(Path(path).read_text("utf-8", errors="replace"))
    return save_skill(None, name or Path(path).stem, desc, body, "9:16", "#111827", "#FFFFFF", "")


def add_template_files(tid: str, paths: list[str]) -> dict:
    t = get_template(tid)
    if not t or t["type"] == "simple":
        raise UserError("เพิ่มไฟล์ได้เฉพาะเทมเพลตแบบโฟลเดอร์ (zip)")
    root = template_root(t)
    for p in paths:
        if p.lower().endswith(".zip"):
            _safe_extract(p, root)
        else:
            shutil.copy(p, root / Path(p).name)
    t["updatedAt"] = _now()
    _write(tdir(tid) / "meta.json", t)
    return t


def duplicate_template(tid: str) -> dict:
    t = get_template(tid)
    if not t:
        raise UserError("ไม่พบเทมเพลต")
    if t["type"] == "simple":
        st = t["style"]
        return save_skill(None, t["name"] + " (สำเนา)", t.get("description", ""), t["content"],
                          st["aspect"], st["bgColor"], st["textColor"], st.get("voiceId", ""))
    nid = _new_id()
    shutil.copytree(tdir(tid), tdir(nid))
    meta = {**t, "id": nid, "name": t["name"] + " (สำเนา)", "createdAt": _now(), "updatedAt": _now()}
    _write(tdir(nid) / "meta.json", meta)
    return meta


def delete_template(tid: str) -> None:
    (SKILLS / f"{_safe(tid)}.json").unlink(missing_ok=True)
    shutil.rmtree(tdir(tid), ignore_errors=True)


def export_template(tid: str) -> str:
    t = get_template(tid)
    if not t:
        raise UserError("ไม่พบเทมเพลต")
    out = Path("/tmp") / f"template_{tid}"
    if t["type"] == "simple":
        out.mkdir(exist_ok=True)
        (out / "SKILL.md").write_text(f"---\nname: {t['name']}\ndescription: {t.get('description', '')}\n---\n{t['content']}", "utf-8")
        return shutil.make_archive(str(out), "zip", out)
    return shutil.make_archive(str(out), "zip", tdir(tid) / "files")


def pdir(pid: str) -> Path:
    return PROJECTS / _safe(pid)


def list_projects(kind: str = "simple") -> list[dict]:
    items = [_read(d / "project.json") for d in PROJECTS.iterdir() if d.is_dir()]
    items = [p for p in items if p and p.get("type", "simple") == kind]
    return sorted(items, key=lambda p: p["updatedAt"], reverse=True)


def get_project(pid: str) -> dict:
    p = _read(pdir(pid) / "project.json")
    if not p:
        raise UserError("ไม่พบโปรเจกต์")
    return p


def save_project(p: dict) -> dict:
    p["updatedAt"] = _now()
    pdir(p["id"]).mkdir(parents=True, exist_ok=True)
    _write(pdir(p["id"]) / "project.json", p)
    return p


# ---------------------------------------------------------------- Claude
class Scene(BaseModel):
    narration: str = Field(description="บทพากย์ของฉากนี้ อ่านออกเสียงได้ทันที")
    caption: str = Field(description="ข้อความสั้นบนจอ (ว่างได้ ถ้าจะใช้บทพากย์เป็นซับ)")
    visual: str = Field(description="คำบรรยายภาพ/ฟุตเทจที่ควรใช้ในฉากนี้")


class Script(BaseModel):
    title: str
    scenes: list[Scene]


def _system(skill: dict | None) -> str:
    base = (
        "คุณคือคนเขียนบทคลิปวิดีโอสั้น เขียนบทที่พากย์ออกเสียงแล้วฟังลื่น เป็นธรรมชาติ\n"
        "แบ่งเป็นฉาก ฉากละ 1-3 ประโยค ยาวพอดีกับภาพหนึ่งภาพ\n"
        "เขียนภาษาเดียวกับที่ผู้ใช้ขอ (ค่าเริ่มต้น: ไทย) ตัวเลขให้เขียนเป็นคำอ่านถ้าอ่านยาก\n"
        "ห้ามใส่คำกำกับเสียง วงเล็บ หรือ emoji ในบทพากย์"
    )
    if skill:
        base += f"\n\n## สกิล/สไตล์ที่ต้องทำตาม: {skill['name']}\n{skill.get('description', '')}\n\n{skill['content']}"
    return base


def ask_claude(model: str, skill: dict | None, user_text: str) -> Script:
    import anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise UserError("ยังไม่ได้ใส่ ANTHROPIC_API_KEY (Colab: เมนู 🔑 Secrets) หรือใช้โหมด 'วางบทเอง'")
    kwargs = {}
    if model in FALLBACK_OK:
        kwargs = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    try:
        res = anthropic.Anthropic().beta.messages.parse(
            model=model,
            max_tokens=16000,
            output_config={"effort": "medium"},
            output_format=Script,
            system=_system(skill),
            messages=[{"role": "user", "content": user_text}],
            **kwargs,
        )
    except anthropic.AuthenticationError:
        raise UserError("Anthropic API key ไม่ถูกต้อง")
    except anthropic.RateLimitError:
        raise UserError("Claude ถูกจำกัดการใช้งานชั่วคราว ลองใหม่อีกสักครู่")
    except anthropic.APIStatusError as e:
        raise UserError(f"Claude API {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        raise UserError("เชื่อมต่อ Claude ไม่ได้ ลองใหม่")
    if res.stop_reason == "refusal":
        why = getattr(res.stop_details, "explanation", "") if res.stop_details else ""
        raise UserError(f"Claude ปฏิเสธคำขอนี้ {why}")
    if res.stop_reason == "max_tokens":
        raise UserError("บทยาวเกิน ลองลดความยาว")
    if not res.parsed_output:
        raise UserError("Claude ตอบไม่ตรงรูปแบบ ลองใหม่")
    return res.parsed_output


def split_local(script: str) -> Script:
    parts = [t.strip() for t in re.split(r"\n\s*\n", script) if t.strip()]
    return Script(title=(parts[0][:40] if parts else "บทใหม่"),
                  scenes=[Scene(narration=t, caption="", visual="") for t in parts])


# ---------------------------------------------------------------- ElevenLabs
ELEVEN = "https://api.elevenlabs.io/v1"


def _eleven_key() -> str:
    k = os.environ.get("ELEVENLABS_API_KEY", "")
    if not k:
        raise UserError("ยังไม่ได้ใส่ ELEVENLABS_API_KEY (Colab: เมนู 🔑 Secrets)")
    return k


def _eleven_check(r: requests.Response) -> requests.Response:
    if r.ok:
        return r
    try:
        d = r.json().get("detail", r.text)
        msg = d.get("message", d) if isinstance(d, dict) else d
    except ValueError:
        msg = r.text
    raise UserError(f"ElevenLabs {r.status_code}: {msg}")


def list_voices() -> list[tuple[str, str]]:
    r = _eleven_check(requests.get(f"{ELEVEN}/voices", headers={"xi-api-key": _eleven_key()}, timeout=30))
    return [(f"{v['name']} ({v['voice_id'][:6]})", v["voice_id"]) for v in r.json()["voices"]]


def tts(text: str, voice_id: str, model_id: str) -> bytes:
    if not voice_id:
        raise UserError("ยังไม่ได้เลือกเสียง")
    r = requests.post(
        f"{ELEVEN}/text-to-speech/{voice_id}",
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": _eleven_key(), "Content-Type": "application/json", "Accept": "audio/mpeg"},
        json={"text": text, "model_id": model_id},
        timeout=180,
    )
    return _eleven_check(r).content


# ---------------------------------------------------------------- video
_MARKS = re.compile(r"[ัิ-ฺ็-๎]")


def vis_len(s: str) -> int:
    """ความกว้างคร่าวๆ (ไม่นับสระบน/ล่าง วรรณยุกต์)"""
    return len(_MARKS.sub("", s))


def _words(text: str) -> list[str]:
    try:
        from pythainlp.tokenize import word_tokenize
        return word_tokenize(text, keep_whitespace=True)
    except ImportError:  # ไม่มี pythainlp: ตัดทีละตัวอักษร (ไม่สวยแต่ใช้ได้)
        return list(text)


def wrap_text(text: str, max_chars: int) -> list[str]:
    lines, line = [], ""
    for w in _words(re.sub(r"\s+", " ", text).strip()):
        if line and vis_len(line + w) > max_chars:
            lines.append(line.strip())
            line = w.lstrip()
        else:
            line += w
    if line.strip():
        lines.append(line.strip())
    return lines


def _ass_time(t: float) -> str:
    cs = round(t * 100)
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _ass_color(h: str) -> str:
    h = h.lstrip("#")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def build_ass(text: str, duration: float, W: int, H: int, color: str) -> str:
    max_chars = 14 if W < H else 18 if W == H else 30
    lines = wrap_text(text, max_chars)
    chunks = [lines[i:i + 2] for i in range(0, len(lines), 2)]
    total = sum(vis_len("".join(c)) for c in chunks) or 1
    t, events = 0.0, []
    for c in chunks:
        d = vis_len("".join(c)) / total * duration
        body = "\\N".join(x.replace("\\", "＼").replace("{", "").replace("}", "") for x in c)
        events.append(f"Dialogue: 0,{_ass_time(t)},{_ass_time(t + d)},Main,,0,0,0,,{body}")
        t += d
    fs = round(min(W, H) * 0.095)
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{FONT},{fs},{_ass_color(color)},&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,{round(fs / 12)},2,2,60,60,{round(H * 0.14)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(events) + "\n"


def _run(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise UserError(f"{cmd[0]} ล้มเหลว:\n{r.stderr[-2000:]}")
    return r.stdout


def audio_duration(f: Path) -> float:
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)])
    return float(out.strip() or 0)


def render_scene(scene: dict, d: Path, i: int, W: int, H: int, style: dict) -> Path:
    audio = d / scene["audio"] if scene.get("audio") else None
    image = d / scene["image"] if scene.get("image") else None
    text = (scene.get("caption") or scene.get("narration") or "").strip()
    alen = audio_duration(audio) if audio else 0
    dur = max(2.0, (alen or vis_len(scene.get("narration") or text) / 12) + 0.4)
    frames = int(dur * FPS + 0.999)
    ass = d / f"scene_{i}.ass"
    ass.write_text(build_ass(text, dur, W, H, style["textColor"]), "utf-8")
    out = d / f"scene_{i}.mp4"

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if image:
        cmd += ["-i", str(image)]
        vf = (f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,crop={W*2}:{H*2},"
              f"zoompan=z='min(zoom+0.0006,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
              f":d={frames}:s={W}x{H}:fps={FPS}")
    else:
        cmd += ["-f", "lavfi", "-i", f"color=c={style['bgColor']}:s={W}x{H}:r={FPS}:d={dur}"]
        vf = "null"
    cmd += ["-i", str(audio)] if audio else ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    ass_esc = str(ass).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    vf += f",ass='{ass_esc}':fontsdir='{FONTS_DIR}',format=yuv420p"
    cmd += [
        "-filter_complex", f"[0:v]{vf}[v];[1:a]apad,aresample=44100,aformat=channel_layouts=stereo[a]",
        "-map", "[v]", "-map", "[a]", "-t", f"{dur:.2f}", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100", str(out),
    ]
    _run(cmd)
    return out


def render_project(p: dict, style: dict, progress=None) -> Path:
    W, H = SIZES.get(style.get("aspect"), SIZES["9:16"])
    d = pdir(p["id"])
    parts = []
    n = len(p["scenes"])
    for i, s in enumerate(p["scenes"]):
        if progress:
            progress(i / (n + 1), desc=f"เรนเดอร์ฉาก {i + 1}/{n}")
        parts.append(render_scene(s, d, i, W, H, style))
    if progress:
        progress(n / (n + 1), desc="ต่อคลิป")
    lst = d / "concat.txt"
    lst.write_text("\n".join(f"file '{x.name}'" for x in parts), "utf-8")
    out = d / "output.mp4"
    _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
          "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out)])
    for x in parts:
        x.unlink(missing_ok=True)
    return out


# ---------------------------------------------------------------- UI helpers
TYPE_ICON = {"simple": "📝", "drama": "🎭", "viral": "🎥", "files": "📦"}
TYPE_NAME = {"simple": "ข้อความ (บท+สี+ซับ)", "drama": "ละครการ์ตูน", "viral": "คลิปไวรัลจากฟุตเทจจริง",
             "files": "โฟลเดอร์ (ยังไม่รองรับการเรนเดอร์)"}


def _skill_choices():
    """เฉพาะเทมเพลตข้อความ (ใช้กับโปรเจกต์แบบบท/ฉาก)"""
    return [("(ไม่ใช้เทมเพลต)", "")] + [(s["name"], s["id"]) for s in list_skills()]


def _template_choices():
    return [(f"{TYPE_ICON.get(t['type'], '📦')} {t['name']}", t["id"]) for t in list_templates()]


def _drama_choices(tid=None):
    ps = [p for p in list_projects("drama") if not tid or p.get("templateId") == tid]
    return [(f"{p['title'][:50]} · {p['updatedAt'][5:16]}", p["id"]) for p in ps]


def _project_choices():
    return [(f"{p['title'][:50]} · {len(p['scenes'])} ฉาก · {p['updatedAt'][5:16]}", p["id"]) for p in list_projects("simple")]


_voice_cache: list[tuple[str, str]] = []


def _voice_choices(current: str = ""):
    ch = [("(ค่าเริ่มต้น)", "")] + list(_voice_cache)
    if current and current not in [v for _, v in ch]:
        ch.append((current, current))
    return ch


def _df(p: dict) -> list[list[str]]:
    return [[s["narration"], s.get("caption", ""), s.get("visual", "")] for s in p["scenes"]]


def _status(p: dict) -> str:
    rows = ["| ฉาก | เสียง | ภาพ |", "|---|---|---|"]
    for i, s in enumerate(p["scenes"], 1):
        rows.append(f"| {i} | {'✅' if s.get('audio') else '—'} | {'🖼️' if s.get('image') else '—'} |")
    return "\n".join(rows)


def _rows(df) -> list[list[str]]:
    """แปลงค่าจาก gr.Dataframe (pandas หรือ list) เป็น list ของแถว"""
    if df is None:
        return []
    if hasattr(df, "values"):
        df = df.values.tolist()
    return [[("" if c is None else str(c)) for c in (list(r) + ["", "", ""])[:3]] for r in df]


def apply_table(p: dict, df) -> dict:
    """อัปเดตฉากจากตาราง: เสียงเดิมใช้ต่อได้ถ้าบทพากย์ไม่เปลี่ยน ภาพผูกกับลำดับแถว"""
    by_text = {s["narration"]: s for s in p["scenes"] if s.get("audio")}
    old = p["scenes"]
    new = []
    for i, (nar, cap, vis) in enumerate(_rows(df)):
        if not (nar.strip() or cap.strip() or vis.strip()):
            continue
        keep = by_text.get(nar)
        new.append({
            "narration": nar, "caption": cap, "visual": vis,
            "audio": keep["audio"] if keep else None, "voiceId": keep.get("voiceId") if keep else None,
            "image": old[i]["image"] if i < len(old) else None,
        })
    p["scenes"] = new
    return p


# ---------------------------------------------------------------- UI
def build_app() -> gr.Blocks:
    def err(fn):
        import functools

        @functools.wraps(fn)  # คง signature ไว้ให้ Gradio ส่ง gr.Progress เข้ามาได้
        def wrapped(*a, **k):
            try:
                return fn(*a, **k)
            except (UserError, D.DramaError, V.ViralError) as e:
                raise gr.Error(str(e), duration=None)
        return wrapped

    with gr.Blocks(title="Clip Studio") as app:
        gr.Markdown("# 🎬 Clip Studio\nเทมเพลต → บท → เสียงพากย์ → คลิป MP4")
        pid = gr.State("")

        with gr.Tabs() as tabs:
            # ---------------- สร้างคลิป
            with gr.Tab("สร้างคลิป", id="create"):
                with gr.Row():
                    c_skill = gr.Dropdown([("(ไม่ใช้เทมเพลต)", "")] + _template_choices(), value="",
                                          label="เทมเพลต (เปลี่ยนแนวคลิปได้ที่นี่)", scale=4)
                    c_tinfo = gr.Markdown()
                with gr.Group(visible=True) as g_simple:
                    c_mode = gr.Radio(
                        [("ให้ Claude คิดเนื้อเรื่อง", "ai"), ("วางบทเอง (Claude แบ่งฉาก)", "own-ai"),
                         ("วางบทเอง (ย่อหน้าละฉาก ไม่ใช้ AI ฟรี)", "own")],
                        value="ai", label="ที่มาของบท")
                    c_model = gr.Dropdown(list(MODELS.items()), value="claude-sonnet-5-5", label="โมเดล Claude")
                    with gr.Group(visible=True) as g_ai:
                        c_topic = gr.Textbox(label="หัวข้อ / ไอเดีย", placeholder="เช่น ตำนานผีบ้านร้างริมคลอง")
                        c_dur = gr.Slider(15, 300, value=60, step=5, label="ความยาว (วินาที)")
                        c_extra = gr.Textbox(label="เพิ่มเติม (ไม่บังคับ)", lines=2)
                    c_script = gr.Textbox(label="วางบท (เว้นบรรทัดว่างระหว่างฉาก)", lines=12, visible=False)
                    c_go = gr.Button("สร้างบท →", variant="primary")

                with gr.Group(visible=False) as g_drama:
                    gr.Markdown("### 🎭 ละครการ์ตูน\n1) วาง series.json → 2) วางบทตอน epNN.json → 3) ตรวจบท → "
                                "4) เลือกเสียง → 5) เช็กฉาก ให้ Claude วาด แล้วอนุมัติ → 6) ดูภาพตัวอย่าง → เรนเดอร์")
                    d_pid = gr.State("")
                    with gr.Row():
                        d_proj = gr.Dropdown([], label="ซีรีส์ (ว่าง = สร้างใหม่จาก series.json)", scale=4)
                        d_del = gr.Button("🗑️ ลบซีรีส์", variant="stop", scale=1)
                    with gr.Accordion("1) series.json (ตัวละคร / สถานที่)", open=True) as d_acc_series:
                        d_series = gr.Code(language="json", label="วาง series.json", lines=10, max_lines=18)
                        d_series_file = gr.File(file_types=[".json"], label="หรือลากไฟล์ series.json มาวาง")
                        d_save_series = gr.Button("💾 บันทึก series.json")
                    with gr.Accordion("2) บทตอน epNN.json", open=True):
                        d_ep_text = gr.Code(language="json", label="วางบทตอน (epNN.json)", lines=10, max_lines=18)
                        d_ep_file = gr.File(file_types=[".json"], label="หรือลากไฟล์ epNN.json มาวาง")
                        with gr.Row():
                            d_save_ep = gr.Button("💾 บันทึกตอน")
                            d_ep = gr.Dropdown([], label="ตอนที่จะทำ", scale=2)
                            d_sec = gr.Number(value=90, precision=0, label="ความยาวเป้าหมาย (วินาที)")
                            d_check = gr.Button("✅ ตรวจบท")
                        d_check_out = gr.Textbox(label="ผลตรวจบท", lines=6, interactive=False)
                    with gr.Accordion("3) เสียงพากย์", open=True):
                        with gr.Row():
                            d_engine = gr.Radio([("edge-tts (ฟรี)", "edge"), ("ElevenLabs", "eleven")], value="edge",
                                                label="เสียงพากย์")
                            d_emodel = gr.Dropdown(ELEVEN_MODELS, value="eleven_v3", label="โมเดล ElevenLabs")
                        d_refresh = gr.State(0)

                        @gr.render(inputs=[d_pid, d_engine, d_refresh])
                        def _dynamic(pid_, engine, _r):
                            if not pid_ or not (pdir(pid_) / "series.json").exists():
                                gr.Markdown("_บันทึก series.json ก่อน แล้วช่องเลือกเสียง/ฉากจะขึ้นที่นี่_")
                                return
                            p = get_project(pid_)
                            series = D.load(pdir(pid_), "series.json")
                            if engine == "eleven":
                                gr.Markdown("**เสียง ElevenLabs ของแต่ละตัวละคร** (ว่าง = ใช้เสียงเริ่มต้น / เสียงผู้เล่า)")
                                vm = p.get("voices", {}).get("map", {})
                                with gr.Row():
                                    for cid, name, vt in [("_default", "เสียงเริ่มต้น", "")] + D.voice_slots(series):
                                        cur = p.get("voices", {}).get("default", "") if cid == "_default" else vm.get(cid, "")
                                        dd = gr.Dropdown(_voice_choices(cur), value=cur, allow_custom_value=True,
                                                         label=f"{name}" + (f" ({vt})" if vt else ""), min_width=180)
                                        dd.change(err(lambda v, cid=cid: _set_voice(pid_, cid, v)), dd, None)
                            else:
                                gr.Markdown("_edge-tts ใช้เสียงไทยของ Microsoft ตามประเภทเสียงใน series.json (ฟรี ต้องมีอินเทอร์เน็ต)_")
                    with gr.Accordion("4) ฉากหลัง: เช็กฉากที่ยังไม่มี → Claude วาด → ดูแล้วอนุมัติ", open=True):
                        with gr.Row():
                            d_bgmodel = gr.Dropdown(list(MODELS.items()), value="claude-sonnet-5-5",
                                                    label="โมเดล Claude ที่วาดฉาก (ต้องมี ANTHROPIC_API_KEY)")
                            d_review = gr.Checkbox(True, label="ให้ Claude ดูภาพตัวเองแล้วปรับอีกรอบก่อนโชว์ (สวยขึ้น แพงขึ้นเล็กน้อย)")

                        @gr.render(inputs=[d_pid, d_refresh])
                        def _bg_panel(pid_, r):
                            if not pid_ or not (pdir(pid_) / "series.json").exists():
                                gr.Markdown("_บันทึก series.json + บทตอนก่อน แล้วรายการฉากจะขึ้นที่นี่_")
                                return
                            p = get_project(pid_)
                            series = D.load(pdir(pid_), "series.json")
                            lib_dir = _lib_dir(p)
                            lib = D.library(lib_dir)
                            sts = D.bg_status(pdir(pid_), series, p.get("bgs", {}), lib_dir)
                            used = [x for x in sts if x["used"]]
                            missing = [x for x in used if x["state"] == "missing"]
                            pending = [x for x in sts if x["cand"]]
                            gr.Markdown(
                                f"**สถานที่ในบท {len(used)} ที่** · มีฉากแล้ว {len(used) - len(missing)} · "
                                f"⚠️ ยังไม่มี {len(missing)}" + (f" ({', '.join(x['place'].get('name', x['place']['id']) for x in missing)})" if missing else "")
                                + (f" · 🆕 รออนุมัติ {len(pending)}" if pending else "")
                                + "\n\n_ฉากที่ยังไม่มีตอนเรนเดอร์จะใช้ฉากสำเร็จรูปแทน_")
                            if missing:
                                b_all = gr.Button(f"🎨 ให้ Claude วาดฉากที่ยังไม่มีทั้งหมด ({len(missing)} ที่)", variant="primary")
                                b_all.click(err(lambda bm, rv, rr, progress=gr.Progress(): _draw_many(pid_, [x["place"] for x in missing], bm, rv, progress) or rr + 1),
                                            [d_bgmodel, d_review, d_refresh], d_refresh)
                            src = ([("🎨 ฉากที่ Claude วาด (อนุมัติแล้ว)", "claude")]
                                   + [(f"📚 {it['name']}" + (f" · {it['series']}" if it.get("series") else ""), "lib:" + it["id"]) for it in lib]
                                   + [(v, k) for k, v in D.PRESETS.items() if k != "claude"])
                            label = {"approved": "✅ ฉากที่ Claude วาด", "library": "📚 จากคลังฉาก", "preset": "🧱 ฉากสำเร็จรูป",
                                     "missing": "⚠️ ยังไม่มีฉาก"}
                            def _row(st):
                                pl = st["place"]
                                plid = pl["id"]
                                with gr.Group():
                                    gr.Markdown(f"#### {pl.get('name', plid)} · {label[st['state']]}"
                                                + ("" if st["used"] else " · _(บทตอนที่มียังไม่ใช้)_"))
                                    with gr.Row():
                                        if st["preview"]:
                                            gr.Image(st["preview"], label="ฉากที่ใช้อยู่", height=320, interactive=False)
                                        if st["cand"]:
                                            gr.Image(st["cand"], label="🆕 Claude วาดใหม่ (รออนุมัติ)", height=320, interactive=False)
                                        with gr.Column():
                                            dd = gr.Dropdown(src, value=st["key"], label="ใช้ฉากจาก")
                                            dd.change(err(lambda v, rr, plid=plid: _set_bg(pid_, plid, v) or rr + 1), [dd, d_refresh], d_refresh)
                                            if st["suggest"]:
                                                sg = st["suggest"]
                                                b_sg = gr.Button(f"💡 ใช้ฉากในคลังที่คล้ายกัน: {sg['name']}")
                                                b_sg.click(err(lambda rr, plid=plid, lid=sg["id"]: _set_bg(pid_, plid, "lib:" + lid) or rr + 1),
                                                           d_refresh, d_refresh)
                                            fb = gr.Textbox(label="อยากให้ปรับอะไร (ไม่บังคับ)", placeholder="เช่น ให้เป็นกลางคืน มีโต๊ะพับสีฟ้า")
                                            b_draw = gr.Button("🔁 ให้ Claude วาดใหม่" if (st["cand"] or st["preview"]) else "🎨 ให้ Claude วาด")
                                            b_draw.click(err(lambda f, bm, rv, rr, plid=plid, progress=gr.Progress():
                                                             _draw_one(pid_, plid, bm, rv, f, progress) or rr + 1),
                                                         [fb, d_bgmodel, d_review, d_refresh], d_refresh)
                                            if st["cand"]:
                                                b_ok = gr.Button("✅ ใช้ฉากนี้ + เก็บเข้าคลัง", variant="primary")
                                                b_ok.click(err(lambda rr, plid=plid: _approve(pid_, plid) or rr + 1), d_refresh, d_refresh)
                                                b_no = gr.Button("❌ ไม่เอา")
                                                b_no.click(err(lambda rr, plid=plid: D.discard_candidate(pdir(pid_), plid) or rr + 1),
                                                           d_refresh, d_refresh)

                            for st in [x for x in sts if x["used"]]:
                                _row(st)
                            unused = [x for x in sts if not x["used"]]
                            if unused:
                                with gr.Accordion(f"สถานที่ที่บทยังไม่ใช้ ({len(unused)})", open=False):
                                    for st in unused:
                                        _row(st)
                    with gr.Accordion("5) ภาพตัวอย่าง & เรนเดอร์", open=True):
                        with gr.Row():
                            d_still_n = gr.Number(value=1, precision=0, minimum=1, label="ช็อตที่")
                            d_still = gr.Button("🖼️ ดูภาพตัวอย่างช็อตนี้")
                            d_render = gr.Button("🎬 เรนเดอร์ทั้งตอน", variant="primary")
                            d_force = gr.Checkbox(False, label="เรนเดอร์แม้บทยังไม่ผ่านการตรวจ (ทดลอง)")
                        d_img = gr.Image(label="ภาพตัวอย่าง", height=560, interactive=False)
                        d_video = gr.Video(label="คลิป", height=640)
                        d_log = gr.Textbox(label="บันทึกการทำงาน", lines=6, interactive=False)

                with gr.Group(visible=False) as g_viral:
                    gr.Markdown("### 🎥 คลิปไวรัลจากฟุตเทจจริง\n1) วางบท → 2) เสียงพากย์ → 3) ฟุตเทจ → 4) ตัดต่อ → 5) เรนเดอร์ "
                                "(ได้คลิปมีเพลง/ไม่มีเพลง + ปก 2 แบบ + เครดิต)")
                    v_pid = gr.State("")
                    with gr.Row():
                        v_proj = gr.Dropdown([], label="คลิป (ว่าง = สร้างใหม่ตอนบันทึกบท)", scale=3)
                        v_name = gr.Textbox(label="ชื่อคลิป", placeholder="เช่น ละมั่งหนีจระเข้", scale=2)
                        v_del = gr.Button("🗑️ ลบคลิป", variant="stop", scale=1)
                    with gr.Accordion("1) บทพากย์", open=True):
                        v_script = gr.Textbox(lines=12, label="วางบท (บรรทัดแรกเป็นแท็กเสียง [ผู้เล่า: ...] ได้ · บรรทัดสุดท้ายชวนกดไลค์)",
                                              placeholder="[ผู้เล่า: เสียงผู้ชายวัยสามสิบ เล่าเรื่องตื่นเต้น จังหวะเร็ว]\nละมั่งตัวนี้ อยู่ห่างจากปากจระเข้ แค่ไม่กี่นิ้ว\n...")
                        with gr.Row():
                            v_sec = gr.Number(value=60, precision=0, label="ความยาวเป้าหมาย (วินาที)")
                            v_save = gr.Button("💾 บันทึกบท", variant="primary")
                        v_sinfo = gr.Markdown()
                    with gr.Accordion("2) เสียงพากย์", open=True):
                        with gr.Row():
                            v_voice = gr.Dropdown(_voice_choices(V.DEFAULT_VOICE), value=V.DEFAULT_VOICE, allow_custom_value=True,
                                                  label="เสียง ElevenLabs (ค่าเริ่มต้นตามเทมเพลต)")
                            v_vmodel = gr.Dropdown(["eleven_v3", "eleven_v4", "eleven_multilingual_v2"], value="eleven_v3",
                                                   label="โมเดลเสียง")
                            v_tts = gr.Button("🎙️ สร้างเสียงจากบท")
                        v_vofile = gr.File(file_types=["audio"], label="หรือลากไฟล์เสียงที่ทำเองมาวาง")
                        v_audio = gr.Audio(label="เสียงที่ตัดช่วงเงียบแล้ว", interactive=False)
                        v_vinfo = gr.Markdown()
                    with gr.Accordion("3) ฟุตเทจ", open=True):
                        with gr.Row():
                            v_model = gr.Dropdown(list(MODELS.items()), value="claude-sonnet-5-5", label="โมเดล Claude")
                            v_find = gr.Button("🔎 Claude หาฟุตเทจ + โหลดให้", variant="primary")
                        v_upload = gr.File(file_count="multiple", file_types=["video"], label="หรืออัปโหลดคลิปเอง (ใช้เมื่อ YouTube บล็อก Colab)")
                        v_flist = gr.Markdown()
                        with gr.Row():
                            v_rmname = gr.Dropdown([], label="ลบฟุตเทจ", scale=3)
                            v_rm = gr.Button("🗑️ ลบไฟล์นี้", scale=1)
                    with gr.Accordion("4) ตัดต่อ", open=True):
                        with gr.Row():
                            v_auto = gr.Button("✂️ ตัดอัตโนมัติ (ฟรี: ไม่มีวงกลม/ลูกศร)")
                            v_ccut = gr.Button("🎬 Claude ตัดต่อเต็มสไตล์", variant="primary")
                            v_review = gr.Checkbox(True, label="ให้ Claude ดูพรีวิวแล้วแก้อีกรอบ")
                        v_preview = gr.Image(label="พรีวิว (ตรวจตัวชี้/ซับ ก่อนเรนเดอร์)", interactive=False)
                    with gr.Accordion("5) เรนเดอร์", open=True):
                        v_render = gr.Button("🎬 เรนเดอร์คลิป (~3-5 นาที)", variant="primary")
                        with gr.Row():
                            v_out1 = gr.Video(label="มีเพลง", height=560)
                            v_out2 = gr.Video(label="ไม่มีเพลง", height=560)
                        v_covers = gr.Gallery(label="ปก + contact sheet", columns=3, height=420)
                        v_credits = gr.Textbox(label="เครดิตฟุตเทจ (ใส่ตอนโพสต์)", lines=5, interactive=False)
                    v_log = gr.Textbox(label="บันทึกการทำงาน", lines=6, interactive=False)

            # ---------------- แก้บท / พากย์ / เรนเดอร์
            with gr.Tab("แก้บท (แบบข้อความ)", id="edit"):
                with gr.Row():
                    e_proj = gr.Dropdown(_project_choices(), label="โปรเจกต์", scale=4)
                    e_reload = gr.Button("🔄", scale=0, min_width=50)
                    e_del = gr.Button("🗑️ ลบโปรเจกต์", variant="stop", scale=1)
                e_title = gr.Textbox(label="ชื่อคลิป")
                with gr.Row():
                    e_skill = gr.Dropdown(_skill_choices(), label="เทมเพลต (สี/ซับ/สัดส่วน)")
                    e_voice = gr.Dropdown(_voice_choices(), label="เสียงพากย์", allow_custom_value=True)
                    e_emodel = gr.Dropdown(ELEVEN_MODELS, value="eleven_v3", label="โมเดลเสียง")
                e_table = gr.Dataframe(headers=["บทพากย์", "ข้อความบนจอ (ว่าง=ใช้บทพากย์)", "ภาพที่ควรใช้"],
                                       datatype=["str", "str", "str"], column_count=(3, "fixed"),
                                       interactive=True, wrap=True, label="ฉาก (แก้ในตารางได้ เพิ่มแถว = เพิ่มฉาก)")
                with gr.Row():
                    e_save = gr.Button("💾 บันทึกบท")
                    e_tts = gr.Button("🎙️ พากย์ทุกฉาก", variant="primary")
                    e_render = gr.Button("🎬 เรนเดอร์คลิป", variant="primary")
                with gr.Row():
                    with gr.Column():
                        e_status = gr.Markdown()
                    with gr.Column():
                        e_scene = gr.Number(value=1, precision=0, minimum=1, label="ฉากที่")
                        e_img = gr.Image(type="filepath", label="ภาพประกอบฉากนี้ (อัปโหลดแล้วบันทึกอัตโนมัติ)")
                        e_audio = gr.Audio(label="เสียงฉากนี้", interactive=False)
                        with gr.Row():
                            e_tts1 = gr.Button("🎙️ พากย์ฉากนี้ใหม่")
                            e_rmimg = gr.Button("ลบภาพ")
                e_video = gr.Video(label="คลิป", height=640)

            # ---------------- เทมเพลต
            with gr.Tab("เทมเพลต", id="skills"):
                s_files = gr.File(file_count="multiple", file_types=[".zip", ".md", ".txt", ".markdown"],
                                  label="➕ เพิ่มเทมเพลต: ลาก .zip (ทั้งโฟลเดอร์สกิล) หรือ .md/.txt มาวาง ได้หลายไฟล์")
                s_pick = gr.Dropdown(_template_choices(), label="เลือกเทมเพลต (ว่าง = สร้างเทมเพลตข้อความใหม่)")
                s_info = gr.Markdown()
                with gr.Row():
                    s_dup = gr.Button("📄 ทำสำเนา")
                    s_export = gr.Button("⬇️ ดาวน์โหลด .zip")
                    s_del = gr.Button("🗑️ ลบเทมเพลต", variant="stop")
                s_zip = gr.File(label="ไฟล์ดาวน์โหลด", visible=False)
                with gr.Group(visible=False) as g_tfiles:
                    s_add = gr.File(file_count="multiple", label="เพิ่ม/แทนที่ไฟล์ในเทมเพลตนี้ (ไฟล์เดี่ยวหรือ .zip)")
                with gr.Group(visible=True) as g_tform:
                    s_name = gr.Textbox(label="ชื่อเทมเพลต")
                    s_desc = gr.Textbox(label="คำอธิบายสั้นๆ")
                    s_content = gr.Textbox(label="คำสั่ง/สไตล์ให้ Claude ทำตาม", lines=12)
                    with gr.Row():
                        s_aspect = gr.Dropdown(list(SIZES), value="9:16", label="สัดส่วนคลิป")
                        s_bg = gr.ColorPicker("#111827", label="สีพื้น")
                        s_fg = gr.ColorPicker("#FFFFFF", label="สีซับ")
                        s_voice = gr.Dropdown(_voice_choices(), value="", label="เสียงพากย์", allow_custom_value=True)
                    s_save = gr.Button("💾 บันทึกเทมเพลต", variant="primary")

            # ---------------- ตั้งค่า
            with gr.Tab("ตั้งค่า", id="settings"):
                gr.Markdown(
                    f"ข้อมูลเก็บที่ `{DATA}`\n\n"
                    f"- Anthropic key: {'✅' if os.environ.get('ANTHROPIC_API_KEY') else '❌ ไม่มี (ใช้ได้แค่โหมดวางบทเอง)'}\n"
                    f"- ElevenLabs key: {'✅' if os.environ.get('ELEVENLABS_API_KEY') else '❌ ไม่มี'}\n\n"
                    "ใส่ key ที่เมนู 🔑 Secrets ของ Colab แล้วรัน cell ใหม่")
                st_load = gr.Button("โหลดรายชื่อเสียง ElevenLabs")
                st_out = gr.Markdown()

        # ===== events: create
        c_mode.change(lambda m: (gr.update(visible=m == "ai"), gr.update(visible=m != "ai"),
                                 gr.update(visible=m != "own")),
                      c_mode, [g_ai, c_script, c_model])

        @err
        def create(skill_id, mode, model, topic, dur, extra, script):
            skill = get_skill(skill_id)
            if mode == "ai":
                if not topic.strip():
                    raise UserError("ใส่หัวข้อก่อน")
                sc = ask_claude(model, skill, f"หัวข้อ/ไอเดีย: {topic}\nความยาวคลิปประมาณ {int(dur)} วินาที\n"
                                              + (f"เพิ่มเติม: {extra}\n" if extra.strip() else "") + "เขียนบทให้ครบทุกฉาก")
            else:
                if not script.strip():
                    raise UserError("วางบทก่อน")
                sc = split_local(script) if mode == "own" else ask_claude(
                    model, skill, "นี่คือบทที่ผู้ใช้เขียนเอง ให้แบ่งเป็นฉาก ใส่ caption และ visual ให้แต่ละฉาก\n"
                    "ห้ามแก้ไข เพิ่ม หรือตัดคำในบทพากย์ narration ต้องเป็นข้อความเดิมเรียงต่อกันครบทุกคำ\n\n" + script)
            p = save_project({
                "id": _new_id(), "createdAt": _now(), "title": sc.title, "skillId": skill_id or None,
                "voiceId": (skill or {}).get("style", {}).get("voiceId", ""),
                "scenes": [{**s.model_dump(), "audio": None, "voiceId": None, "image": None} for s in sc.scenes],
            })
            gr.Info("ได้บทแล้ว ตรวจ/แก้ได้เลย")
            return gr.update(choices=_project_choices(), value=p["id"]), gr.Tabs(selected="edit")

        c_go.click(create, [c_skill, c_mode, c_model, c_topic, c_dur, c_extra, c_script], [e_proj, tabs])

        # ===== events: editor
        def load(pid_):
            if not pid_:
                return "", "", gr.update(), gr.update(), [], "", None, None, None
            p = get_project(pid_)
            out = pdir(pid_) / "output.mp4"
            return (pid_, p["title"], gr.update(choices=_skill_choices(), value=p.get("skillId") or ""),
                    gr.update(choices=_voice_choices(p.get("voiceId", "")), value=p.get("voiceId", "")),
                    _df(p), _status(p), str(out) if out.exists() else None) + _scene_view(p, 1)

        def _scene_view(p, n):
            i = int(n or 1) - 1
            if not (0 <= i < len(p["scenes"])):
                return None, None
            s, d = p["scenes"][i], pdir(p["id"])
            return (str(d / s["image"]) if s.get("image") else None,
                    str(d / s["audio"]) if s.get("audio") else None)

        load_outs = [pid, e_title, e_skill, e_voice, e_table, e_status, e_video, e_img, e_audio]
        e_proj.change(err(load), e_proj, load_outs)
        e_reload.click(lambda: gr.update(choices=_project_choices()), None, e_proj)
        e_scene.change(err(lambda pid_, n: _scene_view(get_project(pid_), n) if pid_ else (None, None)),
                       [pid, e_scene], [e_img, e_audio])

        def _save(pid_, title, skill_id, voice, df):
            if not pid_:
                raise UserError("เลือกโปรเจกต์ก่อน")
            p = get_project(pid_)
            p.update(title=title, skillId=skill_id or None, voiceId=voice or "")
            return save_project(apply_table(p, df))

        @err
        def save(pid_, title, skill_id, voice, df):
            p = _save(pid_, title, skill_id, voice, df)
            gr.Info("บันทึกแล้ว")
            return _df(p), _status(p)

        edit_in = [pid, e_title, e_skill, e_voice, e_table]
        e_save.click(save, edit_in, [e_table, e_status])

        def _target_voice(p):
            vid = p.get("voiceId") or (get_skill(p.get("skillId")) or {}).get("style", {}).get("voiceId", "")
            if not vid:
                raise UserError("เลือกเสียงพากย์ก่อน (กดโหลดรายชื่อเสียงในแท็บตั้งค่า)")
            return vid

        def _voice_scene(p, i, vid, emodel):
            s, d = p["scenes"][i], pdir(p["id"])
            name = f"voice_{i}_{int(time.time() * 1000)}.mp3"
            (d / name).write_bytes(tts(s["narration"], vid, emodel))
            if s.get("audio"):
                (d / s["audio"]).unlink(missing_ok=True)
            s.update(audio=name, voiceId=vid)

        @err
        def voice_all(pid_, title, skill_id, voice, df, emodel, n, progress=gr.Progress()):
            p = _save(pid_, title, skill_id, voice, df)
            vid = _target_voice(p)
            # ข้ามฉากที่มีเสียงของ voice นี้อยู่แล้ว (ไม่เสียเครดิตซ้ำ)
            todo = [i for i, s in enumerate(p["scenes"])
                    if s["narration"].strip() and not (s.get("audio") and s.get("voiceId") == vid)]
            for k, i in enumerate(todo):
                progress(k / max(1, len(todo)), desc=f"พากย์ฉาก {i + 1}")
                _voice_scene(p, i, vid, emodel)
                save_project(p)  # บันทึกทีละฉาก เผื่อพังกลางทาง ไม่ต้องเสียเครดิตซ้ำ
            gr.Info(f"พากย์ {len(todo)} ฉาก (ฉากที่มีเสียงแล้วข้าม)")
            return (_status(p),) + _scene_view(p, n)

        e_tts.click(voice_all, edit_in + [e_emodel, e_scene], [e_status, e_img, e_audio])

        @err
        def voice_one(pid_, title, skill_id, voice, df, emodel, n):
            p = _save(pid_, title, skill_id, voice, df)
            i = int(n or 1) - 1
            if not (0 <= i < len(p["scenes"])):
                raise UserError("ไม่มีฉากนี้")
            _voice_scene(p, i, _target_voice(p), emodel)
            save_project(p)
            return (_status(p),) + _scene_view(p, n)

        e_tts1.click(voice_one, edit_in + [e_emodel, e_scene], [e_status, e_img, e_audio])

        @err
        def set_image(pid_, n, path):
            if not pid_ or not path:
                return gr.update()
            p = get_project(pid_)
            i = int(n or 1) - 1
            if not (0 <= i < len(p["scenes"])):
                raise UserError("ไม่มีฉากนี้")
            d = pdir(pid_)
            if Path(path).parent == d:  # ภาพเดิมของฉากนี้ (ตอนโหลด) ไม่ต้องทำอะไร
                return _status(p)
            name = f"img_{i}_{int(time.time() * 1000)}{Path(path).suffix.lower() or '.jpg'}"
            shutil.copy(path, d / name)
            if p["scenes"][i].get("image"):
                (d / p["scenes"][i]["image"]).unlink(missing_ok=True)
            p["scenes"][i]["image"] = name
            save_project(p)
            return _status(p)

        e_img.upload(set_image, [pid, e_scene, e_img], e_status)

        @err
        def rm_image(pid_, n):
            p = get_project(pid_)
            i = int(n or 1) - 1
            if 0 <= i < len(p["scenes"]) and p["scenes"][i].get("image"):
                (pdir(pid_) / p["scenes"][i]["image"]).unlink(missing_ok=True)
                p["scenes"][i]["image"] = None
                save_project(p)
            return _status(p), None

        e_rmimg.click(rm_image, [pid, e_scene], [e_status, e_img])

        @err
        def render(pid_, title, skill_id, voice, df, progress=gr.Progress()):
            p = _save(pid_, title, skill_id, voice, df)
            if not p["scenes"]:
                raise UserError("ยังไม่มีฉาก")
            missing = [str(i + 1) for i, s in enumerate(p["scenes"]) if s["narration"].strip() and not s.get("audio")]
            if missing:
                gr.Warning(f"ฉาก {', '.join(missing)} ยังไม่มีเสียง จะเป็นฉากเงียบ")
            skill = get_skill(p.get("skillId"))
            style = {"aspect": "9:16", "bgColor": "#111827", "textColor": "#FFFFFF", **((skill or {}).get("style", {}))}
            out = render_project(p, style, progress)
            final = out.with_name(f"output_{int(time.time())}.mp4")  # ชื่อใหม่ กัน browser cache
            for old in pdir(pid_).glob("output_*.mp4"):
                old.unlink(missing_ok=True)
            shutil.copy(out, final)
            return str(final)

        e_render.click(render, edit_in, e_video)

        @err
        def del_project(pid_):
            if pid_:
                shutil.rmtree(pdir(pid_), ignore_errors=True)
            return gr.update(choices=_project_choices(), value=None)

        e_del.click(del_project, pid, e_proj)

        # ===== events: templates
        def _tinfo(t):
            if not t:
                return ""
            txt = f"**{TYPE_ICON.get(t['type'], '📦')} {t['name']}** · {TYPE_NAME.get(t['type'], t['type'])}"
            if t.get("description"):
                txt += f"\n\n{t['description'][:300]}"
            if t["type"] != "simple":
                root = template_root(t)
                n = sum(1 for _ in root.rglob("*") if _.is_file())
                txt += f"\n\n📁 {n} ไฟล์ · `{root.relative_to(DATA)}`"
            return txt

        def skill_form(sid):
            t = get_template(sid) if sid else None
            simple = not t or t["type"] == "simple"
            vis = (gr.update(visible=not simple), gr.update(visible=simple))
            if not t or not simple:
                return (_tinfo(t),) + vis + ("", "", "", "9:16", "#111827", "#FFFFFF", gr.update(choices=_voice_choices(), value=""))
            st = t["style"]
            return (_tinfo(t),) + vis + (t["name"], t.get("description", ""), t["content"], st["aspect"], st["bgColor"], st["textColor"],
                                         gr.update(choices=_voice_choices(st.get("voiceId", "")), value=st.get("voiceId", "")))

        skill_fields = [s_name, s_desc, s_content, s_aspect, s_bg, s_fg, s_voice]
        s_pick.change(err(skill_form), s_pick, [s_info, g_tfiles, g_tform] + skill_fields)

        def refresh_skill_dds(value=None):
            return (gr.update(choices=_template_choices(), value=value),
                    gr.update(choices=[("(ไม่ใช้เทมเพลต)", "")] + _template_choices()),
                    gr.update(choices=_skill_choices()))

        tpl_dds = [s_pick, c_skill, e_skill]

        @err
        def save_s(sid, *vals):
            t = get_template(sid) if sid else None
            if t and t["type"] != "simple":
                raise UserError("เทมเพลตแบบโฟลเดอร์แก้ในฟอร์มนี้ไม่ได้ ใช้ช่องเพิ่ม/แทนที่ไฟล์แทน")
            s_ = save_skill(sid or None, *vals)
            gr.Info("บันทึกเทมเพลตแล้ว")
            return refresh_skill_dds(s_["id"])

        s_save.click(save_s, [s_pick] + skill_fields, tpl_dds)

        @err
        def del_s(sid):
            if sid:
                delete_template(sid)
                gr.Info("ลบเทมเพลตแล้ว (โปรเจกต์ที่เคยทำไว้ยังอยู่)")
            return refresh_skill_dds("")

        s_del.click(del_s, s_pick, tpl_dds)

        @err
        def dup_s(sid):
            if not sid:
                raise UserError("เลือกเทมเพลตก่อน")
            t = duplicate_template(sid)
            gr.Info("ทำสำเนาแล้ว")
            return refresh_skill_dds(t["id"])

        s_dup.click(dup_s, s_pick, tpl_dds)

        @err
        def export_s(sid):
            if not sid:
                raise UserError("เลือกเทมเพลตก่อน")
            return gr.update(value=export_template(sid), visible=True)

        s_export.click(export_s, s_pick, s_zip)

        @err
        def upload_s(files):
            last = None
            for f in files or []:
                last = import_template_file(f if isinstance(f, str) else f.name)
            gr.Info(f"เพิ่ม {len(files or [])} เทมเพลตแล้ว")
            return refresh_skill_dds(last["id"] if last else None) + (None,)

        s_files.upload(upload_s, s_files, tpl_dds + [s_files])

        @err
        def add_files(sid, files):
            if not sid:
                raise UserError("เลือกเทมเพลตก่อน")
            t = add_template_files(sid, [f if isinstance(f, str) else f.name for f in files or []])
            gr.Info("เพิ่มไฟล์แล้ว")
            return _tinfo(t), None

        s_add.upload(add_files, [s_pick, s_add], [s_info, s_add])

        # ===== events: drama
        def _set_voice(pid_, cid, v):
            p = get_project(pid_)
            vs = p.setdefault("voices", {})
            if cid == "_default":
                vs["default"] = v or ""
            else:
                vs.setdefault("map", {})[cid] = v or ""
            save_project(p)

        def _set_bg(pid_, place_id, v):
            p = get_project(pid_)
            p.setdefault("bgs", {})[place_id] = v or "auto"
            save_project(p)

        def on_template(tid):
            t = get_template(tid)
            kind = t["type"] if t else "simple"
            info = ""
            if kind == "files":
                info = "⚠️ เทมเพลตนี้ยังไม่มีตัวเชื่อมสำหรับเรนเดอร์ ส่งให้ผู้พัฒนาเพิ่มรองรับก่อน"
            dch = _drama_choices(tid) if kind == "drama" else []
            vch = _viral_choices(tid) if kind == "viral" else []
            return (info, gr.update(visible=kind in ("simple", "files")), gr.update(visible=kind == "drama"),
                    gr.update(visible=kind == "viral"),
                    gr.update(choices=dch, value=dch[0][1] if dch else None),
                    gr.update(choices=vch, value=vch[0][1] if vch else None))

        c_skill.change(err(on_template), c_skill, [c_tinfo, g_simple, g_drama, g_viral, d_proj, v_proj])

        # ===== events: viral
        VSTATE = DATA / "viral_state"

        def _viral_choices(tid=None):
            ps = [p for p in list_projects("viral") if not tid or p.get("templateId") == tid]
            return [(f"{p['title'][:50]} · {p['updatedAt'][5:16]}", p["id"]) for p in ps]

        def _viral_root(pid_):
            p = get_project(pid_)
            t = get_template(p.get("templateId"))
            if not t or t["type"] != "viral":
                raise UserError("ไม่พบเทมเพลตของคลิปนี้ (ถูกลบไปแล้ว?)")
            return p, template_root(t)

        def _flist(pid_):
            fl = V.footage_list(pdir(pid_))
            if not fl:
                return "_ยังไม่มีฟุตเทจ_", gr.update(choices=[], value=None)
            md = "| ไฟล์ | ยาว | ขนาด |\n|---|---|---|\n" + "\n".join(f"| {f['name']} | {f['dur']} วิ | {f['size']} |" for f in fl)
            total = sum(f["dur"] for f in fl)
            return md + f"\n\nรวม {total:.0f} วิ (ควรมีมากกว่าความยาวคลิปพอสมควร เพราะห้ามใช้ภาพซ้ำ)", \
                gr.update(choices=[f["name"] for f in fl], value=None)

        def _vo_view(pid_):
            d = pdir(pid_)
            f = d / "vo-tight.wav"
            if not f.exists():
                return None, "_ยังไม่มีเสียง_"
            dur = V._probe(f)
            return str(f), f"✅ เสียงยาว {dur:.1f} วิ (ตัดช่วงเงียบแล้ว)"

        def _outputs(pid_):
            fin = pdir(pid_) / "final"
            vids = sorted(str(x) for x in fin.glob("*.mp4") if "preview" not in x.name) if fin.exists() else []
            withm = next((x for x in vids if "ไม่มีเพลง" not in x), None)
            nom = next((x for x in vids if "ไม่มีเพลง" in x), None)
            gal = (sorted(str(x) for x in fin.glob("ปก*.jpg")) + ([str(fin / "contact-sheet.jpg")] if (fin / "contact-sheet.jpg").exists() else [])) if fin.exists() else []
            return withm, nom, gal

        def v_load(pid_):
            if not pid_:
                return ("", "", "", None, "", "_ยังไม่มีฟุตเทจ_", gr.update(choices=[]), None, None, None, [], "")
            p, root = _viral_root(pid_)
            d = pdir(pid_)
            script = (d / "script.txt").read_text("utf-8") if (d / "script.txt").exists() else ""
            audio, vinfo = _vo_view(pid_)
            fl, rmdd = _flist(pid_)
            prev = d / "out" / "preview.jpg"
            o1, o2, gal = _outputs(pid_)
            cred = (d / "footage" / "credits.txt").read_text("utf-8") if (d / "footage" / "credits.txt").exists() else ""
            return (pid_, p.get("title", ""), script, audio, vinfo, fl, rmdd, str(prev) if prev.exists() else None, o1, o2, gal, cred)

        v_load_outs = [v_pid, v_name, v_script, v_audio, v_vinfo, v_flist, v_rmname, v_preview, v_out1, v_out2, v_covers, v_credits]
        v_proj.change(err(v_load), v_proj, v_load_outs)

        @err
        def v_save_(tid, pid_, name, text, sec):
            t = get_template(tid)
            if not t or t["type"] != "viral":
                raise UserError("เลือกเทมเพลตคลิปไวรัลก่อน")
            if not pid_:
                pid_ = _new_id()
                p = {"id": pid_, "type": "viral", "templateId": tid, "createdAt": _now(), "title": (name or "คลิปใหม่").strip()}
                save_project(p)
                V.new_project(template_root(t), pdir(pid_), p["title"])
            else:
                p = get_project(pid_)
                if name.strip():
                    p["title"] = name.strip()
            est = V.save_script(pdir(pid_), text)
            p["target"] = int(sec or 60)
            save_project(p)
            msg = f"บันทึกแล้ว · บทยาวประมาณ **{est} วิ** (เป้า {int(sec or 60)} วิ)"
            if abs(est - int(sec or 60)) > 8:
                msg += " ⚠️ ความยาวห่างจากเป้า ลองเพิ่ม/ตัดบท"
            return gr.update(choices=_viral_choices(tid), value=pid_), pid_, msg

        v_save.click(v_save_, [c_skill, v_pid, v_name, v_script, v_sec], [v_proj, v_pid, v_sinfo])

        def _after_vo(pid_, p, root):
            dur, _out = V.vo_prep(root, pdir(pid_), p.get("target", 60))
            if dur < p.get("target", 60) - 5:
                gr.Warning(f"เสียงยาว {dur:.0f} วิ สั้นกว่าเป้า: เพิ่มบท 1-2 ประโยคแล้วทำเสียงใหม่")
            return _vo_view(pid_)

        @err
        def v_tts_(pid_, voice, vmodel, progress=gr.Progress()):
            if not pid_:
                raise UserError("บันทึกบทก่อน")
            p, root = _viral_root(pid_)
            progress(0.2, desc="ElevenLabs กำลังสร้างเสียง")
            V.tts(root, pdir(pid_), voice or V.DEFAULT_VOICE, vmodel)
            progress(0.8, desc="ตัดช่วงเงียบ + จับเวลาซับ")
            return _after_vo(pid_, p, root)

        v_tts.click(v_tts_, [v_pid, v_voice, v_vmodel], [v_audio, v_vinfo])

        @err
        def v_vofile_(pid_, f):
            if not pid_:
                raise UserError("บันทึกบทก่อน")
            if not f:
                return gr.update(), gr.update()
            p, root = _viral_root(pid_)
            V.import_voice(pdir(pid_), f if isinstance(f, str) else f.name)
            return _after_vo(pid_, p, root)

        v_vofile.upload(v_vofile_, [v_pid, v_vofile], [v_audio, v_vinfo])

        @err
        def v_find_(pid_, model, progress=gr.Progress()):
            if not pid_:
                raise UserError("บันทึกบทก่อน")
            p, root = _viral_root(pid_)
            log = []
            got = V.claude_find_footage(root, pdir(pid_), model, lambda m: (log.append(m), progress(0.5, desc=m[:60])))
            log += [f"✅ {g['name']}: {g['title'][:60]} ({g.get('why', '')})" for g in got]
            fl, rmdd = _flist(pid_)
            return fl, rmdd, V.credits(root, pdir(pid_), p.get("title", "")), "\n".join(log)

        v_find.click(v_find_, [v_pid, v_model], [v_flist, v_rmname, v_credits, v_log])

        @err
        def v_upload_(pid_, files, progress=gr.Progress()):
            if not pid_:
                raise UserError("บันทึกบทก่อน")
            p, root = _viral_root(pid_)
            for k, f in enumerate(files or []):
                progress(k / max(1, len(files)), desc="แปลงไฟล์วิดีโอ")
                V.add_footage_file(pdir(pid_), f if isinstance(f, str) else f.name)
            fl, rmdd = _flist(pid_)
            return fl, rmdd, V.credits(root, pdir(pid_), p.get("title", "")), None

        v_upload.upload(v_upload_, [v_pid, v_upload], [v_flist, v_rmname, v_credits, v_upload])

        @err
        def v_rm_(pid_, name):
            if pid_ and name:
                V.remove_footage(pdir(pid_), name)
            return _flist(pid_) if pid_ else ("", gr.update())

        v_rm.click(v_rm_, [v_pid, v_rmname], [v_flist, v_rmname])

        @err
        def v_auto_(pid_, progress=gr.Progress()):
            if not pid_:
                raise UserError("บันทึกบทก่อน")
            p, root = _viral_root(pid_)
            progress(0.1, desc="จัดช็อตตามเสียง")
            V.auto_cut(pdir(pid_), root)
            progress(0.3, desc="สร้าง footage.mp4")
            out = V.build(root, pdir(pid_), VSTATE)
            V.default_covers(pdir(pid_))
            progress(0.9, desc="พรีวิว")
            return str(V.preview(root, pdir(pid_), VSTATE, "0.3,15,30")), out.strip().splitlines()[-1] if out.strip() else ""

        v_auto.click(v_auto_, v_pid, [v_preview, v_log])

        @err
        def v_ccut_(pid_, model, review, progress=gr.Progress()):
            if not pid_:
                raise UserError("บันทึกบทก่อน")
            p, root = _viral_root(pid_)
            log = []
            V.claude_cut(root, pdir(pid_), model, VSTATE, review, lambda m: (log.append(m), progress(0.5, desc=m[:60])))
            prev = V.preview(root, pdir(pid_), VSTATE, "0,0.25,0.5,15,30")
            gr.Info("Claude ตัดต่อเสร็จ ดูพรีวิวก่อนเรนเดอร์")
            return str(prev), "\n".join(log)

        v_ccut.click(v_ccut_, [v_pid, v_model, v_review], [v_preview, v_log])

        @err
        def v_render_(pid_, progress=gr.Progress()):
            if not pid_:
                raise UserError("บันทึกบทก่อน")
            p, root = _viral_root(pid_)
            log = []
            progress(0.05, desc="เรนเดอร์ภาพ + เสียงประกอบ")
            V.render(root, pdir(pid_), VSTATE, lambda m: (log.append(m), progress(0.5, desc=m[:60])))
            o1, o2, gal = _outputs(pid_)
            # ชื่อไฟล์ใหม่กัน browser cache
            gr.Info("เรนเดอร์เสร็จ 🎉")
            return o1, o2, gal, V.credits(root, pdir(pid_), p.get("title", "")), "\n".join(log[-40:])

        v_render.click(v_render_, v_pid, [v_out1, v_out2, v_covers, v_credits, v_log])

        @err
        def v_del_(pid_, tid):
            if pid_:
                shutil.rmtree(pdir(pid_), ignore_errors=True)
            return gr.update(choices=_viral_choices(tid), value=None), ""

        v_del.click(v_del_, [v_pid, c_skill], [v_proj, v_pid])

        def _drama_template(pid_):
            p = get_project(pid_)
            t = get_template(p.get("templateId"))
            if not t or t["type"] != "drama":
                raise UserError("ไม่พบเทมเพลตละครของซีรีส์นี้ (ถูกลบไปแล้ว?) เพิ่มเทมเพลตเดิมกลับเข้ามาแล้วเลือกใหม่")
            return p, t, template_root(t)

        def d_load(pid_, r):
            if not pid_:
                return "", "", gr.update(choices=[], value=None), "", r + 1, None
            d = pdir(pid_)
            eps = D.episodes(d)
            series = (d / "series.json").read_text("utf-8") if (d / "series.json").exists() else ""
            ep_txt = (d / eps[-1]).read_text("utf-8") if eps else ""
            mp4 = sorted(d.glob("ep*.mp4"))
            return (pid_, series, gr.update(choices=eps, value=eps[-1] if eps else None), ep_txt, r + 1,
                    str(mp4[-1]) if mp4 else None)

        d_proj.change(err(d_load), [d_proj, d_refresh], [d_pid, d_series, d_ep, d_ep_text, d_refresh, d_video])
        d_ep.change(err(lambda pid_, e: (pdir(pid_) / e).read_text("utf-8") if pid_ and e else gr.update()),
                    [d_pid, d_ep], d_ep_text)

        def _file_text(f):
            return Path(f if isinstance(f, str) else f.name).read_text("utf-8", errors="replace") if f else gr.update()

        d_series_file.upload(_file_text, d_series_file, d_series)
        d_ep_file.upload(_file_text, d_ep_file, d_ep_text)

        @err
        def save_series_(tid, pid_, text, r):
            t = get_template(tid)
            if not t or t["type"] != "drama":
                raise UserError("เลือกเทมเพลตละครก่อน")
            if not pid_:
                pid_ = _new_id()
                p = {"id": pid_, "type": "drama", "templateId": tid, "createdAt": _now(),
                     "voices": {"engine": "edge", "map": {}, "default": ""}, "bgs": {}}
            else:
                p = get_project(pid_)
            series = D.save_series(pdir(pid_), text)
            p["title"] = series.get("series_title") or "ซีรีส์ใหม่"
            save_project(p)
            gr.Info("บันทึก series.json แล้ว")
            return gr.update(choices=_drama_choices(tid), value=pid_), pid_, r + 1

        d_save_series.click(save_series_, [c_skill, d_pid, d_series, d_refresh], [d_proj, d_pid, d_refresh])

        @err
        def save_ep_(pid_, text):
            if not pid_:
                raise UserError("บันทึก series.json ก่อน")
            name, _ep = D.save_episode(pdir(pid_), text)
            save_project(get_project(pid_))
            gr.Info(f"บันทึก {name} แล้ว")
            return gr.update(choices=D.episodes(pdir(pid_)), value=name)

        d_save_ep.click(save_ep_, [d_pid, d_ep_text], d_ep).then(lambda r: r + 1, d_refresh, d_refresh)

        @err
        def check_(pid_, ep, sec):
            if not pid_ or not ep:
                raise UserError("เลือกตอนก่อน")
            _p, _t, root = _drama_template(pid_)
            ok, out = D.check(root, pdir(pid_), ep, int(sec or 0))
            (gr.Info if ok else gr.Warning)("บทผ่าน ✅" if ok else "บทยังมี ERROR ต้องแก้ก่อนเรนเดอร์ (WARN แค่คำแนะนำ)")
            return out

        d_check.click(check_, [d_pid, d_ep, d_sec], d_check_out)

        def _voice_cfg(p, engine, emodel):
            v = p.get("voices", {})
            return {"engine": engine, "model": emodel, "map": {k: x for k, x in v.get("map", {}).items() if x},
                    "default": v.get("default", "")}

        def _lib_dir(p):
            t = get_template(p.get("templateId")) or {"name": "unknown"}
            return DATA / "bg_library" / re.sub(r"[^\w-]+", "_", t["name"])

        def _place(pid_, plid):
            for pl in D.load(pdir(pid_), "series.json").get("places", []):
                if pl.get("id") == plid:
                    return pl
            raise UserError("ไม่พบสถานที่นี้ใน series.json")

        def _draw_one(pid_, plid, model, review, feedback, progress=None):
            p, _t, root = _drama_template(pid_)
            say = (lambda m: progress(0, desc=m[:70])) if progress else print
            D.draw_candidate(root, pdir(pid_), D.load(pdir(pid_), "series.json"), _place(pid_, plid), model,
                             review, feedback or "", say)
            gr.Info("วาดเสร็จ ดูภาพแล้วกด ✅ ถ้าโอเค")

        def _draw_many(pid_, places, model, review, progress=None):
            p, _t, root = _drama_template(pid_)
            series = D.load(pdir(pid_), "series.json")
            fails = []
            for k, pl in enumerate(places):
                if progress:
                    progress(k / len(places), desc=f"Claude วาด {pl.get('name', pl['id'])} ({k + 1}/{len(places)})")
                try:
                    D.draw_candidate(root, pdir(pid_), series, pl, model, review, "", print)
                except D.DramaError as e:
                    if "ANTHROPIC_API_KEY" in str(e) or "API key" in str(e):
                        raise
                    fails.append(f"{pl.get('name', pl['id'])}: {e}")
            if fails:
                gr.Warning("วาดไม่สำเร็จ: " + " | ".join(fails)[:400])
            gr.Info("วาดเสร็จ ดูแต่ละภาพแล้วกด ✅ ที่ฉากที่โอเค")

        def _approve(pid_, plid):
            p = get_project(pid_)
            D.approve_candidate(pdir(pid_), _place(pid_, plid), _lib_dir(p), p.get("title", ""))
            p = get_project(pid_)
            p.setdefault("bgs", {})[plid] = "claude"
            save_project(p)
            gr.Info("ใช้ฉากนี้แล้ว และเก็บเข้าคลังฉาก (ซีรีส์อื่นเลือกใช้ได้)")

        def _prep_bgs(p, pid_):
            d = pdir(pid_)
            series = D.load(d, "series.json")
            D.apply_backgrounds(d, series, p.get("bgs", {}), _lib_dir(p))
            miss = [x["place"].get("name", x["place"]["id"]) for x in D.bg_status(d, series, p.get("bgs", {}), _lib_dir(p))
                    if x["used"] and x["state"] == "missing"]
            if miss:
                gr.Warning(f"ยังไม่มีฉาก: {', '.join(miss)} → ใช้ฉากสำเร็จรูปแทน (ไปที่ขั้น 4 ให้ Claude วาดได้)")

        @err
        def still_(pid_, ep, n, engine, emodel):
            if not pid_ or not ep:
                raise UserError("เลือกตอนก่อน")
            p, _t, root = _drama_template(pid_)
            _prep_bgs(p, pid_)
            img = D.render(root, pdir(pid_), ep, _voice_cfg(p, engine, emodel), still=int(n or 1))
            return str(img)

        d_still.click(still_, [d_pid, d_ep, d_still_n, d_engine, d_emodel], d_img)

        @err
        def render_(pid_, ep, engine, emodel, force, progress=gr.Progress()):
            if not pid_ or not ep:
                raise UserError("เลือกตอนก่อน")
            p, _t, root = _drama_template(pid_)
            if engine == "eleven" and not os.environ.get("ELEVENLABS_API_KEY"):
                raise UserError("ยังไม่ได้ใส่ ELEVENLABS_API_KEY")
            ok, out = D.check(root, pdir(pid_), ep)
            if not ok and not force:
                raise UserError("บทยังไม่ผ่านการตรวจ กด ✅ ตรวจบท เพื่อดูว่าต้องแก้อะไร")
            log = []
            _prep_bgs(p, pid_)

            def on_line(line):
                log.append(line)
                if m := re.match(r"shot (\d+)/(\d+)", line):
                    progress(int(m.group(1)) / int(m.group(2)), desc=f"เรนเดอร์ช็อต {m.group(1)}/{m.group(2)}")
                elif m := re.match(r"voice (\d+)/(\d+)", line):
                    progress(0, desc=f"สร้างเสียง {m.group(1)}/{m.group(2)}")
                elif line.startswith("voice:"):
                    progress(0, desc="สร้างเสียงพากย์")

            progress(0, desc="เริ่ม")
            try:
                mp4 = D.render(root, pdir(pid_), ep, _voice_cfg(p, engine, emodel), on_line=on_line)
            except D.DramaError as e:
                silent = sorted(pdir(pid_).glob("ep*.mp4"))
                gr.Warning(str(e)[:300])
                return (str(silent[-1]) if silent and "ไม่มีเสียง" in str(e) else None), "\n".join(log[-60:])
            final = mp4.with_name(f"{mp4.stem}_{int(time.time())}.mp4")  # ชื่อใหม่ กัน browser cache
            for old in pdir(pid_).glob(f"{mp4.stem}_*.mp4"):
                old.unlink(missing_ok=True)
            shutil.copy(mp4, final)
            gr.Info("เรนเดอร์เสร็จ 🎉")
            return str(final), "\n".join(log[-60:])

        d_render.click(render_, [d_pid, d_ep, d_engine, d_emodel, d_force], [d_video, d_log])

        @err
        def d_del_(pid_, tid):
            if pid_:
                shutil.rmtree(pdir(pid_), ignore_errors=True)
            ch = _drama_choices(tid)
            return gr.update(choices=ch, value=None), "", "", ""

        d_del.click(d_del_, [d_pid, c_skill], [d_proj, d_pid, d_series, d_ep_text])

        # ===== events: settings
        @err
        def load_voices():
            _voice_cache[:] = list_voices()
            ch = _voice_choices()
            return f"โหลด {len(_voice_cache)} เสียงแล้ว", gr.update(choices=ch), gr.update(choices=ch)

        st_load.click(load_voices, None, [st_out, e_voice, s_voice])

        # โหลดรายชื่อเสียงอัตโนมัติถ้ามี key
        def on_start():
            if os.environ.get("ELEVENLABS_API_KEY") and not _voice_cache:
                try:
                    _voice_cache[:] = list_voices()
                except Exception:  # noqa: BLE001 — ไม่ให้หน้าเว็บพังเพราะโหลดเสียงไม่ได้
                    pass
            ch = _voice_choices()
            # รายชื่อเทมเพลต/โปรเจกต์ล่าสุดทุกครั้งที่เปิดหน้า (ไม่งั้นรีเฟรชแล้วเห็นของเก่า)
            return (gr.update(choices=ch), gr.update(choices=ch),
                    gr.update(choices=[("(ไม่ใช้เทมเพลต)", "")] + _template_choices()),
                    gr.update(choices=_template_choices()), gr.update(choices=_skill_choices()),
                    gr.update(choices=_project_choices()))

        app.load(on_start, None, [e_voice, s_voice, c_skill, s_pick, e_skill, e_proj])
        d_engine.change(err(lambda pid_, v: _set_engine(pid_, v)), [d_pid, d_engine], None)

        def _set_engine(pid_, v):
            if pid_:
                p = get_project(pid_)
                p.setdefault("voices", {})["engine"] = v
                save_project(p)

    return app


if __name__ == "__main__":
    build_app().queue().launch(share=bool(os.environ.get("COLAB_RELEASE_TAG")), allowed_paths=[str(DATA)])
