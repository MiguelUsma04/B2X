/* B2X frontend. Vanilla JS, sin build step.
   Los textos hablan en términos del negocio, no de la implementación. */
'use strict';

let ALL_IDS = [];
const SELECTED = new Set();
let POLL = null;

const $ = (id) => document.getElementById(id);
const esc = (s) => (s == null ? '' : String(s).replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])));

// Cómo se nombra cada estado de cara al usuario.
const STATUS_TXT = {
  verified:   'Email verificado',
  unverified: 'Email sin verificar',
  pending:    'Falta buscar',
  not_found:  'No se encontró',
};
const GHL_TXT = { pending: 'Sin enviar', sent: 'En el CRM', error: 'Falló' };
const SOURCE_TXT = {
  apollo: 'Venía en el archivo', prospeo: 'Prospeo',
  icypeas: 'Icypeas', hunter: 'Hunter',
};

const pill = (val, dict) => val
  ? `<span class="pill ${esc(val)}">${esc((dict && dict[val]) || val)}</span>`
  : '<span class="sub">—</span>';

/* ---------------- navegación por pasos ---------------- */
document.querySelectorAll('.step').forEach((t) => {
  t.onclick = () => {
    document.querySelectorAll('.step').forEach((x) => {
      x.classList.remove('active'); x.removeAttribute('aria-current');
    });
    document.querySelectorAll('.panel').forEach((x) => x.classList.remove('active'));
    t.classList.add('active');
    t.setAttribute('aria-current', 'page');
    $('panel-' + t.dataset.panel).classList.add('active');
    if (t.dataset.panel === 'enrich') refreshPending();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };
});

function goToStep(name) {
  const btn = document.querySelector(`.step[data-panel="${name}"]`);
  if (btn) btn.click();
}

