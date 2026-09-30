/* herness — frontend logic */
const API_BASE = '/api';
let currentCheckId = null;
let uploadedFiles = [];
/* --- auth helpers --- */
async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) { opts.body = body; }
  else if (body) { opts.headers['Content-Type'] = 'application/x-www-form-urlencoded'; opts.body = body; }
  const r = await fetch(url, opts);
  const j = await r.json();
  if (!r.ok) throw new Error(j.detail || j.error || 'Ошибка ' + r.status);
  return j;
}
async function apiJson(url, data, method = 'PUT') {
  const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
  return r.json();
}
/* --- login --- */
function logout() { fetch('/api/auth/logout', { method: 'POST' }).then(() => window.location = '/login'); }
/* --- sidebar check list --- */
async function loadChecks() {
  const r = await fetch('/api/checks');
  const j = await r.json();
  const list = document.getElementById('check-list');
  list.innerHTML = j.checks.map(c => `<div class="check-item" onclick="openCheck(${c.id})">#${c.id} ${c.name || ''} <span class="badge badge-${c.status}">${c.status}</span></div>`).join('');
}
async function newCheck() {
  const mc = document.getElementById('modal-content');
  mc.innerHTML = `
    <h3>Новая проверка</h3>
    <form onsubmit="createCheck(event)">
      <label>Название проверки</label>
      <input type="text" id="check-name" required placeholder="Например: Проверка комплекта КР">
      <label>Режим работы ИИ</label>
      <select id="check-mode">
        <option value="local">Локальный (Ollama)</option>
        <option value="cloud">Облачный (API)</option>
        <option value="hybrid">Гибридный</option>
      </select>
      <label>Система</label>
      <select id="check-system">
        <option value="mixed">Все системы</option>
        <option value="power">Электроснабжение</option>
        <option value="fire">Противопожарные системы</option>
        <option value="comms">Сети связи (СКС/ЛВС/ВОЛС)</option>
        <option value="security">Охранные системы (СОТ/СКУД/ОС)</option>
        <option value="acs">АСУ/АСУТП</option>
      </select>
      <button type="submit" class="btn-primary" style="margin-top:12px">Создать и загрузить файлы</button>
    </form>`;
  document.getElementById('modal-overlay').style.display = 'flex';
}
async function createCheck(e) {
  e.preventDefault();
  const name = document.getElementById('check-name').value.trim();
  const mode = document.getElementById('check-mode').value;
  const system = document.getElementById('check-system').value;
  if (!name) return;
  closeModal();
  const j = await api('POST', '/api/checks',
    `name=${encodeURIComponent(name)}&mode=${encodeURIComponent(mode)}&system_kind=${encodeURIComponent(system)}`);
  currentCheckId = j.check_id;
  document.getElementById('dialog-title').textContent = 'Проверка #' + currentCheckId;
  document.getElementById('dialog-mode').textContent = mode;
  window.history.replaceState(null, '', `/check/${currentCheckId}`);
  loadChecks();
  setupUpload();
}
async function openCheck(id) {
  window.location = '/check/' + id;
}
/* --- file upload --- */
let uploadReady = false;

