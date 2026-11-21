// ให้ Claude คิดบท/แตกบทเป็นฉาก โดยใช้สกิลเป็น system prompt
import Anthropic from "@anthropic-ai/sdk";
import { z } from "zod";
import { betaZodOutputFormat } from "@anthropic-ai/sdk/helpers/beta/zod";

const MODEL = process.env.CLAUDE_MODEL || "claude-opus-5-5";

const ScriptSchema = z.object({
  title: z.string(),
  scenes: z.array(
    z.object({
      narration: z.string().describe("บทพากย์ของฉากนี้ อ่านออกเสียงได้ทันที"),
      caption: z.string().describe("ข้อความสั้นบนจอ (ว่างได้ ถ้าจะใช้บทพากย์เป็นซับ)"),
      visual: z.string().describe("คำบรรยายภาพ/ฟุตเทจที่ควรใช้ในฉากนี้"),
    }),
  ),
});

function client(apiKey) {
  // ถ้าไม่ส่ง key มา SDK จะหาเองจาก ANTHROPIC_API_KEY / ant auth login
  return apiKey ? new Anthropic({ apiKey }) : new Anthropic();
}

function systemPrompt(skill) {
  return [
    "คุณคือคนเขียนบทคลิปวิดีโอสั้น เขียนบทที่พากย์ออกเสียงแล้วฟังลื่น เป็นธรรมชาติ",
    "แบ่งเป็นฉาก ฉากละ 1-3 ประโยค ยาวพอดีกับภาพหนึ่งภาพ",
    "เขียนภาษาเดียวกับที่ผู้ใช้ขอ (ค่าเริ่มต้น: ไทย) ตัวเลขให้เขียนเป็นคำอ่านถ้าอ่านยาก",
    "ห้ามใส่คำกำกับเสียง วงเล็บ หรือ emoji ในบทพากย์",
    skill
      ? `\n## สกิล/สไตล์ที่ต้องทำตาม: ${skill.name}\n${skill.description || ""}\n\n${skill.content}`
      : "",
  ].join("\n");
}

async function run({ apiKey, skill, userText }) {
  let res;
  try {
    res = await client(apiKey).beta.messages.parse({
      model: MODEL,
      max_tokens: 16000,
      // ถ้าโมเดลหลักปฏิเสธ (refusal) ให้ API สลับไปโมเดลสำรองให้อัตโนมัติ
      betas: ["server-side-fallback-2026-07-01"],
      fallbacks: "default",
      output_config: { effort: "medium", format: betaZodOutputFormat(ScriptSchema) },
      system: systemPrompt(skill),
      messages: [{ role: "user", content: userText }],
    });
  } catch (e) {
    if (e instanceof Anthropic.AuthenticationError || /resolve authentication/i.test(e.message)) {
      throw Object.assign(new Error("Anthropic API key ไม่ถูกต้องหรือยังไม่ได้ใส่ (หน้าตั้งค่า หรือ .env)"), { status: 401 });
    }
    if (e instanceof Anthropic.RateLimitError) throw Object.assign(new Error("Claude ถูกจำกัดการใช้งานชั่วคราว ลองใหม่อีกสักครู่"), { status: 429 });
    if (e instanceof Anthropic.APIError) throw Object.assign(new Error(`Claude API ${e.status}: ${e.message}`), { status: 502 });
    throw e;
  }
  if (res.stop_reason === "refusal") {
    throw Object.assign(new Error("Claude ปฏิเสธคำขอนี้: " + (res.stop_details?.explanation || "")), { status: 422 });
  }
  if (res.stop_reason === "max_tokens") {
    throw Object.assign(new Error("บทยาวเกิน ลองลดความยาว/จำนวนฉาก"), { status: 422 });
  }
  if (!res.parsed_output) throw new Error("Claude ตอบกลับไม่ตรงรูปแบบ ลองใหม่อีกครั้ง");
  return res.parsed_output;
}

// โหมด 1: ให้ Claude คิดเนื้อเรื่องเองจากหัวข้อ
export function generateScript({ apiKey, skill, topic, duration = 60, extra = "" }) {
  return run({
    apiKey,
    skill,
    userText: `หัวข้อ/ไอเดีย: ${topic}\nความยาวคลิปประมาณ ${duration} วินาที\n${extra ? "เพิ่มเติม: " + extra : ""}\nเขียนบทให้ครบทุกฉาก`,
  });
}

// โหมด 2: ผู้ใช้วางบทเอง ให้ Claude แค่แบ่งฉาก + ใส่ caption/visual (ไม่แก้บทพากย์)
export function splitScript({ apiKey, skill, script }) {
  return run({
    apiKey,
    skill,
    userText:
      "นี่คือบทที่ผู้ใช้เขียนเอง ให้แบ่งเป็นฉาก ใส่ caption และ visual ให้แต่ละฉาก\n" +
      "ห้ามแก้ไข เพิ่ม หรือตัดคำในบทพากย์ narration ต้องเป็นข้อความเดิมเรียงต่อกันครบทุกคำ\n\n" +
      script,
  });
}

// โหมด 3: แบ่งฉากเองแบบไม่ใช้ AI (ย่อหน้าละฉาก)
export function splitScriptLocal(script) {
  const scenes = script
    .split(/\n\s*\n/)
    .map((t) => t.trim())
    .filter(Boolean)
    .map((narration) => ({ narration, caption: "", visual: "" }));
  return { title: scenes[0]?.narration.slice(0, 40) || "บทใหม่", scenes };
}
