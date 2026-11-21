// ElevenLabs Text-to-Speech (REST)
const BASE = "https://api.elevenlabs.io/v1";

async function check(res) {
  if (res.ok) return res;
  let msg = await res.text();
  try {
    const j = JSON.parse(msg);
    msg = j.detail?.message || j.detail?.status || JSON.stringify(j.detail || j);
  } catch {}
  throw Object.assign(new Error(`ElevenLabs ${res.status}: ${msg}`), { status: res.status === 401 ? 401 : 502 });
}

export async function listVoices(apiKey) {
  if (!apiKey) throw Object.assign(new Error("ยังไม่ได้ใส่ ElevenLabs API key"), { status: 400 });
  const res = await check(await fetch(`${BASE}/voices`, { headers: { "xi-api-key": apiKey } }));
  const { voices } = await res.json();
  return voices.map((v) => ({ id: v.voice_id, name: v.name, labels: v.labels, preview: v.preview_url }));
}

// คืนค่าเป็น Buffer ของไฟล์ mp3
export async function tts({ apiKey, voiceId, text, modelId = "eleven_v3" }) {
  if (!apiKey) throw Object.assign(new Error("ยังไม่ได้ใส่ ElevenLabs API key"), { status: 400 });
  if (!voiceId) throw Object.assign(new Error("ยังไม่ได้เลือกเสียง (voice)"), { status: 400 });
  const res = await check(
    await fetch(`${BASE}/text-to-speech/${encodeURIComponent(voiceId)}?output_format=mp3_44100_128`, {
      method: "POST",
      headers: { "xi-api-key": apiKey, "Content-Type": "application/json", Accept: "audio/mpeg" },
      body: JSON.stringify({ text, model_id: modelId }),
    }),
  );
  return Buffer.from(await res.arrayBuffer());
}
