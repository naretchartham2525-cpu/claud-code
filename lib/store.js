// เก็บข้อมูลเป็นไฟล์ JSON ในโฟลเดอร์ data/ (ไม่มี DB ให้ติดตั้ง)
import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";

export const DATA_DIR = path.resolve(process.env.DATA_DIR || "data");
const SKILLS_DIR = path.join(DATA_DIR, "skills");
export const PROJECTS_DIR = path.join(DATA_DIR, "projects");
const SETTINGS_FILE = path.join(DATA_DIR, "settings.json");

await fs.mkdir(SKILLS_DIR, { recursive: true });
await fs.mkdir(PROJECTS_DIR, { recursive: true });

export const newId = () => crypto.randomBytes(6).toString("hex");
const safeId = (id) => {
  if (!/^[a-f0-9]{6,32}$/.test(id)) throw Object.assign(new Error("bad id"), { status: 400 });
  return id;
};

async function readJson(file, fallback = null) {
  try {
    return JSON.parse(await fs.readFile(file, "utf8"));
  } catch (e) {
    if (e.code === "ENOENT") return fallback;
    throw e;
  }
}
async function writeJson(file, data) {
  const tmp = file + ".tmp";
  await fs.writeFile(tmp, JSON.stringify(data, null, 2));
  await fs.rename(tmp, file);
}

// ---------- Skills ----------
export async function listSkills() {
  const files = (await fs.readdir(SKILLS_DIR)).filter((f) => f.endsWith(".json"));
  const skills = await Promise.all(files.map((f) => readJson(path.join(SKILLS_DIR, f))));
  return skills.filter(Boolean).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}
export const getSkill = (id) => readJson(path.join(SKILLS_DIR, safeId(id) + ".json"));
export async function saveSkill(input) {
  const now = new Date().toISOString();
  const id = input.id ? safeId(input.id) : newId();
  const old = (await getSkill(id)) || { createdAt: now };
  const skill = {
    ...old,
    id,
    name: String(input.name || "ไม่มีชื่อ").slice(0, 120),
    description: String(input.description || "").slice(0, 500),
    content: String(input.content || ""),
    style: {
      aspect: ["9:16", "16:9", "1:1"].includes(input.style?.aspect) ? input.style.aspect : "9:16",
      voiceId: String(input.style?.voiceId || ""),
      bgColor: /^#[0-9a-fA-F]{6}$/.test(input.style?.bgColor || "") ? input.style.bgColor : "#111827",
      textColor: /^#[0-9a-fA-F]{6}$/.test(input.style?.textColor || "") ? input.style.textColor : "#FFFFFF",
    },
    updatedAt: now,
  };
  await writeJson(path.join(SKILLS_DIR, id + ".json"), skill);
  return skill;
}
export const deleteSkill = (id) => fs.rm(path.join(SKILLS_DIR, safeId(id) + ".json"), { force: true });

// ---------- Projects ----------
export const projectDir = (id) => path.join(PROJECTS_DIR, safeId(id));
export async function listProjects() {
  const dirs = await fs.readdir(PROJECTS_DIR);
  const ps = await Promise.all(dirs.map((d) => readJson(path.join(PROJECTS_DIR, d, "project.json"))));
  return ps.filter(Boolean).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}
export const getProject = (id) => readJson(path.join(projectDir(id), "project.json"));
export async function saveProject(p) {
  p.updatedAt = new Date().toISOString();
  await fs.mkdir(projectDir(p.id), { recursive: true });
  await writeJson(path.join(projectDir(p.id), "project.json"), p);
  return p;
}
export const deleteProject = (id) => fs.rm(projectDir(id), { recursive: true, force: true });

// ---------- Settings (API keys) ----------
// ค่าใน .env มาก่อน ถ้าไม่มีค่อยใช้ที่กรอกในหน้าเว็บ (เก็บใน data/settings.json — อย่า commit)
export async function getSettings() {
  const s = (await readJson(SETTINGS_FILE, {})) || {};
  return {
    anthropicKey: process.env.ANTHROPIC_API_KEY || s.anthropicKey || "",
    elevenKey: process.env.ELEVENLABS_API_KEY || s.elevenKey || "",
    elevenModel: s.elevenModel || process.env.ELEVENLABS_MODEL || "eleven_v3",
    defaultVoiceId: s.defaultVoiceId || "",
    fromEnv: { anthropic: !!process.env.ANTHROPIC_API_KEY, eleven: !!process.env.ELEVENLABS_API_KEY },
  };
}
export async function saveSettings(input) {
  const s = (await readJson(SETTINGS_FILE, {})) || {};
  for (const k of ["anthropicKey", "elevenKey", "elevenModel", "defaultVoiceId"]) {
    if (typeof input[k] === "string" && input[k] !== "") s[k] = input[k].trim();
  }
  await writeJson(SETTINGS_FILE, s);
}
