/* maijev GUI — vanilla JS, no build step. Talks to server.py over
   fetch + SSE; all state is derived from the job's work_dir. */
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const api = async (path, opts = {}) => {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  return res.json();
};
const toast = (msg, ms = 2600) => {
  const el = $('#toast');
  el.textContent = msg; el.classList.add('show');
  clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove('show'), ms);
};
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

let env = {};
let mode = 'zh';
let selected = null;
let es = null;

/* ---------- env chips ---------- */
async function loadEnv() {
  env = await api('/api/env');
  const chip = (ok, label, title) =>
    `<span class="chip ${ok ? 'ok' : 'no'}" title="${title || ''}">${label} ${ok ? '✓' : '✗'}</span>`;
  $('#envChips').innerHTML =
    chip(env.asr, 'ASR', 'OPENROUTER_API_KEY') +
    chip(env.llm, 'Gemini', 'GEMINI_AGENT_PLATFORM_API_KEY / GEMINI_API_KEY') +
    chip(!!env.jev, env.jev ? `Jev·${env.jev}` : 'Jev', 'TYPESAFE_API_KEY 或 Cloudflare');
  $('#runsDir').textContent = `runs: ${env.runs}`;
  $('#model').placeholder = `默认 ${env.default_model}`;
  $('#ocrHint').textContent = env.jev
    ? `有 Jev（${env.jev}）：OCR 先分类筛选再进 pre-pass。`
    : '没有 Jev 凭据：OCR 原文直接进 pre-pass，同样可用。';
}

/* ---------- file browser ---------- */
async function browse(path) {
  const box = $('#browser');
  try {
    const d = await api(`/api/browse?path=${encodeURIComponent(path || '')}`);
    box.innerHTML =
      `<div class="path">${esc(d.path)}</div>` +
      (d.parent ? `<div class="item dir" data-dir="${esc(d.parent)}">..</div>` : '') +
      d.dirs.map((n) => `<div class="item dir" data-dir="${esc(d.path + '/' + n)}">${esc(n)}</div>`).join('') +
      d.files.map((n) => `<div class="item file" data-file="${esc(d.path + '/' + n)}">${esc(n)}</div>`).join('');
    box.classList.add('open');
  } catch (e) { toast(e.message); }
}
$('#browseBtn').onclick = () => {
  const box = $('#browser');
  if (box.classList.contains('open')) return box.classList.remove('open');
  const v = $('#input').value.trim();
  browse(v && v.startsWith('/') ? v.replace(/\/[^/]*$/, '') || '/' : '');
};
$('#browser').onclick = (e) => {
  const el = e.target.closest('.item');
  if (!el) return;
  if (el.dataset.dir) browse(el.dataset.dir);
  else { $('#input').value = el.dataset.file; $('#browser').classList.remove('open'); }
};

/* ---------- new job form ---------- */
$('#modes').onclick = (e) => {
  const b = e.target.closest('button'); if (!b) return;
  mode = b.dataset.mode;
  [...$('#modes').children].forEach((x) => x.classList.toggle('on', x === b));
  $('#zhOpts').style.display = mode === 'zh' ? '' : 'none';
};
$('#startBtn').onclick = async () => {
  const input = $('#input').value.trim();
  if (!input) return toast('先填输入路径或来源');
  const body = {
    input, mode,
    name: $('#name').value.trim() || null,
    prepass: $('#prepass').checked,
    ocr_json: $('#ocr').value.trim() || null,
    extract_frames: $('#frames').checked,
    model: $('#model').value.trim() || null,
    thinking_level: $('#thinking').value || null,
  };
  try {
    $('#startBtn').disabled = true;
    const snap = await api('/api/jobs', { method: 'POST', body: JSON.stringify(body) });
    toast(`已启动 ${snap.id}`);
    await refreshJobs();
    select(snap.id);
  } catch (e) { toast(e.message, 4000); } finally { $('#startBtn').disabled = false; }
};

