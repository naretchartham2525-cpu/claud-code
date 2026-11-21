import express from "express";
import multer from "multer";
import fs from "node:fs/promises";
import path from "node:path";
import * as store from "./lib/store.js";
import * as claude from "./lib/claude.js";
import * as eleven from "./lib/eleven.js";
import { renderProject } from "./lib/video.js";

const app = express();
app.use(express.json({ limit: "2mb" }));
app.use(express.static("public"));

const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 20 * 1024 * 1024 } });
const wrap = (fn) => (req, res, next) => Promise.resolve(fn(req, res, next)).catch(next);
const httpErr = (status, msg) => Object.assign(new Error(msg), { status });

// ---------- Skills ----------
app.get("/api/skills", wrap(async (_req, res) => res.json(await store.listSkills())));
app.post("/api/skills", wrap(async (req, res) => res.json(await store.saveSkill({ ...req.body, id: undefined }))));
app.put("/api/skills/:id", wrap(async (req, res) => {
  if (!(await store.getSkill(req.params.id))) throw httpErr(404, "ไม่พบสกิล");
  res.json(await store.saveSkill({ ...req.body, id: req.params.id }));
}));
app.delete("/api/skills/:id", wrap(async (req, res) => { await store.deleteSkill(req.params.id); res.json({ ok: true }); }));

