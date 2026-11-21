"""สร้าง ClipStudio.ipynb จาก clip_studio.py (ฝังโค้ดไว้ใน notebook ไม่ต้อง clone repo)

รัน: python colab/build_notebook.py
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
MODULES = ["drama_adapter.py", "drama_runner.py", "viral_adapter.py", "clip_studio.py"]  # ฝังทุกไฟล์ลง notebook


def md(src):
    return {"cell_type": "markdown", "metadata": {}, "source": src}


def py(src):
    return {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": src}


cells = [
    md("# 🎬 Clip Studio (Colab)\n"
       "สกิล → บท (Claude หรือวางเอง) → พากย์ ElevenLabs → คลิป MP4 ซับไทย\n\n"
       "**ครั้งแรก:** เมนูซ้าย 🔑 *Secrets* → เพิ่ม `ELEVENLABS_API_KEY` (และ `ANTHROPIC_API_KEY` ถ้าจะให้ AI คิดบท) → เปิด *Notebook access*\n\n"
       "**ทุกครั้ง:** Runtime → Run all แล้วกดลิงก์ `https://….gradio.live` ที่ขึ้นใน cell สุดท้าย\n\n"
       "ข้อมูล (เทมเพลต/โปรเจกต์/คลิป) เก็บใน Google Drive: `MyDrive/ClipStudio`\n\n"
       "**เพิ่มเทมเพลต:** แท็บ *เทมเพลต* → ลากไฟล์ .zip ของสกิลมาวาง"),
    py("#@title 1. ติดตั้ง + เชื่อม Google Drive\n"
       "!apt-get -qq install -y fonts-thai-tlwg > /dev/null\n"
       "!pip -q install gradio==6.32.0 anthropic==2.12.0 pythainlp edge-tts uharfbuzz freetype-py opencv-python-headless yt-dlp\n"
       "import os\n"
       "from google.colab import drive, userdata\n"
       "drive.mount('/content/drive')\n"
       "os.environ['CLIPSTUDIO_DIR'] = '/content/drive/MyDrive/ClipStudio'\n"
       "for k in ('ANTHROPIC_API_KEY', 'ELEVENLABS_API_KEY'):\n"
       "    try:\n"
       "        os.environ[k] = userdata.get(k) or ''\n"
       "        print(k, '✅' if os.environ[k] else '❌ ว่าง')\n"
       "    except Exception:\n"
       "        print(k, '❌ ไม่มีใน Secrets (หรือยังไม่เปิด Notebook access)')"),
] + [py(f"%%writefile /content/{m}\n" + (HERE / m).read_text("utf-8")) for m in MODULES] + [
    py("#@title 2. เปิดหน้าเว็บ (กดลิงก์ gradio.live ด้านล่าง)\n"
       "import sys, importlib\n"
       "sys.path.insert(0, '/content')\n"
       "import clip_studio; importlib.reload(clip_studio)\n"
       "clip_studio.build_app().queue().launch(share=True, debug=True, allowed_paths=[str(clip_studio.DATA)])"),
]

nb = {"cells": cells, "metadata": {"colab": {"provenance": []}, "kernelspec": {"name": "python3", "display_name": "Python 3"},
                                   "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 0}
for c in nb["cells"]:  # ipynb เก็บ source เป็น list ของบรรทัด
    c["source"] = c["source"].splitlines(keepends=True)
(HERE / "ClipStudio.ipynb").write_text(json.dumps(nb, ensure_ascii=False, indent=1), "utf-8")
print("wrote", HERE / "ClipStudio.ipynb")
