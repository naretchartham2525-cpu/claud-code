"""รัน render_ep.py ของเทมเพลตละคร (thai-drama-script) โดยไม่แก้ไฟล์เทมเพลต

ถ้าเลือก ElevenLabs จะสลับฟังก์ชันสร้างเสียง (edge-tts) เป็น ElevenLabs ตอนรันเท่านั้น
ใช้: python drama_runner.py <template_root> <voice_json> -- <args ของ render_ep.py>
voice_json: {"engine": "edge"|"eleven", "model": "...", "map": {"hero": "<voice_id>", ...}, "default": "<voice_id>"}
"""
import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path


def main():
    root, cfg_path = Path(sys.argv[1]), Path(sys.argv[2])
    rest = sys.argv[sys.argv.index("--") + 1:]
    cfg = json.loads(cfg_path.read_text("utf-8"))

    sys.path.insert(0, str(root / "engine"))
    spec = importlib.util.spec_from_file_location("render_ep", root / "scripts" / "render_ep.py")
    R = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(R)

    if cfg.get("engine") == "eleven":
        import requests

        key = os.environ.get("ELEVENLABS_API_KEY", "")
        if not key:
            sys.exit("ERROR ยังไม่ได้ใส่ ELEVENLABS_API_KEY")
        vmap, default = cfg.get("map") or {}, cfg.get("default") or ""
        model = cfg.get("model") or "eleven_v3"
        orig_voice = R.Cast.voice

        def voice(self, cid):
            hero_alias = "hero" if cid in ("narration", "hero") else cid
            vid = vmap.get(cid) or vmap.get(hero_alias) or default
            if not vid:  # ไม่ได้ตั้งเสียงให้ตัวนี้ ใช้เสียงของ hero
                vid = vmap.get("hero") or ""
            if not vid:
                raise SystemExit(f"ERROR ยังไม่ได้เลือกเสียง ElevenLabs ให้ '{cid}' (และไม่มีเสียงเริ่มต้น)")
            return ("eleven:" + vid, "", "")

        R.Cast.voice = voice
        R.Cast._orig_voice = orig_voice

        def synth(text, vid):
            r = requests.post(
                f"{os.environ.get('ELEVENLABS_BASE_URL', 'https://api.elevenlabs.io')}/v1/text-to-speech/{vid}",
                params={"output_format": "mp3_44100_128"},
                headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
                json={"text": text, "model_id": model},
                timeout=180,
            )
            if not r.ok:
                raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:200]}")
            return r.content

        async def _synth_all(items, outdir):
            sem = asyncio.Semaphore(2)
            done = [0]

            async def one(it):
                p = outdir / f"{it['key']}.mp3"
                if p.exists() and p.stat().st_size > 800:  # มีแล้ว ไม่เสียเครดิตซ้ำ
                    return
                async with sem:
                    data = await asyncio.to_thread(synth, it["tts"], it["voice"].split(":", 1)[1])
                    p.write_bytes(data)
                    done[0] += 1
                    print(f"voice {done[0]}/{len(items)}", flush=True)

            await asyncio.gather(*(one(it) for it in items))

        R._synth_all = _synth_all

    sys.argv = ["render_ep.py"] + rest
    R.main()


if __name__ == "__main__":
    main()