/* ---------- job list ---------- */
async function refreshJobs() {
  const jobs = await api('/api/jobs');
  $('#jobs').innerHTML = jobs.map((j) => {
    const dot = j.running ? 'run' : j.exit_code ? 'err' : (j.zh || j.ja || j.asr) ? 'ok' : '';
    const what = j.zh ? 'zh' : j.ja ? 'ja' : j.asr ? 'asr' : '…';
    return `<li data-id="${esc(j.id)}" class="${j.id === selected ? 'sel' : ''}">
      <span class="dot ${dot}"></span><span class="name">${esc(j.id)}</span>
      <span class="hint" style="margin:0">${what}</span></li>`;
  }).join('') || '<li class="hint">还没有任务</li>';
}
$('#jobs').onclick = (e) => { const li = e.target.closest('li[data-id]'); if (li) select(li.dataset.id); };

/* ---------- detail ---------- */
function stageModel(s) {
  const job = s.job || {};
  const m = job.mode || (s.zh ? 'zh' : s.ja ? 'ja' : 'asr');
  const wantGloss = m === 'zh' && (job.prepass !== false || job.ocr_json);
  const st = [
    { n: '01', t: '音频', done: s.audio, sub: s.audio ? 'audio.wav' : (s.running ? '下载 / 抽取中' : '') },
    { n: '02', t: 'ASR', done: s.asr, sub: s.chunks_total ? `${s.chunks_done}/${s.chunks_total} chunks` : '' },
    { n: '03', t: '合并', done: s.ja, skip: m === 'asr', sub: s.merge_batches ? `${s.merge_batches} batch` : '' },
    { n: '04', t: '词库', done: s.glossary, skip: !wantGloss, sub: s.glossary ? 'glossary.md' : '' },
    { n: '05', t: '翻译', done: s.zh, skip: m !== 'zh', sub: s.zh_batches ? `${s.zh_batches} batch` : '' },
    { n: '06', t: '完成', done: s.finished, sub: s.finished && s.timings ? `${Math.round(s.timings.wall_seconds)}s` : '' },
  ];
  let activeSet = false;
  for (const x of st) {
    if (x.skip) { x.cls = 'skip'; continue; }
    if (x.done) x.cls = 'done';
    else if (s.running && !activeSet) { x.cls = 'active'; activeSet = true; }
    else if (!s.running && s.exit_code && !activeSet) { x.cls = 'err'; x.sub = `exit ${s.exit_code}`; activeSet = true; }
    else x.cls = '';
  }
  return st;
}

