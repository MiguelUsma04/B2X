/* B2X frontend. Vanilla JS, sin build step. */
'use strict';

let ALL_IDS = [];              // ids que matchean el filtro actual
const SELECTED = new Set();    // ids seleccionados
let POLL = null;

const $ = (id) => document.getElementById(id);
const esc = (s) => (s == null ? '' : String(s).replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])));

/* ---------------- tabs ---------------- */
document.querySelectorAll('.tab').forEach((t) => {
  t.onclick = () => {
    document.querySelectorAll('.tab').forEach((x) => x.classList.remove('active'));
    document.querySelectorAll('.panel').forEach((x) => x.classList.remove('active'));
    t.classList.add('active');
    $('panel-' + t.dataset.panel).classList.add('active');
    if (t.dataset.panel === 'enrich') refreshPending();
  };
});

/* ---------------- métricas ---------------- */
async function loadMetrics() {
  const m = await (await fetch('/api/metrics')).json();

  $('provstat').innerHTML = m.providers.map((p) =>
    `<span><i class="dot ${p.enabled ? 'on' : 'off'}"></i>${esc(p.name)}</span>`).join('') +
    `<span><i class="dot ${m.ghl_configured ? 'on' : 'off'}"></i>ghl</span>`;

  const src = m.by_source || {};
  const provCell = ['apollo', 'prospeo', 'icypeas', 'hunter'].map((k) =>
    `<div style="display:flex;justify-content:space-between;gap:10px">
       <span style="color:var(--ink-3)">${k}</span><span>${src[k] || 0}</span></div>`).join('');

  $('metrics').innerHTML = `
    <div class="metric"><div class="k">Total contactos</div><div class="v">${m.total}</div>
      <div class="n">${m.batches.length} lote(s)</div></div>
    <div class="metric green"><div class="k">Con email</div><div class="v">${m.pct_with_email}%</div>
      <div class="n">${m.with_email} de ${m.total}</div></div>
    <div class="metric amber"><div class="k">Pendientes</div>
      <div class="v">${m.by_status.pending || 0}</div><div class="n">sin enriquecer</div></div>
    <div class="metric red"><div class="k">Not found</div>
      <div class="v">${m.by_status.not_found || 0}</div><div class="n">agotó la cascada</div></div>
    <div class="metric blue"><div class="k">Enviados a GHL</div>
      <div class="v">${m.by_ghl.sent || 0}</div>
      <div class="n">${m.by_ghl.error || 0} con error</div></div>
    <div class="metric"><div class="k">Resueltos por fuente</div>
      <div class="mono" style="font-size:11px;line-height:1.75;margin-top:5px">${provCell}</div></div>`;

  const opts = '<option value="">todos</option>' + m.batches.map((b) =>
    `<option value="${b.id}">#${b.id} · ${esc(b.filename)}</option>`).join('');
  for (const id of ['f-batch', 'e-batch']) {
    const el = $(id), cur = el.value;
    el.innerHTML = opts;
    el.value = cur;
  }

  $('btbody').innerHTML = m.batches.length ? m.batches.map((b) => `<tr>
      <td class="mono">#${b.id}</td><td>${esc(b.filename)}</td>
      <td class="mono">${esc(b.imported_at)}</td><td class="mono">${b.total_rows}</td>
      <td class="mono" style="color:var(--green)">${b.new_contacts}</td>
      <td class="mono" style="color:var(--ink-3)">${b.duplicate_contacts}</td>
      <td>${esc(b.icp_tag || '—')}</td></tr>`).join('')
    : '<tr><td colspan="7" class="empty">Sin lotes todavía.</td></tr>';

  const missing = m.providers.filter((p) => !p.enabled).map((p) => p.name);
  $('enrich-warn').innerHTML = missing.length
    ? `<div class="alert warn">Sin API key: <b>${missing.join(', ')}</b>. Cargalas en el
       archivo <b>.env</b> y reiniciá la app. La cascada usa solo los proveedores activos.</div>`
    : '';
  return m;
}

