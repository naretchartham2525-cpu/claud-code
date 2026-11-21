"""เทมเพลตแบบ "คลิปไวรัลจากฟุตเทจจริง" (engine แบบ viral-footage-story-60s:
engine/render.py + build_footage.py + vo_prep.py) — ไม่แก้ไฟล์เทมเพลต เรียกสคริปต์ของเทมเพลตตรงๆ

โปรเจกต์ (P) ใช้โครงเดียวกับที่ engine ต้องการ:
  script.txt, vo-raw.mp3 -> vo-tight.wav, segments.json, words.json, footage/*.mp4, cuts.json -> footage.mp4,
  project.json, covers.json, out/, final/
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_VOICE = "SwzBuhE855Xm5zN9AmhS"  # เสียงเริ่มต้นที่เทมเพลตแนะนำ


class ViralError(Exception):
    pass


def detect(root: Path) -> bool:
    e = root / "engine"
    return all((e / f).is_file() for f in ("render.py", "build_footage.py", "vo_prep.py"))


def _env(state_dir: Path) -> dict:
    # engine จำ "แบบเปิดคลิป / เสียงเฉลย" ไว้ใน ~/.viral-footage-story-60s -> ชี้ HOME ไปที่ Drive ให้จำข้ามวันได้
    state_dir.mkdir(parents=True, exist_ok=True)
    return {**os.environ, "HOME": str(state_dir)}


def _run(cmd, cwd, env=None, on_line=None, timeout=3600) -> str:
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    lines = []
    for line in p.stdout:
        lines.append(line.rstrip())
        if on_line:
            on_line(line.rstrip())
    p.wait(timeout=timeout)
    out = "\n".join(lines)
    if p.returncode != 0:
        raise ViralError(out[-2500:] or f"exit {p.returncode}")
    return out


def _probe(f: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _load(p: Path, default=None):
    try:
        return json.loads(p.read_text("utf-8"))
    except FileNotFoundError:
        return default


def _save(p: Path, data) -> None:
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")


# ---------------------------------------------------------------- โปรเจกต์ / บท / เสียง
def new_project(root: Path, pdir: Path, name: str) -> None:
    t = root / "templates"
    for d in ("footage", "final", "grids"):
        (pdir / d).mkdir(parents=True, exist_ok=True)
    for f, dst in (("script-template.txt", "script.txt"), ("cuts.json", "cuts.json"), ("covers.json", "covers.json")):
        if (t / f).exists() and not (pdir / dst).exists():
            shutil.copy(t / f, pdir / dst)
    pj = _load(t / "project.json", {}) or {}
    pj.update(name=name or "คลิปใหม่", title="")
    _save(pdir / "project.json", pj)


def save_script(pdir: Path, text: str) -> int:
    lines = [ln.rstrip() for ln in (text or "").strip().splitlines()]
    body = [ln for ln in lines if ln.strip() and not ln.strip().startswith("[")]
    if not body:
        raise ViralError("บทว่าง")
    (pdir / "script.txt").write_text("\n".join(lines) + "\n", "utf-8")
    syl = sum(len(re.sub(r"[ัิ-ฺ็-๎\s]", "", ln)) for ln in body)
    return round(syl / 9.8)  # ประมาณวินาที (วัดจากตัวอย่างในสกิล: ~9.8 ตัวอักษรไทยต่อวินาที)


def tts(root: Path, pdir: Path, voice: str, model: str, on_line=None) -> str:
    if not os.environ.get("ELEVENLABS_API_KEY"):
        raise ViralError("ยังไม่ได้ใส่ ELEVENLABS_API_KEY")
    return _run([sys.executable, str(root / "engine" / "tts_elevenlabs.py"), str(pdir), "--voice", voice or DEFAULT_VOICE,
                 "--model", model], pdir, on_line=on_line, timeout=900)


def import_voice(pdir: Path, path: str) -> None:
    _run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vn", "-ac", "1", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "128k",
          str(pdir / "vo-raw.mp3")], pdir)


def vo_prep(root: Path, pdir: Path, target_sec: int) -> tuple[float, str]:
    if not (pdir / "vo-raw.mp3").exists():
        raise ViralError("ยังไม่มีไฟล์เสียงพากย์")
    out = _run([sys.executable, str(root / "engine" / "vo_prep.py"), str(pdir), "--min", str(max(10, int(target_sec) - 5))], pdir)
    pj = _load(pdir / "project.json", {})
    pj["vo"] = "vo-tight.wav"
    _save(pdir / "project.json", pj)
    return _probe(pdir / "vo-tight.wav"), out


# ---------------------------------------------------------------- ฟุตเทจ
def footage_list(pdir: Path) -> list[dict]:
    fd = pdir / "footage"
    fd.mkdir(exist_ok=True)
    out = []
    for f in sorted(fd.glob("*.mp4")):
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height",
                            "-of", "csv=s=x:p=0", str(f)], capture_output=True, text=True).stdout.strip().splitlines()
        out.append({"name": f.name, "path": str(f), "dur": round(_probe(f), 1), "size": r[0] if r else "?"})
    return out


def add_footage_file(pdir: Path, path: str) -> str:
    fd = pdir / "footage"
    fd.mkdir(exist_ok=True)
    n = len(list(fd.glob("*.mp4"))) + 1
    stem = re.sub(r"^\d+-", "", re.sub(r"[^\w-]+", "-", Path(path).stem))[:30].strip("-") or "clip"
    out = fd / f"{n:02d}-{stem}.mp4"
    _run(["ffmpeg", "-v", "error", "-y", "-i", path, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
          "-vf", "scale='min(1920,iw)':-2", "-c:a", "aac", str(out)], pdir, timeout=1800)
    with open(fd / ".credits-raw.txt", "a", encoding="utf-8") as f:
        f.write(f"{out.name}|{Path(path).name}|อัปโหลดเอง|-\n")
    return out.name


def remove_footage(pdir: Path, name: str) -> None:
    if re.fullmatch(r"[\w.-]+\.mp4", name or ""):
        (pdir / "footage" / name).unlink(missing_ok=True)


def download(root: Path, pdir: Path, name: str, url: str, rng: str = "", on_line=None) -> str:
    if not shutil.which("yt-dlp"):
        raise ViralError("ไม่มี yt-dlp")
    if not re.fullmatch(r"[\w-]{1,40}", name):
        raise ViralError("ชื่อไฟล์ไม่ถูกต้อง")
    args = ["bash", str(root / "engine" / "download.sh"), str(pdir), name, url] + ([rng] if rng else [])
    try:
        return _run(args, pdir, on_line=on_line, timeout=1800)
    except ViralError as e:
        msg = str(e)
        if "Sign in" in msg or "bot" in msg or "403" in msg:
            raise ViralError("YouTube บล็อกการโหลดจาก Colab (ขอให้ล็อกอิน/ยืนยันว่าไม่ใช่บอท) "
                             "→ โหลดคลิปเองแล้วอัปโหลดเข้าเว็บแทน\n" + msg[-400:])
        raise


def credits(root: Path, pdir: Path, title: str) -> str:
    f = pdir / "footage" / ".credits-raw.txt"
    if not f.exists():
        return ""
    try:
        _run([sys.executable, str(root / "engine" / "credits.py"), str(pdir), "--title", title or "footage sources"], pdir)
    except ViralError:
        pass
    c = pdir / "footage" / "credits.txt"
    return c.read_text("utf-8") if c.exists() else f.read_text("utf-8")


def ytsearch(query: str, n: int = 8) -> list[dict]:
    if not shutil.which("yt-dlp"):
        raise ViralError("ไม่มี yt-dlp (ติดตั้ง: pip install yt-dlp)")
    r = subprocess.run(["yt-dlp", "--flat-playlist", "-J", f"ytsearch{n}:{query}"], capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise ViralError(f"ค้น YouTube ไม่ได้: {r.stderr[-300:]}")
    out = []
    for e in json.loads(r.stdout).get("entries", []) or []:
        if not e or (e.get("duration") or 0) > 900:
            continue
        out.append({"title": e.get("title"), "channel": e.get("channel") or e.get("uploader"), "views": e.get("view_count"),
                    "dur": e.get("duration"), "url": e.get("url") or f"https://www.youtube.com/watch?v={e.get('id')}"})
    return out


def contact_sheet(root: Path, video: Path, out: Path, max_tiles: int = 60, start: float = 0, length: float | None = None,
                  grid: bool = False) -> float:
    """แผ่นภาพย่อมีเวลา (วินาทีต้นฉบับ) บนทุกภาพ คืน step ที่ใช้"""
    dur = length or (_probe(video) - start)
    step = max(0.25, round(dur / max_tiles, 2)) if dur > 0 else 1
    font = next((str(f) for f in (root / "engine" / "fonts").glob("Inter*.ttf")), "")
    n = max(1, int(dur / step))
    cols = min(10, n)
    rows = (n + cols - 1) // cols
    txt = f"drawtext=fontfile='{font}':text='%{{eif\\:t+{start}\\:d}}.%{{eif\\:mod((t+{start})*10\\,10)\\:d}}':x=4:y=4:fontsize=18:fontcolor=yellow:box=1:boxcolor=black@0.7" if font else "null"
    gridf = ""
    if grid:  # เส้นตาราง + พิกัดพิกเซล (สำหรับให้ Claude บอกตำแหน่งตัวเอก) บนภาพ 1920x1080
        gridf = ",drawgrid=w=iw/8:h=ih/6:t=1:c=white@0.35"
    vf = f"fps=1/{step},scale={'480' if grid else '220'}:-2{gridf},{txt},tile={cols}x{rows}"
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", str(start)] + (["-t", str(length)] if length else []) + \
          ["-i", str(video), "-vf", vf, "-frames:v", "1", "-q:v", "4", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise ViralError("ทำภาพย่อไม่ได้: " + r.stderr[-300:])
    return step


# ---------------------------------------------------------------- Claude
def _claude():
    import anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ViralError("ยังไม่ได้ใส่ ANTHROPIC_API_KEY (Colab: เมนู 🔑 Secrets)")
    return anthropic, anthropic.Anthropic()


def _ask(model: str, system, content, max_tokens=16000) -> str:
    anthropic, client = _claude()
    kw = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} if model in ("claude-sonnet-5-5", "claude-opus-5-5") else {}
    try:
        res = client.beta.messages.create(model=model, max_tokens=max_tokens, output_config={"effort": "medium"},
                                          system=system, messages=content if isinstance(content, list) and content and
                                          isinstance(content[0], dict) and "role" in content[0] else
                                          [{"role": "user", "content": content}], **kw)
    except anthropic.AuthenticationError:
        raise ViralError("Anthropic API key ไม่ถูกต้อง")
    except anthropic.RateLimitError:
        raise ViralError("Claude ถูกจำกัดการใช้งานชั่วคราว ลองใหม่อีกสักครู่")
    except anthropic.APIStatusError as e:
        raise ViralError(f"Claude API {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        raise ViralError("เชื่อมต่อ Claude ไม่ได้")
    if res.stop_reason == "refusal":
        raise ViralError("Claude ปฏิเสธคำขอนี้")
    return "".join(b.text for b in res.content if b.type == "text")


def _json_from(text: str):
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    raw = m.group(1) if m else text[text.find("{") if "{" in text else 0:]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # ตัดส่วนเกินท้าย
        end = raw.rfind("}")
        return json.loads(raw[:end + 1])


def _img(path: Path, max_w: int = 1600) -> dict:
    from PIL import Image
    im = Image.open(path).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, int(im.height * max_w / im.width)))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}}


def _skill_rules(root: Path) -> str:
    s = (root / "SKILL.md").read_text("utf-8", errors="replace")
    s = re.sub(r"[​-‍⁠﻿]", "", s)  # ตัดอักขระล่องหน
    return s


def claude_find_footage(root: Path, pdir: Path, model: str, log=print) -> list[dict]:
    """Claude อ่านบท -> คิดคำค้น -> ค้น YouTube (yt-dlp) -> Claude เลือกคลิป -> โหลด"""
    if not shutil.which("yt-dlp"):
        raise ViralError("ไม่มี yt-dlp (ติดตั้ง: pip install yt-dlp) → หรืออัปโหลดคลิปเอง")
    script = (pdir / "script.txt").read_text("utf-8")
    sys_ = "คุณช่วยหาฟุตเทจจริงบน YouTube สำหรับคลิปเล่าเรื่องไวรัล ตอบเป็น JSON เท่านั้น"
    log("🔎 Claude อ่านบทแล้วคิดคำค้น")
    q = _json_from(_ask(model, sys_, "บทพากย์:\n" + script + "\n\nคิดคำค้น YouTube ภาษาอังกฤษ 3-4 คำค้น "
                        "หาคลิปต้นฉบับของเหตุการณ์นี้ + มุมกล้องอื่น + ภาพเสริม ตอบ {\"queries\": [\"...\"]}", 2000))
    cands = []
    for query in (q.get("queries") or [])[:4]:
        log(f"   ค้น: {query}")
        try:
            cands += ytsearch(query)
        except ViralError as e:
            log(f"   ⚠️ {e}")
    seen, uniq = set(), []
    for c in cands:
        if c["url"] not in seen:
            seen.add(c["url"])
            uniq.append(c)
    if not uniq:
        raise ViralError("ค้น YouTube ไม่เจอ หรือ Colab โดนบล็อก → อัปโหลดคลิปเองแทน")
    listing = "\n".join(f"{i}. {c['title']} | {c['channel']} | {c['views']} views | {c['dur']}s | {c['url']}" for i, c in enumerate(uniq))
    log(f"🧠 Claude เลือกจาก {len(uniq)} คลิป")
    pick = _json_from(_ask(model, sys_, "บท:\n" + script + "\n\nผลค้น:\n" + listing +
                           "\n\nเลือก 2-4 คลิปที่เป็นฟุตเทจจริงของเหตุการณ์นี้ (ต้นฉบับก่อน แล้วมุมอื่น/ภาพเสริม) ไม่เอา compilation/สไลด์/AI "
                           "ถ้าคลิปยาวเกิน 5 นาที ระบุช่วงที่ใช้ range \"start-end\" วินาที (ไม่รู้ก็ว่างไว้) ตอบ "
                           "{\"picks\": [{\"i\": เลข, \"name\": \"01-main\", \"range\": \"\", \"why\": \"ไทยสั้นๆ\"}]}", 3000))
    got = []
    for k, p in enumerate((pick.get("picks") or [])[:4]):
        try:
            c = uniq[int(p["i"])]
        except (KeyError, ValueError, IndexError):
            continue
        name = re.sub(r"[^\w-]+", "-", p.get("name") or f"{k + 1:02d}-clip")[:30]
        if not re.match(r"\d\d-", name):
            name = f"{len(footage_list(pdir)) + 1:02d}-{name}"
        log(f"⬇️ โหลด {name}: {c['title'][:60]}")
        try:
            download(root, pdir, name, c["url"], p.get("range") or "")
            got.append({**c, "name": name, "why": p.get("why", "")})
        except ViralError as e:
            log(f"   ⚠️ โหลดไม่ได้: {str(e)[:200]}")
    if not got:
        raise ViralError("โหลดฟุตเทจไม่ได้เลย (Colab มักโดน YouTube บล็อก) → โหลดคลิปเองแล้วกดอัปโหลด")
    return got


# ---------------------------------------------------------------- ตัดต่อ
def _segments(pdir: Path) -> list[dict]:
    s = _load(pdir / "segments.json")
    if not s:
        raise ViralError("ยังไม่ได้เตรียมเสียง (ต้องมี segments.json) กดสร้าง/อัปโหลดเสียงก่อน")
    return s


def _files_map(pdir: Path) -> dict:
    fl = footage_list(pdir)
    if not fl:
        raise ViralError("ยังไม่มีฟุตเทจ")
    return {chr(65 + i): {"file": f"footage/{f['name']}", "dur": f["dur"]} for i, f in enumerate(fl[:12])}


def _check_cuts(root: Path, cuts: dict) -> list[str]:
    sys.path.insert(0, str(root / "engine"))
    try:
        from check_repeats import check
        bad, _rows = check(cuts)
        return bad
    finally:
        sys.path.pop(0)


def auto_cut(pdir: Path, root: Path) -> dict:
    """ตัดแบบง่าย (ฟรี): 1 ช็อตต่อ 1 วลี เดินหน้าในแต่ละไฟล์ ไม่ใช้ช่วงซ้ำ ปรับความเร็วให้พอดี"""
    segs = _segments(pdir)
    files = _files_map(pdir)
    keys = list(files)
    vo_end = segs[-1]["end"] + 0.5
    # ขอบเขตช็อต: ตัดตามต้นวลี แบ่งวลียาว > 3.2 วิ
    cuts_t = []
    for s in segs[1:]:
        cuts_t.append(round(s["start"], 3))
    bounds, prev = [], 0.0
    for t in cuts_t + [round(vo_end, 3)]:
        while t - prev > 3.2:
            prev = round(prev + min(2.6, (t - prev) / 2), 3)
            bounds.append(prev)
        if t - prev >= 0.6:
            bounds.append(t)
            prev = t
    total_src = sum(f["dur"] for f in files.values())
    ratio = min(1.6, max(0.5, (total_src * 0.9) / vo_end))  # ฟุตเทจน้อย = สโลว์ ฟุตเทจเยอะ = เร่งนิดหน่อย
    cursor = {k: 0.3 for k in keys}
    pieces, t0, k = [], 0.0, 0
    for end in bounds:
        d = end - t0
        ln = round(d * ratio, 2)
        for _ in range(len(keys)):
            key = keys[k % len(keys)]
            if cursor[key] + ln <= files[key]["dur"] - 0.2:
                break
            k += 1
        else:
            ln = round(d * 0.5, 2)
            key = max(keys, key=lambda x: files[x]["dur"] - cursor[x])
            if cursor[key] + ln > files[key]["dur"]:
                raise ViralError("ฟุตเทจไม่พอสำหรับความยาวเสียง (ห้ามใช้ภาพซ้ำ) เพิ่มคลิปอีก")
        pieces.append({"end": round(end, 3), "file": key, "src": round(cursor[key], 2), "len": ln, "cx": 0.5})
        cursor[key] = round(cursor[key] + ln + 0.05, 2)
        t0 = end
        if len(pieces) % 3 == 0:
            k += 1  # สลับมุมทุก 3 ช็อต
    cuts = {"files": {k2: v["file"] for k2, v in files.items()}, "crop": 0.88, "pieces": pieces}
    bad = _check_cuts(root, cuts)
    if bad:
        raise ViralError("ตัดอัตโนมัติชนกัน: " + "; ".join(bad[:3]))
    _save(pdir / "cuts.json", cuts)
    # เอฟเฟกต์พื้นฐานตามสไตล์: riser หลังฮุก, click ต้นท่อน, ปุ่มกดไลค์ท้าย, เปิดคลิปไม่มีตัวชี้ (ไม่รู้ตำแหน่งตัวเอก)
    pj = _load(pdir / "project.json", {})
    last = segs[-1]
    pj.update(arrows=[], neons=[], circles=[], shakes=[], blurs=[], cues=[], hollow_hit_at=[], opening_shock=False,
              flashes=[{"t": round(segs[1]["start"], 2), "d": 0.25, "a": 0.6}] if len(segs) > 1 else [],
              riser_at=[round(segs[1]["start"], 2)] if len(segs) > 1 else [],
              click_at=[round(s["start"], 2) for s in segs[2:-1:max(1, len(segs) // 4)]][:4],
              awkward_at=[round(segs[-2]["start"], 2)] if len(segs) > 2 else [],
              cta=[{"at": round(last["start"] + (last["end"] - last["start"]) * 0.5, 2), "style": "fb_end", "tap": 0.75, "y": 250}])
    _save(pdir / "project.json", pj)
    return cuts


def build(root: Path, pdir: Path, env_dir: Path, on_line=None) -> str:
    return _run([sys.executable, str(root / "engine" / "build_footage.py"), str(pdir)], pdir, _env(env_dir), on_line, 3600)


def claude_cut(root: Path, pdir: Path, model: str, env_dir: Path, review: bool = True, log=print) -> None:
    """Claude ดูภาพย่อฟุตเทจ -> cuts.json; สร้าง footage.mp4; ดูเฟรมจริง -> ตัวชี้/เอฟเฟกต์/ปก; ดูพรีวิวแล้วแก้ 1 รอบ"""
    segs = _segments(pdir)
    files = _files_map(pdir)
    rules = _skill_rules(root)
    ex_dir = next(iter(sorted((root / "examples").glob("*/cuts.json"))), None)
    example = ""
    if ex_dir:
        example = ("\n\n=== ตัวอย่าง cuts.json ===\n" + ex_dir.read_text("utf-8")[:4000] +
                   "\n\n=== ตัวอย่าง project.json (เฉพาะเอฟเฟกต์) ===\n" +
                   json.dumps({k: v for k, v in (_load(ex_dir.parent / "project.json", {}) or {}).items()
                               if k in ("arrows", "neons", "circles", "flashes", "shakes", "riser_at", "click_at", "awkward_at",
                                        "hollow_hit_at", "cta", "cues", "opening_shock", "reveal_sound")}, ensure_ascii=False))
    system = [{"type": "text", "text": "คุณเป็นคนตัดต่อคลิปตามกฎของสกิลด้านล่างอย่างเคร่งครัด ตอบเป็น JSON ใน ```json``` เท่านั้น"},
              {"type": "text", "text": rules + example, "cache_control": {"type": "ephemeral"}}]

    # --- ขั้น A: เลือกช่วงฟุตเทจ
    grids = pdir / "grids"
    grids.mkdir(exist_ok=True)
    content = []
    for key, f in files.items():
        out = grids / f"{key}.jpg"
        step = contact_sheet(root, pdir / f["file"], out, max_tiles=60)
        content += [{"type": "text", "text": f"ไฟล์ {key} = {f['file']} ยาว {f['dur']} วิ (ภาพทุก {step} วิ เลขบนภาพ = วินาทีต้นฉบับ)"},
                    _img(out)]
    seg_txt = json.dumps(segs, ensure_ascii=False)
    opening = _run([sys.executable, str(root / "engine" / "next_opening.py"), str(pdir)], pdir, _env(env_dir)).strip().splitlines()[0] \
        if (root / "engine" / "next_opening.py").exists() else "neon"
    content.append({"type": "text", "text":
        f"บท:\n{(pdir / 'script.txt').read_text('utf-8')}\n\nเวลาวลีของเสียงพากย์ (segments.json):\n{seg_txt}\n\n"
        "ขั้นที่ 5 ข้อ 1: เขียน cuts.json (ใช้ชื่อไฟล์ตามตัวอักษรด้านบน) หนึ่งช็อตต่อหนึ่งวลี 1–3 วิ ต่อกันจนจบเสียง + 0.5 วิ "
        "ห้ามใช้ช่วงต้นฉบับซ้ำ ภาพต้องตรงกับบท ตัดซูม (punch) 2–3 จุด (ไม่ใช่ช็อตแรก) "
        'ตอบ {"cuts": {"files": {...}, "crop": 0.88, "pieces": [...]}}'})
    msgs = [{"role": "user", "content": content}]
    for attempt in range(3):
        log("✂️ Claude เลือกช่วงฟุตเทจ" + (f" (แก้ครั้งที่ {attempt})" if attempt else ""))
        text = _ask(model, system, msgs)
        try:
            cuts = _json_from(text)["cuts"]
            cuts["files"] = {k: v["file"] for k, v in files.items()}
            bad = _check_cuts(root, cuts)
            if bad:
                raise ViralError("ฟุตเทจซ้ำ: " + "; ".join(bad[:5]))
            for p in cuts["pieces"]:
                if p["file"] not in files or p["src"] + p.get("len", 0) > files[p["file"]]["dur"] + 0.05:
                    raise ViralError(f"ช่วงเกินความยาวไฟล์: {p}")
            break
        except (ViralError, KeyError, TypeError, json.JSONDecodeError) as e:
            msgs += [{"role": "assistant", "content": text}, {"role": "user", "content": f"ผิด: {e} ส่ง JSON ใหม่ทั้งก้อน"}]
    else:
        raise ViralError("Claude ตัดต่อไม่ผ่านการตรวจ (ลองอีกครั้ง หรือใช้ตัดอัตโนมัติ)")
    _save(pdir / "cuts.json", cuts)
    log("🎞️ สร้าง footage.mp4 (สโลว์แบบลื่นอาจใช้หลายนาที)")
    build(root, pdir, env_dir)

    # --- ขั้น B: ตัวชี้ / เอฟเฟกต์ / CTA / ปก จากเฟรมจริงของ footage.mp4
    fm = pdir / "footage.mp4"
    contact_sheet(root, fm, grids / "open.jpg", max_tiles=8, start=0, length=2.0, grid=True)
    contact_sheet(root, fm, grids / "all.jpg", max_tiles=40, grid=False)
    pj = _load(pdir / "project.json", {})
    words = (pdir / "words.json").read_text("utf-8")[:12000]
    content = [{"type": "text", "text": "ภาพ 2 วินาทีแรกของ footage.mp4 (ขนาดจริง 1920x1080 ตารางแบ่ง 8x6 ช่อง ช่องละ 240x180 px)"},
               _img(grids / "open.jpg"),
               {"type": "text", "text": "ภาพทั้งคลิป (เลขบนภาพ = เวลาในคลิป)"}, _img(grids / "all.jpg"),
               {"type": "text", "text":
                f"shots: {json.dumps(pj.get('shots'), ensure_ascii=False)}\nwords.json: {words}\n\n"
                f"แบบเปิดคลิปนี้: {opening}\n"
                "ทำขั้นที่ 5 ข้อ 4 และข้อ 8 (ปก): กำหนดเอฟเฟกต์ตามกฎ (พิกัดเป็นพิกเซลของ footage.mp4 1920x1080 เวลาในคลิป) "
                "ตัวชี้เปิดคลิปต้อง start 0.0 ชี้ตัวเอกที่เห็นในเฟรมแรก CTA กดไลค์ท้ายคลิปตรงคำว่า กดไลค์ ตอบ "
                '{"project": {"arrows": [], "neons": [], "circles": [], "flashes": [], "shakes": [], "riser_at": [], "click_at": [], '
                '"awkward_at": [], "hollow_hit_at": [], "opening_shock": false, "cues": [], "cta": []}, "covers": [ปก A, ปก B]}'}]
    log("🎯 Claude วางตัวชี้ เสียงประกอบ และปุ่มกดไลค์")
    text = _ask(model, system, content)
    res = _json_from(text)
    keys = ("arrows", "neons", "circles", "flashes", "shakes", "riser_at", "click_at", "awkward_at", "hollow_hit_at",
            "opening_shock", "cues", "cta")
    pj.update({k: v for k, v in (res.get("project") or {}).items() if k in keys})
    pj["title"] = ""
    _save(pdir / "project.json", pj)
    if res.get("covers"):
        _save(pdir / "covers.json", res["covers"])

    if review:
        pts = sorted({0.0, 0.25, 0.5} | {round(a.get("start", 0) + 0.3, 2) for a in pj.get("arrows", [])[:3]})
        try:
            preview(root, pdir, env_dir, ",".join(str(p) for p in pts))
            log("👀 Claude ตรวจภาพพรีวิว")
            text2 = _ask(model, system, [{"role": "user", "content": content}, {"role": "assistant", "content": text},
                                         {"role": "user", "content": [
                                             _img(pdir / "out" / "preview.jpg"),
                                             {"type": "text", "text": f"พรีวิวเวลา {pts}: ตัวชี้ตรงตัวเอกไหม เห็นตัวเอกเฟรมแรกไหม ซับบังจุดสำคัญไหม "
                                                                      "ถ้าดีแล้วตอบ OK ถ้าต้องแก้ ส่ง JSON รูปแบบเดิมทั้งก้อน"}]}])
            if "{" in text2 and "OK" not in text2.strip()[:4]:
                res2 = _json_from(text2)
                pj.update({k: v for k, v in (res2.get("project") or {}).items() if k in keys})
                _save(pdir / "project.json", pj)
                if res2.get("covers"):
                    _save(pdir / "covers.json", res2["covers"])
                log("   ปรับตามพรีวิวแล้ว")
        except (ViralError, json.JSONDecodeError) as e:
            log(f"   ข้ามการตรวจพรีวิว: {str(e)[:150]}")


def default_covers(pdir: Path) -> None:
    """ปก 2 แบบแบบง่าย (ตัดอัตโนมัติ): ใช้ประโยคแรกของบท"""
    body = [ln.strip() for ln in (pdir / "script.txt").read_text("utf-8").splitlines() if ln.strip() and not ln.startswith("[")]
    words = body[0].split() if body else ["คลิปนี้"]
    top, bottom = " ".join(words[:2])[:18], (" ".join(words[2:5]) or "ดูให้จบ!")[:18]
    dur = _probe(pdir / "footage.mp4") or 30
    base = {"src": "footage.mp4", "layout": "landscape", "cx": 0.5, "main_h": 1150, "main_y": 620, "shade": "top",
            "shade_h": 660, "text_y": 150}
    _save(pdir / "covers.json", [{**base, "name": "ปก-A", "t": round(dur * 0.55, 2), "rows": [[top, 120, "white"], [bottom, 140, "yellow"]]},
                                 {**base, "name": "ปก-B", "t": round(dur * 0.3, 2), "rows": [[top, 120, "white"], [bottom, 140, "yellow"]]}])


# ---------------------------------------------------------------- เรนเดอร์
def preview(root: Path, pdir: Path, env_dir: Path, times: str = "0.3,10,30") -> Path:
    if not (pdir / "footage.mp4").exists():
        raise ViralError("ยังไม่ได้ตัดต่อ")
    _run([sys.executable, str(root / "engine" / "render.py"), str(pdir / "project.json"), str(pdir / "out"), "--preview", times],
         pdir, _env(env_dir), timeout=600)
    return pdir / "out" / "preview.jpg"


def render(root: Path, pdir: Path, env_dir: Path, on_line=None) -> dict:
    if not (pdir / "footage.mp4").exists():
        raise ViralError("ยังไม่ได้ตัดต่อ")
    for f in (pdir / "out").glob("*.mp4") if (pdir / "out").exists() else []:
        f.unlink()
    env = _env(env_dir)
    _run([sys.executable, str(root / "engine" / "render.py"), str(pdir / "project.json"), str(pdir / "out")], pdir, env, on_line)
    if on_line:
        on_line("ปรับความดัง -14 LUFS + ทำไฟล์พรีวิว")
    shutil.rmtree(pdir / "final", ignore_errors=True)
    _run(["bash", str(root / "engine" / "finish.sh"), str(pdir)], pdir, env, on_line)
    if (pdir / "covers.json").exists():
        try:
            _run([sys.executable, str(root / "engine" / "cover.py"), str(pdir / "covers.json"), str(pdir / "final")], pdir, env)
        except ViralError as e:
            if on_line:
                on_line(f"ทำปกไม่ได้: {str(e)[:200]}")
    fin = pdir / "final"
    return {"videos": sorted(str(p) for p in fin.glob("*.mp4") if "preview" not in p.name),
            "covers": sorted(str(p) for p in fin.glob("ปก*.jpg")),
            "sheet": str(fin / "contact-sheet.jpg") if (fin / "contact-sheet.jpg").exists() else None}