function renderState(s) {
  const d = $('#detail');
  if (!d.dataset.id || d.dataset.id !== s.id) {
    d.innerHTML = ''; d.dataset.id = s.id;
    d.appendChild($('#detailTpl').content.cloneNode(true));
    $('.jid', d).textContent = s.id;
    $('.rerunBtn', d).onclick = () => rerun(s.id);
    $('.cancelBtn', d).onclick = () => api(`/api/jobs/${s.id}`, { method: 'DELETE' }).then(() => toast('已停止')).catch((e) => toast(e.message));
    $('.saveGloss', d).onclick = () => saveGlossary(s.id, false);
    $('.saveRerun', d).onclick = () => saveGlossary(s.id, true);
    loadGlossary(s.id);
  }
  const job = s.job || {};
  $('.src', d).textContent = [job.input, job.model && `model=${job.model}`, job.ocr_json && 'ocr', job.prepass && 'prepass'].filter(Boolean).join('  ·  ');
  $('.cancelBtn', d).disabled = !s.running;
  $('.rerunBtn', d).disabled = s.running || !job.input;
  $('.saveRerun', d).disabled = s.running || !job.input;

  $('.stages', d).innerHTML = stageModel(s).map((x) =>
    `<div class="stage ${x.cls}"><div class="n">${x.n}</div><div class="t">${x.t}</div><div class="s">${esc(x.sub || '')}</div></div>`).join('');

  const t = s.timings || {};
  const facts = [];
  if (t.asr_seconds) facts.push(`ASR <b>${t.asr_seconds.toFixed(0)}s</b>`);
  if (t.merge_seconds) facts.push(`合并 <b>${t.merge_seconds.toFixed(0)}s</b>`);
  if (t.prepass_seconds) facts.push(`pre-pass <b>${t.prepass_seconds.toFixed(0)}s</b>`);
  if (t.translate_seconds) facts.push(`翻译 <b>${t.translate_seconds.toFixed(0)}s</b>`);
  if (s.cost_usd != null) facts.push(`ASR 费用 <b>$${s.cost_usd.toFixed(4)}</b>`);
  $('.facts', d).innerHTML = facts.map((f) => `<div class="fact">${f}</div>`).join('');

  const main = s.zh ? 'out_zh.srt' : s.ja ? 'out_llm_ja.srt' : s.asr ? 'out.srt' : null;
  $('.files', d).innerHTML = s.files.filter((f) => f.endsWith('.srt') || f === 'timings.json' || f === 'source_meta.json')
    .map((f) => `<a class="${f === main ? 'main' : ''}" href="/api/jobs/${s.id}/files/${f}?download=1">↓ ${f}</a>`).join('');
  if (main && $('.srt', d).dataset.file !== main + s.mtime) {
    $('.srt', d).dataset.file = main + s.mtime;
    $('.srtTitle', d).textContent = `字幕预览 · ${main}`;
    fetch(`/api/jobs/${s.id}/files/${main}`).then((r) => r.text()).then((txt) => { $('.srt', d).textContent = txt; });
  }
  if (s.glossary && !s.running && $('.glossAuto', d).dataset.m !== String(s.mtime)) {
    $('.glossAuto', d).dataset.m = String(s.mtime);
    loadGlossary(s.id, true);
  }
}

function appendLog(line) {
  const pre = $('#detail .log'); if (!pre) return;
  const cls = /SUCCESS/.test(line) ? 'ok' : /ERROR|Traceback|failed/.test(line) ? 'err' : line.startsWith('$ ') ? 'cmd' : '';
  const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
  const span = document.createElement('span');
  span.className = cls; span.textContent = line + '\n';
  pre.appendChild(span);
  while (pre.childNodes.length > 2000) pre.removeChild(pre.firstChild);
  if (atBottom) pre.scrollTop = pre.scrollHeight;
}

function select(id) {
  selected = id;
  [...$('#jobs').children].forEach((li) => li.classList.toggle('sel', li.dataset.id === id));
  if (es) es.close();
  $('#detail').dataset.id = '';
  es = new EventSource(`/api/jobs/${id}/events`);
  es.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === 'log') appendLog(msg.line);
    else if (msg.type === 'state') { renderState(msg); refreshJobs(); }
    else if (msg.type === 'end') { es.close(); refreshJobs(); }
  };
  es.onerror = () => { es.close(); };
}

/* ---------- glossary ---------- */
async function loadGlossary(id, autoOnly = false) {
  const g = await api(`/api/jobs/${id}/glossary`);
  const d = $('#detail');
  $('.glossAuto', d).value = g.auto;
  if (!autoOnly) $('.glossUser', d).value = g.user;
}
async function saveGlossary(id, andRerun) {
  const text = $('#detail .glossUser').value;
  try {
    const r = await api(`/api/jobs/${id}/glossary`, { method: 'PUT', body: JSON.stringify({ text }) });
    toast(`词库已保存（${r.lines} 条）`);
    if (andRerun) await rerun(id);
  } catch (e) { toast(e.message, 4000); }
}
async function rerun(id) {
  try {
    await api(`/api/jobs/${id}/rerun`, { method: 'POST' });
    toast('已重跑');
    select(id);
  } catch (e) { toast(e.message, 4000); }
}

/* ---------- boot ---------- */
loadEnv().then(refreshJobs).then(() => {
  const first = $('#jobs li[data-id]');
  if (first) select(first.dataset.id);
});
setInterval(() => { if (!es || es.readyState === EventSource.CLOSED) refreshJobs(); }, 8000);