/* ---------------- contactos ---------------- */
function filterParams() {
  const p = new URLSearchParams();
  if ($('f-q').value.trim()) p.set('q', $('f-q').value.trim());
  if ($('f-status').value) p.set('email_status', $('f-status').value);
  if ($('f-source').value) p.set('email_source', $('f-source').value);
  if ($('f-ghl').value) p.set('ghl_status', $('f-ghl').value);
  if ($('f-batch').value) p.set('import_batch_id', $('f-batch').value);
  return p;
}

async function loadContacts() {
  const d = await (await fetch('/api/contacts?' + filterParams())).json();
  ALL_IDS = d.all_ids;
  $('ccount').textContent = `— ${d.contacts.length} de ${d.total}`;

  $('ctbody').innerHTML = d.contacts.map((c) => {
    const pill = (v, cls) => v ? `<span class="pill ${cls || v}">${esc(v)}</span>`
      : '<span class="muted">—</span>';
    return `<tr id="row-${c.id}" class="${SELECTED.has(c.id) ? 'sel' : ''}">
      <td><input type="checkbox" ${SELECTED.has(c.id) ? 'checked' : ''}
          onclick="toggleOne(${c.id},this.checked)"></td>
      <td><span class="clickable" onclick="showDetail(${c.id})">${esc(c.full_name)}</span></td>
      <td>${esc(c.company_name || '—')}<br><span class="mono muted">${esc(c.company_domain || '')}</span></td>
      <td>${esc(c.job_title || '—')}</td>
      <td class="mono">${c.email ? esc(c.email) : '<span class="muted">—</span>'}</td>
      <td>${pill(c.email_status)}</td>
      <td>${pill(c.email_source)}</td>
      <td>${pill(c.ghl_status)}${c.ghl_error_message
        ? `<br><span class="mono muted" title="${esc(c.ghl_error_message)}">${esc(c.ghl_error_message.slice(0, 34))}…</span>` : ''}</td>
      <td class="mono muted">${c.import_batch_id ? '#' + c.import_batch_id : '—'}</td>
      <td><button class="sm" onclick="showDetail(${c.id})">Ver</button></td></tr>`;
  }).join('');

  $('cempty').innerHTML = d.contacts.length ? ''
    : '<div class="empty">Sin contactos. Importá un CSV para empezar.</div>';
  updateSelInfo();
}

function resetFilters() {
  ['f-q', 'f-status', 'f-source', 'f-ghl', 'f-batch'].forEach((i) => { $(i).value = ''; });
  loadContacts();
}

/* ---------------- selección ---------------- */
function toggleOne(id, on) {
  on ? SELECTED.add(id) : SELECTED.delete(id);
  $('row-' + id)?.classList.toggle('sel', on);
  updateSelInfo();
}
function togglePage(box) {
  document.querySelectorAll('#ctbody input[type=checkbox]').forEach((cb) => {
    const id = +cb.getAttribute('onclick').match(/\d+/)[0];
    cb.checked = box.checked;
    box.checked ? SELECTED.add(id) : SELECTED.delete(id);
    $('row-' + id)?.classList.toggle('sel', box.checked);
  });
  updateSelInfo();
}
function selectAllFiltered() {
  ALL_IDS.forEach((id) => SELECTED.add(id));
  document.querySelectorAll('#ctbody input[type=checkbox]').forEach((cb) => { cb.checked = true; });
  document.querySelectorAll('#ctbody tr').forEach((tr) => tr.classList.add('sel'));
  updateSelInfo();
}
function clearSelection() {
  SELECTED.clear();
  document.querySelectorAll('#ctbody input[type=checkbox]').forEach((cb) => { cb.checked = false; });
  document.querySelectorAll('#ctbody tr').forEach((tr) => tr.classList.remove('sel'));
  $('chk-all').checked = false;
  updateSelInfo();
}
function updateSelInfo() {
  $('selinfo').textContent = `${SELECTED.size} seleccionado(s)`;
  $('btn-ghl').disabled = SELECTED.size === 0;
}

