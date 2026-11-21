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


def pdir(pid: str) -> Path:
    return PROJECTS / _safe(pid)


def list_projects() -> list[dict]:
    items = [_read(d / "project.json") for d in PROJECTS.iterdir() if d.is_dir()]
    return sorted([p for p in items if p], key=lambda p: p["updatedAt"], reverse=True)


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
def _skill_choices():
    return [("(ไม่ใช้สกิล)", "")] + [(s["name"], s["id"]) for s in list_skills()]


def _project_choices():
    return [(f"{p['title'][:50]} · {len(p['scenes'])} ฉาก · {p['updatedAt'][5:16]}", p["id"]) for p in list_projects()]


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
        def wrapped(*a, **k):
            try:
                return fn(*a, **k)
            except UserError as e:
                raise gr.Error(str(e), duration=None)
        wrapped.__name__ = fn.__name__
        return wrapped

    with gr.Blocks(title="Clip Studio") as app:
        gr.Markdown("# 🎬 Clip Studio\nสกิล → บท → พากย์ ElevenLabs → คลิป MP4")
        pid = gr.State("")

        with gr.Tabs() as tabs:
            # ---------------- สร้างคลิป
            with gr.Tab("สร้างคลิป", id="create"):
                c_skill = gr.Dropdown(_skill_choices(), value="", label="สกิล / สไตล์")
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

            # ---------------- แก้บท / พากย์ / เรนเดอร์
            with gr.Tab("แก้บท & เรนเดอร์", id="edit"):
                with gr.Row():
                    e_proj = gr.Dropdown(_project_choices(), label="โปรเจกต์", scale=4)
                    e_reload = gr.Button("🔄", scale=0, min_width=50)
                    e_del = gr.Button("🗑️ ลบโปรเจกต์", variant="stop", scale=1)
                e_title = gr.Textbox(label="ชื่อคลิป")
                with gr.Row():
                    e_skill = gr.Dropdown(_skill_choices(), label="สกิล (ใช้สไตล์ภาพ/ซับ)")
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

            # ---------------- สกิล
            with gr.Tab("สกิล", id="skills"):
                s_files = gr.File(file_count="multiple", file_types=[".md", ".txt", ".markdown"],
                                  label="อัปโหลดไฟล์สกิล (.md/.txt, รองรับ SKILL.md)")
                s_pick = gr.Dropdown(_skill_choices(), label="เลือกสกิลเพื่อแก้ไข (ว่าง = เพิ่มใหม่)")
                s_name = gr.Textbox(label="ชื่อสกิล")
                s_desc = gr.Textbox(label="คำอธิบายสั้นๆ")
                s_content = gr.Textbox(label="คำสั่ง/สไตล์ให้ Claude ทำตาม", lines=12)
                with gr.Row():
                    s_aspect = gr.Dropdown(list(SIZES), value="9:16", label="สัดส่วนคลิป")
                    s_bg = gr.ColorPicker("#111827", label="สีพื้น")
                    s_fg = gr.ColorPicker("#FFFFFF", label="สีซับ")
                    s_voice = gr.Dropdown(_voice_choices(), value="", label="เสียงพากย์", allow_custom_value=True)
                with gr.Row():
                    s_save = gr.Button("💾 บันทึกสกิล", variant="primary")
                    s_del = gr.Button("🗑️ ลบสกิล", variant="stop")

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

        # ===== events: skills
        def skill_form(sid):
            s = get_skill(sid) if sid else None
            if not s:
                return "", "", "", "9:16", "#111827", "#FFFFFF", gr.update(choices=_voice_choices(), value="")
            st = s["style"]
            return (s["name"], s.get("description", ""), s["content"], st["aspect"], st["bgColor"], st["textColor"],
                    gr.update(choices=_voice_choices(st.get("voiceId", "")), value=st.get("voiceId", "")))

        skill_fields = [s_name, s_desc, s_content, s_aspect, s_bg, s_fg, s_voice]
        s_pick.change(err(skill_form), s_pick, skill_fields)

        def refresh_skill_dds(value=None):
            ch = _skill_choices()
            return gr.update(choices=ch, value=value), gr.update(choices=ch), gr.update(choices=ch)

        @err
        def save_s(sid, *vals):
            s = save_skill(sid or None, *vals)
            gr.Info("บันทึกสกิลแล้ว")
            return refresh_skill_dds(s["id"])

        s_save.click(save_s, [s_pick] + skill_fields, [s_pick, c_skill, e_skill])

        @err
        def del_s(sid):
            if sid:
                (SKILLS / f"{_safe(sid)}.json").unlink(missing_ok=True)
            return refresh_skill_dds("")

        s_del.click(del_s, s_pick, [s_pick, c_skill, e_skill])

        @err
        def upload_s(files):
            for f in files or []:
                import_skill_file(f if isinstance(f, str) else f.name)
            gr.Info(f"เพิ่ม {len(files or [])} สกิลแล้ว")
            return refresh_skill_dds() + (None,)

        s_files.upload(upload_s, s_files, [s_pick, c_skill, e_skill, s_files])

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
            return gr.update(choices=ch), gr.update(choices=ch)

        app.load(on_start, None, [e_voice, s_voice])

    return app


if __name__ == "__main__":
    build_app().queue().launch(share=bool(os.environ.get("COLAB_RELEASE_TAG")), allowed_paths=[str(DATA)])
