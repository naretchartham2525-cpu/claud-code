# 🎬 Clip Studio — วางทั้ง cell นี้ใน Colab แล้วกด ▶️ (ดึงโค้ดล่าสุดจาก GitHub ทุกครั้ง)
# ก่อนรัน: เมนูซ้าย 🔑 Secrets → ใส่ ELEVENLABS_API_KEY / ANTHROPIC_API_KEY แล้วเปิด Notebook access
import os, subprocess, sys, urllib.request
BASE = "https://raw.githubusercontent.com/naretchartham2525-cpu/claud-code/claude/ecstatic-planck-rzy33z/colab/"
APP = "/content/clipstudio"
print("⏳ ติดตั้งโปรแกรม...")
subprocess.run("apt-get -qq install -y fonts-thai-tlwg > /dev/null 2>&1", shell=True)
subprocess.run([sys.executable, "-m", "pip", "-q", "install", "gradio==6.32.0", "anthropic==2.12.0", "pythainlp", "edge-tts",
                "uharfbuzz", "freetype-py", "opencv-python-headless", "yt-dlp"], check=True)
os.makedirs(APP, exist_ok=True)
for f in ("drama_adapter.py", "drama_runner.py", "viral_adapter.py", "clip_studio.py"):
    try:
        urllib.request.urlretrieve(BASE + f, f"{APP}/{f}")
    except Exception as e:
        raise SystemExit(f"❌ โหลด {f} ไม่ได้ ({e}) — repo ยังเป็น private อยู่หรือเปล่า?")
from google.colab import drive, userdata
drive.mount("/content/drive")
os.environ["CLIPSTUDIO_DIR"] = "/content/drive/MyDrive/ClipStudio"
for k in ("ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY"):
    try:
        os.environ[k] = userdata.get(k) or ""
    except Exception:
        os.environ[k] = ""
    print(k, "✅" if os.environ[k] else "❌ ไม่มี")
sys.path.insert(0, APP)
for m in ("clip_studio", "drama_adapter", "viral_adapter"):
    sys.modules.pop(m, None)
import clip_studio
print("🚀 กดลิงก์ https://....gradio.live ด้านล่าง (อย่าแชร์ลิงก์ให้คนอื่น)")
clip_studio.build_app().queue().launch(share=True, debug=True, allowed_paths=[str(clip_studio.DATA)])