function setupUpload() {
  uploadReady = true;
  const zone = document.getElementById('upload-zone');
  const input = document.getElementById('file-input');
  zone.style.display = 'block';
  zone.textContent = 'Нажмите или перетащите файлы для загрузки';

  async function ensureCheck() {
    if (currentCheckId) return currentCheckId;
    const now = new Date().toLocaleString('ru-RU');
    const j = await api('POST', '/api/checks',
      `name=${encodeURIComponent('Проверка от ' + now)}&mode=local&system_kind=mixed`);
    currentCheckId = j.check_id;
    document.getElementById('dialog-title').textContent = 'Проверка #' + currentCheckId;
    document.getElementById('dialog-mode').textContent = 'local';
    window.history.replaceState(null, '', `/check/${currentCheckId}`);
    loadChecks();
    return currentCheckId;
  }

  zone.onclick = async () => {
    await ensureCheck();
    input.click();
  };

  zone.ondragover = e => { e.preventDefault(); zone.classList.add('dragover'); };
  zone.ondragleave = () => zone.classList.remove('dragover');
  zone.ondrop = async (e) => {
    e.preventDefault(); zone.classList.remove('dragover');
    await ensureCheck();
    handleFiles(e.dataTransfer.files);
  };

  input.onchange = () => { handleFiles(input.files); input.value = ''; };
}
async function handleFiles(files) {
  for (const f of files) {
    const fd = new FormData(); fd.append('file', f);
    try {
      const r = await fetch(`/api/checks/${currentCheckId}/upload`, { method: 'POST', body: fd });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || j.error || 'Ошибка загрузки');
      uploadedFiles.push(f.name);
    } catch (e) {
      alert('Ошибка загрузки ' + f.name + ': ' + e.message);
    }
  }
  updateUploadList();
  document.getElementById('btn-run').disabled = false;
}
function updateUploadList() {
  let el = document.getElementById('upload-list');
  if (!el) {
    el = document.createElement('div'); el.id = 'upload-list'; el.className = 'upload-list';
    document.getElementById('upload-zone').after(el);
  }
  el.innerHTML = uploadedFiles.map(n => `<span class="upload-item">${n}</span>`).join('');
}
/* --- run check --- */
async function runCheck() {
  if (!currentCheckId) return;
  document.getElementById('btn-run').disabled = true;
  document.getElementById('step-list').style.display = 'block';
  document.getElementById('step-list').innerHTML = '<div class="step-row">⏳ Запуск проверки…</div>';
  document.getElementById('findings-list').style.display = 'none';
  const j = await fetch(`/api/checks/${currentCheckId}/run`, { method: 'POST' }).then(r => r.json());
  loadResults(currentCheckId);
}
async function loadResults(checkId) {
  const r = await fetch(`/api/checks/${checkId}`);
  const j = await r.json();
  const stepList = document.getElementById('step-list');
  if (j.steps && j.steps.length) {
    stepList.style.display = 'block';
    stepList.innerHTML = j.steps.map(s => `<div class="step-row"><span class="step-name">${s.title}</span><span class="badge badge-${s.status}">${s.status}</span>${s.reason ? '<br><span style="color:var(--fg2)">'+s.reason+'</span>' : ''}</div>`).join('');
  }
  const fList = document.getElementById('findings-list');
  if (j.findings && j.findings.length) {
    fList.style.display = 'block';
    fList.innerHTML = '<h3>Замечания</h3>' +
      j.findings.map(f => `<div class="finding ${f.severity}"><div class="finding-title">${f.title}</div>${f.ntd_document ? '<div class="finding-ntd">'+f.ntd_document+(f.ntd_clause?' п. '+f.ntd_clause:'')+'</div>' : ''}${f.description ? '<div style="margin-top:4px;font-size:.9rem">'+f.description+'</div>' : ''}</div>`).join('');
    document.getElementById('btn-docx').disabled = false;
    document.getElementById('btn-xlsx').disabled = false;
  }
  loadChecks();
}
/* --- reports --- */
function downloadReport(format) {
  if (!currentCheckId) return;
  window.open(`/api/checks/${currentCheckId}/report/${format}`, '_blank');
}
/* --- NTD --- */
async function refreshNtd() {
  await api('POST', '/api/ntd/refresh');
  loadNtdStatus();
}
async function loadNtdStatus() {
  const r = await fetch('/api/ntd');
  if (!r.ok) return;
  const j = await r.json();
  const el = document.getElementById('ntd-status');
  el.innerHTML = j.documents.map(d => `<div style="font-size:.8rem;margin:2px 0">${d.code} <span class="badge badge-${d.status === 'active' ? 'ok' : 'err'}">${d.status}</span></div>`).join('');
}
/* --- admin: users --- */
async function loadUsers() {
  const r = await fetch('/api/admin/users'); if (!r.ok) return;
  const j = await r.json(); const tbody = document.querySelector('#users-table tbody');
  tbody.innerHTML = j.users.map(u => `<tr><td>${u.id}</td><td>${u.login}</td><td>${u.role}</td><td>${u.is_active ? 'активен' : 'заблокирован'}</td><td>
    <button onclick="resetPw(${u.id})" class="btn-sm">Сменить пароль</button>
    ${u.role !== 'admin' ? `<button onclick="toggleBlock(${u.id})" class="btn-sm">${u.is_active ? 'Блокировать' : 'Разблокировать'}</button><button onclick="delUser(${u.id})" class="btn-sm" style="background:var(--danger)">Удалить</button>` : ''}
  </td></tr>`).join('');
}
async function addUser(e) { e.preventDefault();
  await api('POST', '/api/admin/users', `login=${encodeURIComponent(document.getElementById('new-login').value)}&password=${encodeURIComponent(document.getElementById('new-password').value)}&full_name=${encodeURIComponent(document.getElementById('new-name').value)}&role=${document.getElementById('new-role').value}`);
  loadUsers(); e.target.reset();
}
async function resetPw(id) {
  const pw = prompt('Новый пароль:'); if (!pw) return;
  await fetch(`/api/admin/users/${id}/reset-password`, { method: 'POST', headers: {'Content-Type':'application/x-www-form-urlencoded'}, body: `password=${encodeURIComponent(pw)}` });
  alert('Пароль изменён');
}
async function toggleBlock(id) { await fetch(`/api/admin/users/${id}/toggle-block`, { method: 'POST' }); loadUsers(); }
async function delUser(id) { if (!confirm('Удалить пользователя?')) return; await fetch(`/api/admin/users/${id}`, { method: 'DELETE' }); loadUsers(); }
/* --- admin: AI models --- */
async function loadAiModels() {
  const el = document.getElementById('model-list') || document.getElementById('ai-model-list');
  if (!el) return;
  el.textContent = 'Загрузка...';
  try {
    const r = await fetch('/api/ai-models');
    if (!r.ok) { el.textContent = 'Ошибка загрузки'; return; }
    const j = await r.json();
    el.innerHTML = j.models.map(m =>
      `<div style="font-size:.8rem;margin:2px 0;display:flex;justify-content:space-between;align-items:center">
        <span>${m.name} (${m.kind}) ${m.is_enabled ? '✅' : '🚫'}</span>
        <button onclick="delModelById(${m.id})" class="btn-sm" style="color:var(--danger);padding:2px 6px;font-size:.8rem">✕</button>
      </div>`
    ).join('');
    if (!j.models.length) el.textContent = 'Модели не добавлены. Нажмите «+ Добавить модель»';
  } catch (e) {
    el.textContent = 'Ошибка: ' + e.message;
  }
}
async function delModelById(id) {
  if (!confirm('Удалить модель?')) return;
  await fetch('/api/ai-models/' + id, { method: 'DELETE' });
  loadAiModels();
}
function showAddModel() {
  const mc = document.getElementById('modal-content');
  mc.innerHTML = `<h3>Добавить модель ИИ</h3>
    <form onsubmit="addModel(event)">
      <label>Название</label><input type="text" id="m-name" required>
      <label>Тип</label><select id="m-kind"><option value="local">Локальная (Ollama)</option><option value="cloud" selected>Облачная (API)</option></select>
      <label>Провайдер</label><input type="text" id="m-provider" value="openai-compatible">
      <label>Base URL</label><input type="text" id="m-url">
      <label>Model ID</label><input type="text" id="m-model">
      <label>API Key</label><input type="password" id="m-key">
      <button type="submit" class="btn-primary">Сохранить</button>
    </form>`;
  document.getElementById('modal-overlay').style.display = 'flex';
}
async function addModel(e) {
  e.preventDefault();
  const btn = e.target.querySelector('button[type="submit"]');
  btn.disabled = true; btn.textContent = 'Сохранение…';
  try {
    const j = await apiJson('/api/ai-models', {
      name: document.getElementById('m-name').value,
      kind: document.getElementById('m-kind').value,
      provider: document.getElementById('m-provider').value,
      base_url: document.getElementById('m-url').value,
      model_id: document.getElementById('m-model').value,
      api_key: document.getElementById('m-key').value,
      is_enabled: 1,
    });
    if (!j.ok) throw new Error(j.error || 'Ошибка');
    closeModal(); loadAiModels();
  } catch (err) {
    alert('Ошибка: ' + err.message);
    btn.disabled = false; btn.textContent = 'Сохранить';
  }
}
async function delModel(id) { await fetch('/api/ai-models/' + id, { method: 'DELETE' }); loadAiModels(); }
function closeModal() { document.getElementById('modal-overlay').style.display = 'none'; }
/* --- page load --- */
setupUpload();
if (document.getElementById('check-list')) loadChecks();
if (document.getElementById('ntd-status')) loadNtdStatus();
if (document.getElementById('model-list') || document.getElementById('ai-model-list')) loadAiModels();
if (document.getElementById('users-table')) loadUsers();