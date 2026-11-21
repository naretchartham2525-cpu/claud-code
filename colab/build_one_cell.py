"""สร้าง colab/ClipStudio_1cell.py: โค้ด cell เดียว ก๊อปไปวางใน Colab แล้วรันได้เลย

รัน: python colab/build_one_cell.py
"""
import base64
import io
import tarfile
from pathlib import Path

HERE = Path(__file__).parent
MODULES = ["drama_adapter.py", "drama_runner.py", "viral_adapter.py", "clip_studio.py"]

buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as tar:
    for m in MODULES:
        tar.add(HERE / m, arcname=m)
blob = base64.b64encode(buf.getvalue()).decode()

cell = f'''# 🎬 Clip Studio — วางทั้ง cell นี้ใน Colab แล้วกด ▶️ (ครั้งแรกใช้เวลา 1-2 นาที)
# ก่อนรัน: เมนูซ้าย 🔑 Secrets → ใส่ ELEVENLABS_API_KEY / ANTHROPIC_API_KEY แล้วเปิด Notebook access
# ข้อมูลทั้งหมดเก็บที่ Google Drive: MyDrive/ClipStudio
import base64, io, os, subprocess, sys, tarfile

print("⏳ ติดตั้งโปรแกรม...")
subprocess.run("apt-get -qq install -y fonts-thai-tlwg > /dev/null 2>&1", shell=True)
subprocess.run([sys.executable, "-m", "pip", "-q", "install", "gradio==6.32.0", "anthropic==2.12.0", "pythainlp",
                "edge-tts", "uharfbuzz", "freetype-py", "opencv-python-headless", "yt-dlp"], check=True)

from google.colab import drive, userdata
drive.mount("/content/drive")
os.environ["CLIPSTUDIO_DIR"] = "/content/drive/MyDrive/ClipStudio"
for k in ("ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY"):
    try:
        os.environ[k] = userdata.get(k) or ""
    except Exception:
        os.environ[k] = ""
    print(k, "✅" if os.environ[k] else "❌ ไม่มี (ใส่ใน 🔑 Secrets แล้วเปิด Notebook access)")

APP = "{blob}"
APP_DIR = "/content/clipstudio"
os.makedirs(APP_DIR, exist_ok=True)
with tarfile.open(fileobj=io.BytesIO(base64.b64decode(APP)), mode="r:gz") as tar:
    try:
        tar.extractall(APP_DIR, filter="data")
    except TypeError:  # Python รุ่นเก่า
        tar.extractall(APP_DIR)
sys.path.insert(0, APP_DIR)
for m in ("clip_studio", "drama_adapter"):
    sys.modules.pop(m, None)  # รันซ้ำแล้วได้โค้ดล่าสุด
import clip_studio

print("🚀 กดลิงก์ https://....gradio.live ด้านล่างเพื่อเปิดเว็บ (อย่าแชร์ลิงก์ให้คนอื่น)")
clip_studio.build_app().queue().launch(share=True, debug=True, allowed_paths=[str(clip_studio.DATA)])
'''
(HERE / "ClipStudio_1cell.py").write_text(cell, "utf-8")
print("wrote", HERE / "ClipStudio_1cell.py", f"{len(cell) / 1024:.0f} KB")
