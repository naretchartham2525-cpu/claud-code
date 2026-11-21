# Clip Studio

เว็บสำหรับทำคลิปตาม "สกิล" (สไตล์) ที่บันทึกไว้

1. **สกิล** — อัปโหลดไฟล์ `.md`/`.txt` (รองรับ `SKILL.md` ที่มี frontmatter) หรือพิมพ์เอง + ตั้งสัดส่วนคลิป สีพื้น สีซับ เสียงพากย์
2. **บท** — ให้ Claude คิดเนื้อเรื่องจากหัวข้อ / วางบทเองให้ Claude แบ่งฉาก / วางบทเองแบ่งตามย่อหน้า (ไม่ใช้ AI)
3. **พากย์** — ElevenLabs TTS ทีละฉากหรือทั้งหมด (ฉากที่มีเสียงแล้วไม่เสียเครดิตซ้ำ)
4. **เรนเดอร์** — ffmpeg รวมภาพประกอบ (ซูมช้าๆ) หรือพื้นสี + เสียง + ซับไทย → MP4

## ติดตั้ง

ต้องมี Node.js 20+, ffmpeg และฟอนต์ไทย

```bash
sudo apt install ffmpeg fonts-thai-tlwg   # macOS: brew install ffmpeg
npm install
cp .env.example .env   # ใส่ key (หรือใส่ในหน้าตั้งค่า)
node --env-file=.env server.js
```

เปิด http://localhost:3000

- macOS/Windows ไม่มีฟอนต์ Garuda: ตั้ง `SUB_FONT` (ชื่อฟอนต์) และ `SUB_FONTS_DIR` (โฟลเดอร์ฟอนต์) ใน `.env`
- ข้อมูลทั้งหมดอยู่ใน `data/` (สกิล, โปรเจกต์, ไฟล์เสียง/คลิป, settings.json ที่มี key — อย่า commit)

## เวอร์ชัน Colab (Gradio)

ใช้ไฟล์ `colab/ClipStudio.ipynb` (ไม่ต้องติดตั้งอะไรเอง) หรือก๊อปทั้งไฟล์ `colab/ClipStudio_1cell.py` ไปวางใน Colab cell เดียวแล้วกดรัน

1. อัปโหลด `ClipStudio.ipynb` เข้า Colab (File → Upload notebook) หรือเปิดจาก GitHub
2. เมนู 🔑 Secrets ใส่ `ELEVENLABS_API_KEY` และ `ANTHROPIC_API_KEY` (ถ้าจะให้ AI คิดบท) แล้วเปิด Notebook access
3. Runtime → Run all แล้วกดลิงก์ `gradio.live`

ข้อมูลเก็บใน Google Drive `MyDrive/ClipStudio` ไม่หายตอนปิด Colab

### เทมเพลต

แท็บ **เทมเพลต** → ลาก `.zip` ของสกิลมาวาง เว็บตรวจชนิดให้เอง
- 🎭 **ละครการ์ตูน** (มี `scripts/render_ep.py` + `engine/`): วาง `series.json` + `epNN.json` → ตรวจบท → เลือกเสียง (edge-tts ฟรี หรือ ElevenLabs แยกตามตัวละคร) → ภาพตัวอย่าง → เรนเดอร์ ไม่แก้ไฟล์เทมเพลต
  - ฉากหลัง (ขั้น 4): เว็บเช็กว่าสถานที่ไหนในบทยังไม่มีฉาก → กดให้ Claude วาด (ต้องมี `ANTHROPIC_API_KEY`) → ดูภาพแล้วกด ✅ อนุมัติ หรือพิมพ์สิ่งที่อยากปรับแล้ววาดใหม่
  - ฉากที่อนุมัติจะเก็บเข้า **คลังฉาก** (`bg_library/<เทมเพลต>`) ซีรีส์อื่นเลือกใช้ได้ และเว็บจะแนะนำฉากในคลังที่ชื่อคล้ายกันให้
  - ตอนเรนเดอร์ไม่วาดเอง: สถานที่ที่ยังไม่มีฉากจะใช้ฉากสำเร็จรูปแทนพร้อมแจ้งเตือน
- 📝 **ข้อความ** (.md/.txt หรือ zip ที่ไม่มีโค้ด): สไตล์บท + สี/ซับ ใช้กับโหมดบทแบบเดิม

ไฟล์เทมเพลตที่มีลิขสิทธิ์ห้ามแจกจ่าย จึงไม่ได้ใส่ไว้ใน repo นี้ ต้องอัปเข้าเว็บเอง
แก้โค้ดที่ `colab/*.py` แล้วรัน `python colab/build_notebook.py` และ `python colab/build_one_cell.py` เพื่อสร้าง notebook / cell เดียวใหม่
