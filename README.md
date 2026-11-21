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