// อัปโหลดไฟล์สกิล (.md / .txt) — รองรับ frontmatter แบบ SKILL.md (name:, description:)
app.post("/api/skills/upload", upload.array("files", 20), wrap(async (req, res) => {
  const saved = [];
  for (const f of req.files || []) {
    let content = f.buffer.toString("utf8");
    let name = path.parse(Buffer.from(f.originalname, "latin1").toString("utf8")).name;
    let description = "";
    const fm = content.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
    if (fm) {
      name = fm[1].match(/^name:\s*(.+)$/m)?.[1].trim().replace(/^["']|["']$/g, "") || name;
      description = fm[1].match(/^description:\s*(.+)$/m)?.[1].trim().replace(/^["']|["']$/g, "") || "";
      content = content.slice(fm[0].length);
    }
    saved.push(await store.saveSkill({ name, description, content }));
  }
  res.json(saved);
}));

// ---------- Settings ----------
const mask = (k) => (k ? k.slice(0, 4) + "…" + k.slice(-4) : "");
app.get("/api/settings", wrap(async (_req, res) => {
  const s = await store.getSettings();
  res.json({ anthropicKey: mask(s.anthropicKey), elevenKey: mask(s.elevenKey), elevenModel: s.elevenModel, defaultVoiceId: s.defaultVoiceId, fromEnv: s.fromEnv });
}));
app.post("/api/settings", wrap(async (req, res) => { await store.saveSettings(req.body); res.json({ ok: true }); }));
app.get("/api/voices", wrap(async (_req, res) => res.json(await eleven.listVoices((await store.getSettings()).elevenKey))));

// ---------- Projects ----------
app.get("/api/projects", wrap(async (_req, res) => res.json(await store.listProjects())));

app.post("/api/projects", wrap(async (req, res) => {
  const { skillId, mode = "ai", topic = "", duration = 60, extra = "", script = "" } = req.body;
  const skill = skillId ? await store.getSkill(skillId) : null;
  const { anthropicKey } = await store.getSettings();
  let result;
  if (mode === "ai") {
    if (!topic.trim()) throw httpErr(400, "ใส่หัวข้อ/ไอเดียก่อน");
    result = await claude.generateScript({ apiKey: anthropicKey, skill, topic, duration: Number(duration) || 60, extra });
  } else if (mode === "own-ai") {
    if (!script.trim()) throw httpErr(400, "วางบทก่อน");
    result = await claude.splitScript({ apiKey: anthropicKey, skill, script });
  } else {
    if (!script.trim()) throw httpErr(400, "วางบทก่อน");
    result = claude.splitScriptLocal(script);
  }
  const project = await store.saveProject({
    id: store.newId(),
    createdAt: new Date().toISOString(),
    title: result.title,
    skillId: skill?.id || null,
    voiceId: skill?.style.voiceId || "",
    input: { mode, topic, duration, extra, script },
    scenes: result.scenes.map((s) => ({ ...s, audio: null, image: null })),
    render: null,
  });
  res.json(project);
}));

async function loadProject(id) {
  const p = await store.getProject(id);
  if (!p) throw httpErr(404, "ไม่พบโปรเจกต์");
  return p;
}
app.get("/api/projects/:id", wrap(async (req, res) => res.json(await loadProject(req.params.id))));
app.delete("/api/projects/:id", wrap(async (req, res) => { await store.deleteProject(req.params.id); res.json({ ok: true }); }));

// แก้บท/ฉาก: ถ้าบทพากย์เปลี่ยน เสียงเดิมถือว่าใช้ไม่ได้
app.put("/api/projects/:id", wrap(async (req, res) => {
  const p = await loadProject(req.params.id);
  if (typeof req.body.title === "string") p.title = req.body.title;
  if (typeof req.body.voiceId === "string") p.voiceId = req.body.voiceId;
  if (typeof req.body.skillId === "string" || req.body.skillId === null) p.skillId = req.body.skillId;
  if (Array.isArray(req.body.scenes)) {
    p.scenes = req.body.scenes.map((s, i) => {
      const narration = String(s.narration || "");
      const old = s.audio ? p.scenes.find((o) => o.audio === s.audio) : null;
      const keepAudio = old && old.narration === narration;
      const oldImg = s.image ? p.scenes.find((o) => o.image === s.image) : null;
      return {
        narration,
        caption: String(s.caption || ""),
        visual: String(s.visual || ""),
        audio: keepAudio ? old.audio : null,
        voiceId: keepAudio ? old.voiceId : null,
        image: oldImg ? oldImg.image : null,
      };
    });
  }
  res.json(await store.saveProject(p));
}));

// พากย์เสียงด้วย ElevenLabs (ทุกฉาก หรือ ?scene=i)
app.post("/api/projects/:id/voice", wrap(async (req, res) => {
  const p = await loadProject(req.params.id);
  const settings = await store.getSettings();
  const voiceId = p.voiceId || settings.defaultVoiceId;
  const dir = store.projectDir(p.id);
  const only = req.query.scene !== undefined ? Number(req.query.scene) : null;
  for (let i = 0; i < p.scenes.length; i++) {
    if (only !== null && i !== only) continue;
    const s = p.scenes[i];
    if (!s.narration.trim()) continue;
    if (only === null && s.audio && s.voiceId === voiceId) continue; // มีเสียงแล้ว ไม่เสียเครดิตซ้ำ
    const buf = await eleven.tts({ apiKey: settings.elevenKey, voiceId, text: s.narration, modelId: settings.elevenModel });
    const name = `voice_${i}_${Date.now()}.mp3`;
    await fs.writeFile(path.join(dir, name), buf);
    if (s.audio) await fs.rm(path.join(dir, s.audio), { force: true });
    s.audio = name;
    s.voiceId = voiceId;
  }
  res.json(await store.saveProject(p));
}));

// อัปโหลดภาพประกอบฉาก
app.post("/api/projects/:id/scenes/:i/image", upload.single("image"), wrap(async (req, res) => {
  const p = await loadProject(req.params.id);
  const i = Number(req.params.i);
  if (!p.scenes[i]) throw httpErr(404, "ไม่พบฉาก");
  if (!req.file || !/^image\/(png|jpe?g|webp)$/.test(req.file.mimetype)) throw httpErr(400, "รองรับ png / jpg / webp");
  const ext = { "image/png": "png", "image/webp": "webp" }[req.file.mimetype] || "jpg";
  const dir = store.projectDir(p.id);
  const name = `img_${i}_${Date.now()}.${ext}`;
  await fs.writeFile(path.join(dir, name), req.file.buffer);
  if (p.scenes[i].image) await fs.rm(path.join(dir, p.scenes[i].image), { force: true });
  p.scenes[i].image = name;
  res.json(await store.saveProject(p));
}));
app.delete("/api/projects/:id/scenes/:i/image", wrap(async (req, res) => {
  const p = await loadProject(req.params.id);
  const s = p.scenes[Number(req.params.i)];
  if (s?.image) { await fs.rm(path.join(store.projectDir(p.id), s.image), { force: true }); s.image = null; }
  res.json(await store.saveProject(p));
}));

// เรนเดอร์คลิป (ทำงานเบื้องหลัง หน้าเว็บ poll สถานะเอา)
const rendering = new Set();
app.post("/api/projects/:id/render", wrap(async (req, res) => {
  const p = await loadProject(req.params.id);
  if (rendering.has(p.id)) throw httpErr(409, "กำลังเรนเดอร์อยู่");
  if (!p.scenes.length) throw httpErr(400, "ยังไม่มีฉาก");
  const skill = p.skillId ? await store.getSkill(p.skillId) : null;
  const style = { aspect: "9:16", bgColor: "#111827", textColor: "#FFFFFF", ...(skill?.style || {}), ...(req.body?.style || {}) };
  rendering.add(p.id);
  p.render = { status: "running", progress: 0, message: "เริ่ม", file: null, error: null };
  await store.saveProject(p);
  res.json(p);

  const update = async (patch) => {
    const cur = await store.getProject(p.id);
    if (!cur) return;
    cur.render = { ...cur.render, ...patch };
    await store.saveProject(cur);
  };
  renderProject({ project: p, dir: store.projectDir(p.id), style, onProgress: (progress, message) => update({ progress, message }) })
    .then((file) => update({ status: "done", progress: 1, message: "เสร็จแล้ว", file: `${file}?v=${Date.now()}` }))
    .catch((e) => update({ status: "error", error: e.message }))
    .finally(() => rendering.delete(p.id));
}));

// ไฟล์ในโปรเจกต์ (เสียง/ภาพ/คลิป)
app.get("/files/:id/:file", wrap(async (req, res) => {
  if (!/^[\w.-]+$/.test(req.params.file)) throw httpErr(400, "bad file");
  res.sendFile(path.join(store.projectDir(req.params.id), req.params.file));
}));

app.use((err, _req, res, _next) => {
  const status = err.status && err.status >= 400 && err.status < 600 ? err.status : 500;
  if (status >= 500) console.error(err);
  res.status(status).json({ error: err.message || "เกิดข้อผิดพลาด" });
});

const PORT = Number(process.env.PORT) || 3000;
app.listen(PORT, () => console.log(`Clip Studio: http://localhost:${PORT}`));