/* ---------------- importación ---------------- */
async function doPreview() {
  const f = $('csvfile').files[0];
  if (!f) return;
  $('prev-alert').innerHTML = '<div class="alert info">Leyendo archivo…</div>';
  $('prev-box').innerHTML = '';

  const fd = new FormData();
  fd.append('file', f);
  const r = await fetch('/api/import/preview', { method: 'POST', body: fd });
  const d = await r.json();
  if (!r.ok) {
    $('prev-alert').innerHTML = `<div class="alert err">${esc(d.detail || 'Error al leer el CSV.')}</div>`;
    return;
  }
  $('prev-alert').innerHTML = '';

  if (d.export_kind === 'company') {
    $('prev-box').innerHTML = `<div class="card"><h2>No es un export de personas</h2><div class="body">
      <div class="alert err">Este CSV parece un export de <b>Cuentas (empresas)</b> de Apollo.
      No tiene columnas de persona, así que no hay a quién buscarle el email.<br><br>
      En Apollo, andá a la pestaña <b>People</b>, aplicá tus filtros y exportá desde ahí.</div>
      <p class="hint">Columnas detectadas: ${esc(d.headers.slice(0, 14).join(' · '))}…</p>
      </div></div>`;
    return;
  }

  const cols = Object.entries(d.mapping).map(([k, v]) =>
    `<tr><td class="mono">${esc(k)}</td><td class="mono">${v
      ? `<span style="color:var(--green)">${esc(v)}</span>`
      : '<span style="color:var(--ink-3)">— sin mapear —</span>'}</td></tr>`).join('');

  const rows = d.preview.map((c) => `<tr>
    <td>${esc(c.full_name || '—')}</td><td>${esc(c.company_name || '—')}</td>
    <td>${esc(c.job_title || '—')}</td>
    <td class="mono">${esc(c.email || '—')}</td>
    <td class="mono">${esc(c.company_domain || '—')}</td>
    <td><span class="pill ${c.email_status}">${c.email_status}</span></td></tr>`).join('');

  const warn = d.unmapped_fields.filter((f) => ['first_name', 'company_domain'].includes(f));
  $('prev-box').innerHTML = `
    <div class="card"><h2>Paso 2 — Mapeo detectado</h2><div class="body">
      ${warn.length ? `<div class="alert warn">Ojo: no se detectó <b>${warn.join(', ')}</b>.
        El enriquecimiento necesita nombre + dominio para funcionar bien.</div>` : ''}
      <table><thead><tr><th>Campo B2X</th><th>Columna del CSV</th></tr></thead>
      <tbody>${cols}</tbody></table></div></div>

    <div class="card"><h2>Paso 3 — Vista previa (${d.preview.length} de ${d.total_rows} filas)</h2>
      <div class="tbl-scroll"><table>
      <thead><tr><th>Nombre</th><th>Empresa</th><th>Cargo</th><th>Email</th>
        <th>Dominio</th><th>Estado</th></tr></thead>
      <tbody>${rows}</tbody></table></div></div>

    <div class="card"><h2>Paso 4 — Confirmar importación</h2><div class="body">
      <div class="row">
        <div><label class="fld">Tag de ICP (opcional)</label>
          <input id="icp-tag" placeholder="ej: proptech-latam" style="width:230px"></div>
        <button class="primary" onclick="doConfirm()">Importar ${d.total_rows} filas</button>
      </div>
      <p class="hint" style="margin-top:9px">Los duplicados (por email, o por nombre+dominio)
        se descartan automáticamente.</p>
      <div id="confirm-result"></div>
    </div></div>`;
}

