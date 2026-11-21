const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

async function api(url, opts = {}) {
  const o = { ...opts };
  if (o.body && !(o.body instanceof FormData)) {
    o.headers = { "Content-Type": "application/json" };
    o.body = JSON.stringify(o.body);
  }
  const res = await fetch(url, o);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "show" + (err ? " err" : "");
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = ""), err ? 6000 : 2500);
}
// กันกดซ้ำ + แสดง error
async function busy(btn, fn) {
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = "⏳ " + label;
  try { return await fn(); }
  catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; btn.textContent = label; }
}

// ---------- Tabs ----------
function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  if (name === "skills") loadSkills();
  if (name === "projects") loadProjects();
  if (name === "settings") loadSettings();
  if (name === "create") fillSkillSelect($("#c-skill"));
}
document.querySelectorAll("nav button").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));

// ---------- Voices ----------
let voices = null;
async function getVoices() {
  if (!voices) voices = await api("/api/voices");
  return voices;
}
function fillVoiceSelect(sel, value, emptyLabel) {
  const opts = (voices || []).map((v) => `<option value="${esc(v.id)}">${esc(v.name)}${v.labels?.accent ? " · " + esc(v.labels.accent) : ""}</option>`);
  sel.innerHTML = `<option value="">${emptyLabel}</option>` + opts.join("");
  if (value && !(voices || []).some((v) => v.id === value)) sel.innerHTML += `<option value="${esc(value)}">${esc(value)}</option>`;
  sel.value = value || "";
}
getVoices().catch(() => {}); // โหลดไว้ก่อน ถ้ายังไม่มี key ก็ข้าม