/* ---------------- métricas ---------------- */
async function loadMetrics() {
  const m = await (await fetch('/api/metrics')).json();

  $('provstat').innerHTML = m.providers.map((p) =>
    `<span class="chip"><i class="dot ${p.enabled ? 'on' : 'off'}"></i>${esc(p.name)}</span>`)
    .join('') +
    `<span class="chip"><i class="dot ${m.ghl_configured ? 'on' : 'off'}"></i>CRM</span>`;

  const src = m.by_source || {};
  const srcRows = [['apollo', 'Venía en el archivo'], ['prospeo', 'Prospeo'],
                   ['icypeas', 'Icypeas'], ['hunter', 'Hunter']]
    .map(([k, label]) =>
      `<div class="srcline"><span>${label}</span><span>${src[k] || 0}</span></div>`).join('');

  const pending = m.by_status.pending || 0;
  const sent = m.by_ghl.sent || 0;

  $('metrics').innerHTML = `
    <div class="metric hero">
      <div class="k">Contactos con email</div>
      <div class="v">${m.with_email}</div>
      <div class="n">${m.pct_with_email}% de ${m.total} · listos para contactar</div>
    </div>
    <div class="metric">
      <div class="k">Total cargados</div>
      <div class="v">${m.total}</div>
      <div class="n">${m.batches.length} carga(s) de Apollo</div>
    </div>
    <div class="metric">
      <div class="k">Falta buscarles el email</div>
      <div class="v">${pending}</div>
      <div class="n">${pending ? 'Andá al paso 2' : 'Ninguno pendiente'}</div>
    </div>
    <div class="metric">
      <div class="k">Ya están en el CRM</div>
      <div class="v">${sent}</div>
      <div class="n">${(m.by_ghl.error || 0)
        ? (m.by_ghl.error + ' fallaron al enviar') : 'Sin errores'}</div>
    </div>
    <div class="metric">
      <div class="k">Quién encontró cada email</div>
      <div style="margin-top:8px">${srcRows}</div>
    </div>`;

  const opts = '<option value="">Todas</option>' + m.batches.map((b) =>
    `<option value="${b.id}">#${b.id} · ${esc(b.filename)}</option>`).join('');
  for (const id of ['f-batch', 'e-batch']) {
    const el = $(id), cur = el.value;
    el.innerHTML = opts; el.value = cur;
  }

  $('btbody').innerHTML = m.batches.length ? m.batches.map((b) => `<tr>
      <td>#${b.id}</td><td>${esc(b.filename)}</td>
      <td class="sub">${esc(b.imported_at)}</td><td>${b.total_rows}</td>
      <td style="color:var(--ok);font-weight:600">${b.new_contacts}</td>
      <td class="sub">${b.duplicate_contacts}</td>
      <td>${esc(b.icp_tag || '—')}</td></tr>`).join('')
    : `<tr><td colspan="7"><div class="empty"><strong>Todavía no cargaste nada</strong>
       Cuando subas tu primer archivo de Apollo va a aparecer acá.</div></td></tr>`;

  const missing = m.providers.filter((p) => !p.enabled).map((p) => p.name);
  $('enrich-warn').innerHTML = missing.length
    ? `<div class="alert warn">No están configurados: <b>${missing.join(', ')}</b>.
       La búsqueda va a usar solo los servicios conectados.</div>`
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
  $('ccount').textContent = d.total
    ? `— mostrando ${d.contacts.length} de ${d.total}` : '';

  $('ctbody').innerHTML = d.contacts.map((c) => `
    <tr id="row-${c.id}" class="${SELECTED.has(c.id) ? 'sel' : ''}">
      <td><input type="checkbox" ${SELECTED.has(c.id) ? 'checked' : ''}
          onclick="toggleOne(${c.id},this.checked)"
          aria-label="Marcar ${esc(c.full_name)}"></td>
      <td><button class="linkish" onclick="showDetail(${c.id})">${esc(c.full_name)}</button>
        ${c.job_title ? `<div class="sub">${esc(c.job_title)}</div>` : ''}</td>
      <td>${esc(c.company_name || '—')}
        ${c.company_domain ? `<div class="sub">${esc(c.company_domain)}</div>` : ''}</td>
      <td>${c.email ? esc(c.email) : '<span class="sub">—</span>'}</td>
      <td>${pill(c.email_status, STATUS_TXT)}</td>
      <td>${pill(c.email_source, SOURCE_TXT)}</td>
      <td>${pill(c.ghl_status, GHL_TXT)}
        ${c.ghl_error_message
          ? `<div class="sub" title="${esc(c.ghl_error_message)}">${esc(c.ghl_error_message.slice(0, 30))}…</div>`
          : ''}</td>
      <td><button class="sm" onclick="showDetail(${c.id})">Ver</button></td></tr>`).join('');

  $('cempty').innerHTML = d.contacts.length ? '' : (d.total === 0 && !filterParams().toString()
    ? `<div class="empty"><strong>Todavía no hay contactos</strong>
       Empezá subiendo tu archivo de Apollo.<br><br>
       <button class="primary" onclick="goToStep('import')">Cargar contactos</button></div>`
    : `<div class="empty"><strong>Ningún contacto coincide</strong>
       Probá cambiando los filtros de arriba.</div>`);
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
  const n = SELECTED.size;
  $('selinfo').textContent = n === 0 ? 'Ninguno marcado'
    : (n === 1 ? '1 contacto marcado' : `${n} contactos marcados`);
  $('btn-ghl').disabled = n === 0;
}