async function doConfirm() {
  const fd = new FormData();
  fd.append('icp_tag', $('icp-tag').value.trim());
  const r = await fetch('/api/import/confirm', { method: 'POST', body: fd });
  const d = await r.json();
  if (!r.ok) {
    $('confirm-result').innerHTML = `<div class="alert err">${esc(d.detail || 'Error.')}</div>`;
    return;
  }
  $('confirm-result').innerHTML = `<div class="alert ok">
    Lote <b>#${d.batch_id}</b> importado — ${d.new_contacts} nuevos ·
    ${d.duplicate_contacts} duplicados descartados ·
    ${d.skipped_no_name} sin nombre omitidos (de ${d.total_rows} filas).</div>`;
  $('csvfile').value = '';
  await loadMetrics();
  await loadContacts();
  await refreshPending();
}

/* ---------------- enriquecimiento ---------------- */
async function refreshPending() {
  const d = await (await fetch('/api/enrich/pending-count')).json();
  $('pendinfo').textContent = `${d.pending} pendiente(s)`;
  $('btn-enrich').disabled = d.pending === 0;
}

async function startEnrich() {
  const fd = new FormData();
  fd.append('limit', $('e-limit').value || '');
  fd.append('batch_id', $('e-batch').value || '');
  const r = await fetch('/api/enrich/start', { method: 'POST', body: fd });
  const d = await r.json();
  if (!r.ok) {
    $('progress-box').innerHTML = `<div class="alert err">${esc(d.detail || 'Error.')}</div>`;
    return;
  }
  if (!d.started) {
    $('progress-box').innerHTML = `<div class="alert info">${esc(d.message)}</div>`;
    return;
  }
  $('btn-enrich').disabled = true;
  if (POLL) clearInterval(POLL);
  POLL = setInterval(pollProgress, 900);
  pollProgress();
}

async function pollProgress() {
  const p = await (await fetch('/api/enrich/progress')).json();
  const pct = p.total ? Math.round((p.processed / p.total) * 100) : 0;
  const by = Object.entries(p.by_provider || {}).map(([k, v]) =>
    `<span class="pill ${k}">${k}: ${v}</span>`).join(' ') || '<span class="muted">—</span>';

  $('progress-box').innerHTML = `
    <div class="card" style="margin:0"><h2>${p.running
      ? '<span class="blink">▮</span> Procesando'
      : (p.finished ? 'Corrida terminada' : 'En espera')}</h2>
      <div class="body">
        ${p.error ? `<div class="alert err">${esc(p.error)}</div>` : ''}
        <div class="mono">${p.processed} / ${p.total} — ${pct}%</div>
        <div class="bar"><i style="width:${pct}%"></i></div>
        ${p.running && p.current_contact ? `<div class="mono" style="color:var(--ink-2)">
          ▸ ${esc(p.current_contact)} <span style="color:var(--amber)">
          [${esc(p.current_provider || '…')}]</span></div>` : ''}
        <div class="row" style="margin-top:11px">
          <span class="mono" style="color:var(--green)">encontrados: ${p.found}</span>
          <span class="mono" style="color:var(--red)">no encontrados: ${p.not_found}</span>
        </div>
        <div style="margin-top:9px">${by}</div>
      </div></div>`;

  if (!p.running && p.finished) {
    clearInterval(POLL); POLL = null;
    await loadMetrics(); await loadContacts(); await refreshPending();
  }
}