// ---------- Skills ----------
let skills = [];
async function fillSkillSelect(sel, value) {
  skills = await api("/api/skills");
  sel.innerHTML = `<option value="">(ไม่ใช้สกิล)</option>` + skills.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
  if (value !== undefined) sel.value = value || "";
}
async function loadSkills() {
  skills = await api("/api/skills");
  fillVoiceSelect($("#s-voice"), $("#s-voice").value, "(ใช้ค่าเริ่มต้น)");
  $("#s-list").innerHTML = skills.length
    ? skills.map((s) => `
      <div class="item">
        <div class="meta"><b>${esc(s.name)}</b><span class="muted">${esc(s.description || s.content.slice(0, 120))}</span>
          <div class="muted">${s.style.aspect} · ${s.content.length.toLocaleString()} ตัวอักษร</div></div>
        <div class="row">
          <button data-use="${s.id}" class="primary">ใช้ทำคลิป</button>
          <button data-edit="${s.id}">แก้ไข</button>
          <button data-del="${s.id}" class="danger">ลบ</button>
        </div>
      </div>`).join("")
    : `<p class="muted">ยังไม่มีสกิล — อัปโหลดไฟล์หรือพิมพ์ด้านบน</p>`;
}
$("#s-list").onclick = async (e) => {
  const { use, edit, del } = e.target.dataset;
  if (use) { showTab("create"); await fillSkillSelect($("#c-skill"), use); }
  if (edit) {
    const s = skills.find((x) => x.id === edit);
    $("#s-id").value = s.id; $("#s-name").value = s.name; $("#s-desc").value = s.description; $("#s-content").value = s.content;
    $("#s-aspect").value = s.style.aspect; $("#s-bg").value = s.style.bgColor; $("#s-fg").value = s.style.textColor;
    fillVoiceSelect($("#s-voice"), s.style.voiceId, "(ใช้ค่าเริ่มต้น)");
    $("#s-form-title").textContent = "แก้ไขสกิล: " + s.name;
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  if (del && confirm("ลบสกิลนี้?")) { await api("/api/skills/" + del, { method: "DELETE" }); loadSkills(); }
};
function resetSkillForm() {
  for (const id of ["s-id", "s-name", "s-desc", "s-content"]) $("#" + id).value = "";
  $("#s-aspect").value = "9:16"; $("#s-bg").value = "#111827"; $("#s-fg").value = "#ffffff"; $("#s-voice").value = "";
  $("#s-form-title").textContent = "เพิ่มสกิลใหม่";
}
$("#s-reset").onclick = resetSkillForm;
$("#s-save").onclick = (e) => busy(e.target, async () => {
  const body = {
    name: $("#s-name").value, description: $("#s-desc").value, content: $("#s-content").value,
    style: { aspect: $("#s-aspect").value, bgColor: $("#s-bg").value, textColor: $("#s-fg").value, voiceId: $("#s-voice").value },
  };
  if (!body.name.trim() || !body.content.trim()) throw new Error("ใส่ชื่อและเนื้อหาสกิล");
  const id = $("#s-id").value;
  await api(id ? "/api/skills/" + id : "/api/skills", { method: id ? "PUT" : "POST", body });
  toast("บันทึกสกิลแล้ว"); resetSkillForm(); loadSkills();
});
$("#s-files").onchange = async (e) => {
  const fd = new FormData();
  for (const f of e.target.files) fd.append("files", f);
  try {
    const saved = await api("/api/skills/upload", { method: "POST", body: fd });
    toast(`เพิ่ม ${saved.length} สกิลแล้ว`); loadSkills();
  } catch (err) { toast(err.message, true); }
  e.target.value = "";
};

// ---------- Create ----------
document.querySelectorAll("input[name=mode]").forEach((r) => (r.onchange = () => {
  const ai = $("input[name=mode]:checked").value === "ai";
  $("#c-ai").classList.toggle("hidden", !ai);
  $("#c-script").classList.toggle("hidden", ai);
}));
$("#c-go").onclick = (e) => busy(e.target, async () => {
  const p = await api("/api/projects", {
    method: "POST",
    body: {
      skillId: $("#c-skill").value, mode: $("input[name=mode]:checked").value,
      topic: $("#c-topic").value, duration: $("#c-duration").value, extra: $("#c-extra").value, script: $("#c-script").value,
    },
  });
  toast("ได้บทแล้ว ตรวจ/แก้ได้เลย");
  openEditor(p.id);
});

// ---------- Projects ----------
async function loadProjects() {
  const ps = await api("/api/projects");
  $("#p-list").innerHTML = ps.length
    ? ps.map((p) => `
      <div class="item">
        <div class="meta"><b>${esc(p.title)}</b>
          <span class="muted">${p.scenes.length} ฉาก · ${new Date(p.updatedAt).toLocaleString("th-TH")}${p.render?.status === "done" ? " · ✅ มีคลิป" : ""}</span></div>
        <div class="row"><button data-open="${p.id}" class="primary">เปิด</button><button data-del="${p.id}" class="danger">ลบ</button></div>
      </div>`).join("")
    : `<p class="muted">ยังไม่มีโปรเจกต์</p>`;
}
$("#p-list").onclick = async (e) => {
  const { open, del } = e.target.dataset;
  if (open) openEditor(open);
  if (del && confirm("ลบโปรเจกต์นี้ (รวมไฟล์เสียง/คลิป)?")) { await api("/api/projects/" + del, { method: "DELETE" }); loadProjects(); }
};

// ---------- Editor ----------
let cur = null;
let pollTimer = null;
const fileUrl = (name) => `/files/${cur.id}/${name}`;

async function openEditor(id) {
  cur = await api("/api/projects/" + id);
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-editor"));
  document.querySelectorAll("nav button").forEach((b) => b.classList.remove("active"));
  await fillSkillSelect($("#e-skill"), cur.skillId);
  fillVoiceSelect($("#e-voice"), cur.voiceId, "(ค่าเริ่มต้น)");
  renderEditor();
  watchRender();
}

function renderEditor() {
  $("#e-title").value = cur.title;
  $("#e-scenes").innerHTML = cur.scenes.map((s, i) => `
    <div class="scene" data-i="${i}">
      <header class="row between"><b>ฉาก ${i + 1}</b>
        <span class="row">
          <button data-act="up" ${i === 0 ? "disabled" : ""}>↑</button>
          <button data-act="down" ${i === cur.scenes.length - 1 ? "disabled" : ""}>↓</button>
          <button data-act="del" class="danger">ลบ</button>
        </span></header>
      <label>บทพากย์<textarea data-f="narration" rows="3">${esc(s.narration)}</textarea></label>
      <label>ข้อความบนจอ (ว่าง = ใช้บทพากย์เป็นซับ)<input data-f="caption" value="${esc(s.caption)}" /></label>
      <label>ภาพที่ควรใช้<input data-f="visual" value="${esc(s.visual)}" /></label>
      ${s.audio ? `<audio controls src="${fileUrl(s.audio)}"></audio>` : `<div class="muted">ยังไม่มีเสียง</div>`}
      ${s.image ? `<img class="thumb" src="${fileUrl(s.image)}" />` : ""}
      <div class="row">
        <button data-act="voice">🎙️ พากย์ฉากนี้</button>
        <label style="flex:0 0 auto"><span class="muted">ภาพประกอบ</span> <input type="file" data-act="img" accept="image/png,image/jpeg,image/webp" /></label>
        ${s.image ? `<button data-act="rmimg">ลบภาพ</button>` : ""}
      </div>
    </div>`).join("");
}

function collect() {
  document.querySelectorAll(".scene").forEach((el) => {
    const s = cur.scenes[el.dataset.i];
    el.querySelectorAll("[data-f]").forEach((f) => (s[f.dataset.f] = f.value));
  });
  cur.title = $("#e-title").value;
  cur.skillId = $("#e-skill").value || null;
  cur.voiceId = $("#e-voice").value;
}
async function save() {
  collect();
  cur = await api("/api/projects/" + cur.id, { method: "PUT", body: { title: cur.title, skillId: cur.skillId, voiceId: cur.voiceId, scenes: cur.scenes } });
  renderEditor();
}

$("#e-back").onclick = () => showTab("projects");
$("#e-save").onclick = (e) => busy(e.target, async () => { await save(); toast("บันทึกแล้ว"); });
$("#e-add").onclick = () => { collect(); cur.scenes.push({ narration: "", caption: "", visual: "", audio: null, image: null }); renderEditor(); };
$("#e-voice-all").onclick = (e) => busy(e.target, async () => {
  await save();
  if (!cur.voiceId && !(await api("/api/settings")).defaultVoiceId) throw new Error("เลือกเสียงก่อน (หรือตั้งเสียงเริ่มต้นในหน้าตั้งค่า)");
  cur = await api(`/api/projects/${cur.id}/voice`, { method: "POST" });
  renderEditor(); toast("พากย์เสร็จแล้ว");
});
$("#e-render").onclick = (e) => busy(e.target, async () => {
  await save();
  if (cur.scenes.some((s) => s.narration.trim() && !s.audio) && !confirm("บางฉากยังไม่มีเสียง จะเรนเดอร์แบบเงียบในฉากนั้น ต่อเลยไหม?")) return;
  cur = await api(`/api/projects/${cur.id}/render`, { method: "POST" });
  watchRender();
});

$("#e-scenes").onclick = async (e) => {
  const act = e.target.dataset.act;
  const el = e.target.closest(".scene");
  if (!act || !el || act === "img") return;
  const i = Number(el.dataset.i);
  collect();
  if (act === "up" || act === "down") {
    const j = act === "up" ? i - 1 : i + 1;
    [cur.scenes[i], cur.scenes[j]] = [cur.scenes[j], cur.scenes[i]];
    renderEditor();
  }
  if (act === "del" && confirm("ลบฉากนี้?")) { cur.scenes.splice(i, 1); renderEditor(); }
  if (act === "voice") busy(e.target, async () => {
    await save();
    cur = await api(`/api/projects/${cur.id}/voice?scene=${i}`, { method: "POST" });
    renderEditor(); toast(`พากย์ฉาก ${i + 1} แล้ว`);
  });
  if (act === "rmimg") busy(e.target, async () => {
    await save();
    cur = await api(`/api/projects/${cur.id}/scenes/${i}/image`, { method: "DELETE" });
    renderEditor();
  });
};
$("#e-scenes").onchange = async (e) => {
  if (e.target.dataset.act !== "img" || !e.target.files[0]) return;
  const i = Number(e.target.closest(".scene").dataset.i);
  try {
    await save();
    const fd = new FormData();
    fd.append("image", e.target.files[0]);
    cur = await api(`/api/projects/${cur.id}/scenes/${i}/image`, { method: "POST", body: fd });
    renderEditor();
  } catch (err) { toast(err.message, true); }
};

function showRender() {
  const r = cur.render;
  const st = $("#e-status"), v = $("#e-video"), dl = $("#e-download");
  if (!r) { st.textContent = ""; v.classList.add("hidden"); dl.classList.add("hidden"); return; }
  if (r.status === "running") st.innerHTML = `${esc(r.message)}<progress value="${r.progress}" max="1"></progress>`;
  if (r.status === "error") st.innerHTML = `<span style="color:var(--danger)">เรนเดอร์ล้มเหลว:</span><pre style="white-space:pre-wrap">${esc(r.error)}</pre>`;
  if (r.status === "done") {
    st.textContent = "✅ เรนเดอร์เสร็จ";
    const url = fileUrl(r.file);
    if (v.getAttribute("src") !== url) v.src = url;
    dl.href = url; dl.download = (cur.title || "clip") + ".mp4";
    v.classList.remove("hidden"); dl.classList.remove("hidden");
  }
}
function watchRender() {
  clearInterval(pollTimer);
  showRender();
  if (cur.render?.status !== "running") return;
  const id = cur.id;
  pollTimer = setInterval(async () => {
    if (!cur || cur.id !== id) return clearInterval(pollTimer);
    const p = await api("/api/projects/" + id).catch(() => null);
    if (!p) return;
    cur.render = p.render;
    showRender();
    if (p.render?.status !== "running") clearInterval(pollTimer);
  }, 1500);
}

// ---------- Settings ----------
async function loadSettings() {
  const s = await api("/api/settings");
  $("#st-a-cur").textContent = s.anthropicKey ? `(ตั้งแล้ว ${s.anthropicKey}${s.fromEnv.anthropic ? " จาก .env" : ""})` : "(ยังไม่ตั้ง)";
  $("#st-e-cur").textContent = s.elevenKey ? `(ตั้งแล้ว ${s.elevenKey}${s.fromEnv.eleven ? " จาก .env" : ""})` : "(ยังไม่ตั้ง)";
  $("#st-model").value = s.elevenModel;
  fillVoiceSelect($("#st-voice"), s.defaultVoiceId, voices ? "— เลือกเสียง —" : "— กดโหลดรายชื่อเสียง —");
}
$("#st-load-voices").onclick = (e) => busy(e.target, async () => {
  voices = null;
  await getVoices();
  fillVoiceSelect($("#st-voice"), $("#st-voice").value, "— เลือกเสียง —");
  toast(`โหลด ${voices.length} เสียง`);
});
$("#st-save").onclick = (e) => busy(e.target, async () => {
  await api("/api/settings", { method: "POST", body: { anthropicKey: $("#st-a").value, elevenKey: $("#st-e").value, elevenModel: $("#st-model").value, defaultVoiceId: $("#st-voice").value } });
  $("#st-a").value = $("#st-e").value = "";
  voices = null; getVoices().catch(() => {});
  toast("บันทึกแล้ว"); loadSettings();
});

showTab("create");