/* ---------------- importar ---------------- */
async function doPreview() {
  const f = $('csvfile').files[0];
  if (!f) return;
  $('prev-alert').innerHTML = '<div class="alert info">Leyendo el archivo…</div>';
  $('prev-box').innerHTML = '';

  const fd = new FormData();
  fd.append('file', f);
  const r = await fetch('/api/import/preview', { method: 'POST', body: fd });
  const d = await r.json();
  if (!r.ok) {
    $('prev-alert').innerHTML =
      `<div class="alert err">${esc(d.detail || 'No se pudo leer el archivo.')}</div>`;
    return;
  }
  $('prev-alert').innerHTML = '';

  if (d.export_kind === 'company') {
    $('prev-box').innerHTML = `<div class="card"><h2>Este archivo no sirve</h2><div class="body">
      <div class="alert err">
        <b>Exportaste empresas, no personas.</b><br><br>
        Este archivo tiene datos de compañías, pero no trae nombres ni emails de
        contactos — así que no hay a quién buscarle el email.<br><br>
        En Apollo, entrá a la pestaña <b>People</b>, aplicá tus filtros y exportá desde ahí.
      </div>
      <p class="help">Columnas encontradas: ${esc(d.headers.slice(0, 12).join(' · '))}…</p>
      </div></div>`;
    return;
  }

  const rows = d.preview.map((c) => `<tr>
    <td>${esc(c.full_name || '—')}${c.job_title ? `<div class="sub">${esc(c.job_title)}</div>` : ''}</td>
    <td>${esc(c.company_name || '—')}${c.company_domain ? `<div class="sub">${esc(c.company_domain)}</div>` : ''}</td>
    <td>${esc(c.email || '—')}</td>
    <td>${pill(c.email_status, STATUS_TXT)}</td></tr>`).join('');

  const conEmail = d.preview.filter((c) => c.email).length;
  const warn = d.unmapped_fields.filter((x) => ['first_name', 'company_domain'].includes(x));

  $('prev-box').innerHTML = `
    <div class="card"><h2>Así se ve tu archivo</h2><div class="body">
      ${warn.length ? `<div class="alert warn">
        No se reconoció una columna importante. La búsqueda de emails necesita el
        nombre de la persona y el sitio web de la empresa para funcionar bien.</div>` : ''}
      <p class="lede">
        Encontramos <b>${d.total_rows} contactos</b> en el archivo.
        De los primeros ${d.preview.length} que ves acá, ${conEmail} ya traen email
        y el resto habría que buscarlos en el paso 2.
      </p>
      <div class="tbl-scroll"><table>
        <thead><tr><th>Persona</th><th>Empresa</th><th>Email</th><th>Estado</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
    </div></div>

    <div class="card"><h2>¿Todo bien? Guardalos</h2><div class="body">
      <div class="row">
        <div>
          <label class="fld" for="icp-tag">Nombre para esta carga (opcional)</label>
          <input id="icp-tag" placeholder="Ej: inmobiliarias Bogotá" style="width:270px">
        </div>
        <button class="primary" onclick="doConfirm()">Guardar ${d.total_rows} contactos</button>
      </div>
      <p class="help">
        Sirve para identificar el segmento después. Los contactos que ya tengas
        cargados no se duplican.
      </p>
      <div id="confirm-result" style="margin-top:14px"></div>
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
  const dupTxt = d.duplicate_contacts
    ? ` Se descartaron ${d.duplicate_contacts} que ya tenías.` : '';
  $('confirm-result').innerHTML = `<div class="alert ok">
    <b>Listo: ${d.new_contacts} contactos agregados.</b>${dupTxt}<br><br>
    <button class="primary" onclick="goToStep('enrich')">Ahora buscá los emails →</button>
    </div>`;
  $('csvfile').value = '';
  await loadMetrics(); await loadContacts(); await refreshPending();
}

/* ---------------- buscar emails ---------------- */
async function refreshPending() {
  const d = await (await fetch('/api/enrich/pending-count')).json();
  $('pendinfo').textContent = d.pending === 0
    ? 'No hay contactos pendientes'
    : (d.pending === 1 ? 'Falta 1 contacto' : `Faltan ${d.pending} contactos`);
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
  const by = Object.entries(p.by_provider || {})
    .map(([k, v]) => `<span class="pill ${k}">${esc(SOURCE_TXT[k] || k)}: ${v}</span>`)
    .join(' ');

  if (p.error) {
    $('progress-box').innerHTML = `<div class="alert err">${esc(p.error)}</div>`;
  } else if (p.running) {
    $('progress-box').innerHTML = `
      <div class="card" style="margin:0"><h2><span class="pulse">●</span> Buscando…</h2>
      <div class="body">
        <p class="lede">Podés dejar esta pantalla abierta. Va a tardar
          aproximadamente ${Math.max(1, Math.round((p.total - p.processed) * 1.5 / 60))} minuto(s).</p>
        <div style="font-size:19px;font-weight:600">${p.processed} de ${p.total} revisados</div>
        <div class="bar"><i style="width:${pct}%"></i></div>
        ${p.current_contact ? `<p style="color:var(--ink-2)">Consultando
          <b>${esc(SOURCE_TXT[p.current_provider] || p.current_provider || '…')}</b>
          para ${esc(p.current_contact)}</p>` : ''}
        <div class="row" style="margin-top:14px">
          <span style="color:var(--ok);font-weight:600">${p.found} emails encontrados</span>
          <span style="color:var(--ink-3)">${p.not_found} sin resultado</span>
        </div>
        ${by ? `<div style="margin-top:12px">${by}</div>` : ''}
      </div></div>`;
  } else if (p.finished) {
    $('progress-box').innerHTML = `
      <div class="alert ok">
        <b>Búsqueda terminada.</b> Se encontraron ${p.found} emails nuevos
        de ${p.total} contactos revisados.
        ${p.not_found ? ` En ${p.not_found} no hubo resultado en ningún servicio.` : ''}
        <br><br>${by ? by + '<br><br>' : ''}
        <button class="primary" onclick="goToStep('contacts')">Ver y enviar al CRM →</button>
      </div>`;
  }

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
      <b>Intento ${i + 1}</b>
      <span class="pill ${esc(l.provider)}">${esc(SOURCE_TXT[l.provider] || l.provider)}</span>
      <span class="pill ${l.success ? 'verified' : 'not_found'}">
        ${l.success ? 'Encontró el email' : 'Sin resultado'}</span>
      <span class="sub">${esc(l.timestamp)}</span></div>
      <div class="log-b">
        ${l.error_message ? `<div class="alert err" style="margin-bottom:10px">${esc(l.error_message)}</div>` : ''}
        <details><summary style="cursor:pointer;color:var(--ink-2);font-size:14px">
          Ver detalle técnico</summary>
          <div class="hint" style="margin-top:10px">LO QUE SE PREGUNTÓ</div>
          <pre>${esc(JSON.stringify(l.request_payload, null, 2))}</pre>
          <div class="hint" style="margin-top:10px">LO QUE RESPONDIÓ</div>
          <pre>${esc(JSON.stringify(l.response_payload, null, 2))}</pre>
        </details>
      </div></div>`).join('')
    : `<div class="empty">Todavía no se buscó el email de este contacto.</div>`;

  $('modal-body').innerHTML = `
    <dl class="kv">
      <dt>Email</dt><dd>${esc(c.email || 'Sin email todavía')}</dd>
      <dt>Estado</dt><dd>${pill(c.email_status, STATUS_TXT)}</dd>
      <dt>Encontrado por</dt><dd>${pill(c.email_source, SOURCE_TXT)}</dd>
      <dt>Cargo</dt><dd>${esc(c.job_title || '—')}</dd>
      <dt>Empresa</dt><dd>${esc(c.company_name || '—')}</dd>
      <dt>Sitio web</dt><dd>${esc(c.company_domain || '—')}</dd>
      <dt>LinkedIn</dt><dd>${c.linkedin_url
        ? `<a href="${esc(c.linkedin_url)}" target="_blank" rel="noopener"
             style="color:var(--brand)">Ver perfil</a>` : '—'}</dd>
      <dt>Teléfono</dt><dd>${esc(c.phone || '—')}</dd>
      <dt>En el CRM</dt><dd>${pill(c.ghl_status, GHL_TXT)}</dd>
      ${c.ghl_error_message
        ? `<dt>Error del CRM</dt><dd style="color:var(--danger)">${esc(c.ghl_error_message)}</dd>` : ''}
    </dl>
    <h3 style="font-size:17px;font-weight:600;margin-bottom:12px">
      Cómo se buscó su email</h3>
    ${logs}`;
  $('modal').classList.add('open');
}
function closeModal() { $('modal').classList.remove('open'); }
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

