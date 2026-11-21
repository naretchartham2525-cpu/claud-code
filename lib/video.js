// ประกอบคลิปด้วย ffmpeg: ภาพ (หรือพื้นสี) + เสียงพากย์ + ซับไทย (libass) ต่อกันทีละฉาก
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";

const SIZES = { "9:16": [1080, 1920], "16:9": [1920, 1080], "1:1": [1080, 1080] };
const FPS = 30;
const FONT = process.env.SUB_FONT || "Garuda"; // ฟอนต์ไทย (apt install fonts-thai-tlwg)

function sh(cmd, args) {
  return new Promise((resolve, reject) => {
    const p = spawn(cmd, args, { stdio: ["ignore", "pipe", "pipe"] });
    let out = "", err = "";
    p.stdout.on("data", (d) => (out += d));
    p.stderr.on("data", (d) => (err = (err + d).slice(-4000)));
    p.on("error", reject);
    p.on("close", (code) => (code === 0 ? resolve(out) : reject(new Error(`${cmd} ล้มเหลว:\n${err}`))));
  });
}

export async function audioDuration(file) {
  const out = await sh("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", file]);
  return parseFloat(out) || 0;
}

// นับความกว้างตัวอักษรแบบคร่าวๆ (ไม่นับสระบน/ล่าง วรรณยุกต์ไทย)
const visLen = (s) => s.replace(/[\u0E31\u0E34-\u0E3A\u0E47-\u0E4E]/g, "").length;
const seg = new Intl.Segmenter("th", { granularity: "word" });

// ตัดบรรทัดตามคำ (ภาษาไทยไม่มีเว้นวรรค จึงใช้ Intl.Segmenter)
export function wrapText(text, maxChars) {
  const lines = [];
  let line = "";
  for (const { segment } of seg.segment(text.replace(/\s+/g, " ").trim())) {
    if (line && visLen(line + segment) > maxChars) {
      lines.push(line.trim());
      line = segment.trimStart();
    } else line += segment;
  }
  if (line.trim()) lines.push(line.trim());
  return lines;
}

const assTime = (t) => {
  const cs = Math.round(t * 100);
  const h = Math.floor(cs / 360000), m = Math.floor(cs / 6000) % 60, s = Math.floor(cs / 100) % 60;
  return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${String(cs % 100).padStart(2, "0")}`;
};
const assColor = (hex) => `&H00${hex.slice(5, 7)}${hex.slice(3, 5)}${hex.slice(1, 3)}`.toUpperCase();
const assEscape = (s) => s.replace(/\\/g, "＼").replace(/[{}]/g, "");

// สร้างไฟล์ซับ ASS: แบ่งเป็นช่วงละ 2 บรรทัด จับเวลาตามสัดส่วนความยาวข้อความ
function buildAss({ text, duration, W, H, textColor }) {
  const maxChars = W < H ? 14 : W === H ? 18 : 30;
  const lines = wrapText(text, maxChars);
  const chunks = [];
  for (let i = 0; i < lines.length; i += 2) chunks.push(lines.slice(i, i + 2));
  const total = chunks.reduce((n, c) => n + visLen(c.join("")), 0) || 1;
  let t = 0;
  const events = chunks.map((c) => {
    const d = (visLen(c.join("")) / total) * duration;
    const ev = `Dialogue: 0,${assTime(t)},${assTime(t + d)},Main,,0,0,0,,${c.map(assEscape).join("\\N")}`;
    t += d;
    return ev;
  });
  const fontSize = Math.round(Math.min(W, H) * 0.095);
  return `[Script Info]
ScriptType: v4.00+
PlayResX: ${W}
PlayResY: ${H}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,${FONT},${fontSize},${assColor(textColor)},&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,${Math.round(fontSize / 12)},2,2,60,60,${Math.round(H * 0.14)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
${events.join("\n")}
`;
}

// เรนเดอร์ 1 ฉาก -> mp4
async function renderScene({ scene, dir, idx, W, H, style }) {
  const audio = scene.audio ? path.join(dir, scene.audio) : null;
  const image = scene.image ? path.join(dir, scene.image) : null;
  const text = (scene.caption || scene.narration || "").trim();
  // ไม่มีเสียง: ประมาณเวลาอ่านบทพากย์ ~12 ตัวอักษร/วินาที
  const audioLen = audio ? await audioDuration(audio) : 0;
  const duration = Math.max(2, (audioLen || visLen(scene.narration || text) / 12) + 0.4);
  const frames = Math.ceil(duration * FPS);

  const assFile = path.join(dir, `scene_${idx}.ass`);
  await fs.writeFile(assFile, buildAss({ text, duration, W, H, textColor: style.textColor }));
  const out = path.join(dir, `scene_${idx}.mp4`);

  const args = ["-y", "-hide_banner", "-loglevel", "error"];
  let vf;
  if (image) {
    args.push("-i", image);
    // ซูมช้าๆ (Ken Burns) ให้ภาพนิ่งดูมีชีวิต
    vf =
      `scale=${W * 2}:${H * 2}:force_original_aspect_ratio=increase,crop=${W * 2}:${H * 2},` +
      `zoompan=z='min(zoom+0.0006,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=${frames}:s=${W}x${H}:fps=${FPS}`;
  } else {
    args.push("-f", "lavfi", "-i", `color=c=${style.bgColor}:s=${W}x${H}:r=${FPS}:d=${duration}`);
    vf = "null";
  }
  if (audio) args.push("-i", audio);
  else args.push("-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo");

  const fontsDir = process.env.SUB_FONTS_DIR || "/usr/share/fonts/truetype/tlwg";
  const assEsc = assFile.replace(/\\/g, "/").replace(/:/g, "\\:").replace(/'/g, "\\'");
  vf += `,ass='${assEsc}':fontsdir='${fontsDir}',format=yuv420p`;

  args.push(
    "-filter_complex", `[0:v]${vf}[v];[1:a]apad,aresample=44100,aformat=channel_layouts=stereo[a]`,
    "-map", "[v]", "-map", "[a]",
    "-t", duration.toFixed(2), "-r", String(FPS),
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
    "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
    out,
  );
  await sh("ffmpeg", args);
  return out;
}

export async function renderProject({ project, dir, style, onProgress }) {
  const [W, H] = SIZES[style.aspect] || SIZES["9:16"];
  const parts = [];
  for (let i = 0; i < project.scenes.length; i++) {
    onProgress?.(i / (project.scenes.length + 1), `กำลังเรนเดอร์ฉาก ${i + 1}/${project.scenes.length}`);
    parts.push(await renderScene({ scene: project.scenes[i], dir, idx: i, W, H, style }));
  }
  onProgress?.(project.scenes.length / (project.scenes.length + 1), "กำลังต่อคลิป");
  const list = path.join(dir, "concat.txt");
  await fs.writeFile(list, parts.map((p) => `file '${path.basename(p)}'`).join("\n"));
  const out = path.join(dir, "output.mp4");
  await sh("ffmpeg", ["-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", list, "-c", "copy", "-movflags", "+faststart", out]);
  for (const p of parts) await fs.rm(p, { force: true });
  return "output.mp4";
}