/* ---------------- detalle ---------------- */
async function showDetail(id) {
  const d = await (await fetch('/api/contacts/' + id)).json();
  const c = d.contact;
  $('modal-title').textContent = c.full_name || ('Contacto #' + id);

  const logs = d.logs.length ? d.logs.map((l, i) => `
    <div class="log"><div class="log-h">
      <span style="color:var(--ink-3)">#${i + 1}</span>
      <span class="pill ${esc(l.provider)}">${esc(l.provider)}</span>
      <span class="pill ${l.success ? 'verified' : 'not_found'}">${l.success ? 'éxito' : 'falló'}</span>
      <span style="color:var(--ink-3)">${esc(l.timestamp)}</span></div>
      <div class="log-b">
        ${l.error_message ? `<div class="alert err" style="margin-bottom:9px">${esc(l.error_message)}</div>` : ''}
        <div class="hint" style="margin-bottom:3px">REQUEST</div>
        <pre>${esc(JSON.stringify(l.request_payload, null, 2))}</pre>
        <div class="hint" style="margin:8px 0 3px">RESPONSE</div>
        <pre>${esc(JSON.stringify(l.response_payload, null, 2))}</pre>
      </div></div>`).join('')
    : '<div class="empty">Sin intentos de enriquecimiento todavía.</div>';

  $('modal-body').innerHTML = `
    <dl class="kv">
      <dt>Email</dt><dd>${esc(c.email || '—')}</dd>
      <dt>Estado</dt><dd><span class="pill ${c.email_status}">${c.email_status}</span></dd>
      <dt>Fuente</dt><dd>${c.email_source ? `<span class="pill ${c.email_source}">${c.email_source}</span>` : '—'}</dd>
      <dt>Empresa</dt><dd>${esc(c.company_name || '—')}</dd>
      <dt>Dominio</dt><dd>${esc(c.company_domain || '—')}</dd>
      <dt>Cargo</dt><dd>${esc(c.job_title || '—')}</dd>
      <dt>LinkedIn</dt><dd>${c.linkedin_url
        ? `<a href="${esc(c.linkedin_url)}" target="_blank" rel="noopener"
             style="color:var(--blue)">${esc(c.linkedin_url)}</a>` : '—'}</dd>
      <dt>Teléfono</dt><dd>${esc(c.phone || '—')}</dd>
      <dt>GHL</dt><dd><span class="pill ${c.ghl_status}">${c.ghl_status}</span>
        ${c.ghl_contact_id ? ' · ' + esc(c.ghl_contact_id) : ''}</dd>
      ${c.ghl_error_message ? `<dt>Error GHL</dt><dd style="color:var(--red)">${esc(c.ghl_error_message)}</dd>` : ''}
      <dt>Lote</dt><dd>${c.import_batch_id ? '#' + c.import_batch_id : '—'}</dd>
    </dl>
    <h2 style="font-family:var(--mono);font-size:11.5px;letter-spacing:.11em;
      text-transform:uppercase;color:var(--ink-2);margin-bottom:10px">
      Historial de enriquecimiento (${d.logs.length})</h2>
    ${logs}`;
  $('modal').classList.add('open');
}
function closeModal() { $('modal').classList.remove('open'); }
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

/* ---------------- GHL ---------------- */
async function sendToGHL() {
  const ids = [...SELECTED];
  if (!ids.length) return;
  if (!confirm(`Enviar ${ids.length} contacto(s) a GoHighLevel?`)) return;

  $('btn-ghl').disabled = true;
  $('ghl-result').innerHTML = '<div class="alert info">Enviando a GHL…</div>';

  const fd = new FormData();
  fd.append('contact_ids', JSON.stringify(ids));
  fd.append('tag', $('ghl-tag').value.trim());

  const r = await fetch('/api/ghl/send', { method: 'POST', body: fd });
  const d = await r.json();

  if (d.error) {
    $('ghl-result').innerHTML = `<div class="alert err">${esc(d.error)}</div>`;
  } else {
    const errs = (d.results || []).filter((x) => x.status === 'error').slice(0, 6);
    $('ghl-result').innerHTML = `
      <div class="alert ${d.failed ? 'warn' : 'ok'}">
        Enviados: <b>${d.sent}</b> · Errores: <b>${d.failed}</b> · Omitidos (sin email):
        <b>${d.skipped || 0}</b>
        ${errs.length ? '<br><br>' + errs.map((e) =>
          `#${e.id}: ${esc(e.message)}`).join('<br>') : ''}
      </div>`;
  }
  $('btn-ghl').disabled = false;
  clearSelection();
  await loadMetrics();
  await loadContacts();
}

/* ---------------- init ---------------- */
$('f-q').addEventListener('keydown', (e) => { if (e.key === 'Enter') loadContacts(); });
(async () => { await loadMetrics(); await loadContacts(); await refreshPending(); })();