/* ---------------- enviar al CRM ---------------- */
async function sendToGHL() {
  const ids = [...SELECTED];
  if (!ids.length) return;
  if (!confirm(`¿Enviar ${ids.length} contacto(s) a tu CRM?`)) return;

  $('btn-ghl').disabled = true;
  $('ghl-result').innerHTML = '<div class="alert info">Enviando…</div>';

  const fd = new FormData();
  fd.append('contact_ids', JSON.stringify(ids));
  fd.append('tag', $('ghl-tag').value.trim());

  const r = await fetch('/api/ghl/send', { method: 'POST', body: fd });
  const d = await r.json();

  if (d.error) {
    $('ghl-result').innerHTML = `<div class="alert err">${esc(d.error)}</div>`;
  } else {
    const errs = (d.results || []).filter((x) => x.status === 'error').slice(0, 5);
    const parts = [`<b>${d.sent} contacto(s) enviados al CRM.</b>`];
    if (d.skipped) parts.push(`${d.skipped} no se enviaron porque todavía no tienen email.`);
    if (d.failed) parts.push(`${d.failed} fallaron.`);
    $('ghl-result').innerHTML = `<div class="alert ${d.failed ? 'warn' : 'ok'}">
      ${parts.join(' ')}
      ${errs.length ? '<br><br>' + errs.map((e) => esc(e.message)).join('<br>') : ''}</div>`;
  }
  $('btn-ghl').disabled = false;
  clearSelection();
  await loadMetrics(); await loadContacts();
}

/* ---------------- init ---------------- */
$('f-q').addEventListener('keydown', (e) => { if (e.key === 'Enter') loadContacts(); });
(async () => { await loadMetrics(); await loadContacts(); await refreshPending(); })();
