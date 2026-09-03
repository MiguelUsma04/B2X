/* B2K frontend. Vanilla JS, sin build step.
   Los textos hablan en términos del negocio, no de la implementación.
   Lo largo se cuenta plegado (details.tip) y lo urgente en toasts. */
'use strict';

let ALL_IDS = [];
const SELECTED = new Set();
let POLL = null, MPOLL = null;
const PREV = {};          // último valor de cada número, para animar el cambio

const $ = (id) => document.getElementById(id);
const esc = (s) => (s == null ? '' : String(s).replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])));

const STATUS_TXT = {
  verified: 'Verificado', unverified: 'Sin verificar',
  pending: 'Falta buscar', not_found: 'No se encontró',
};
const GHL_TXT = { pending: 'Sin enviar', sent: 'En el CRM', error: 'Falló' };
const SOURCE_TXT = {
  apollo: 'Venía en el archivo', prospeo: 'Prospeo',
  icypeas: 'Icypeas', hunter: 'Hunter', web: 'Del sitio web', ia: 'IA',
};

const pill = (val, dict) => val
  ? `<span class="pill ${esc(val)}">${esc((dict && dict[val]) || val)}</span>`
  : '<span class="sub dash">—</span>';

/* ======================= quién entró ======================= */
async function loadMe() {
  try {
    const m = await (await fetch('/api/me')).json();
    if (!m.email) return;
    const el = $('quien');
    el.hidden = false;
    el.title = m.email;
    // En el teléfono no entra el mail completo: se muestra el usuario.
    el.innerHTML = `<span class="lbl">${esc(m.email)}</span>
      <span class="solo-chico">${esc(m.email.split('@')[0])}</span>`;
  } catch (e) { /* saber quién entró no puede tumbar la pantalla */ }
}

/* ======================= tema ======================= */
function toggleTheme() {
  const cur = document.documentElement.getAttribute('data-theme');
  const dark = cur ? cur === 'dark'
    : matchMedia('(prefers-color-scheme: dark)').matches;
  const next = dark ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  try { localStorage.setItem('b2x-theme', next); } catch (e) {}
}

/* ======================= toasts ======================= */
const TOAST_ICO = { ok: '✓', err: '✕', warn: '!', info: 'i' };
function toast(msg, kind = 'info', ms = 4200) {
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.innerHTML = `<span class="ico">${TOAST_ICO[kind] || 'i'}</span>
    <span class="msg">${msg}</span>`;
  $('toasts').appendChild(el);
  setTimeout(() => {
    el.classList.add('out');
    setTimeout(() => el.remove(), 220);
  }, ms);
}

/* ======================= diálogo de confirmación =======================
   Reemplaza a confirm()/prompt(), que en el teléfono son un cartel del
   navegador sin contexto y no se pueden leer bien. */
let ASK_RESOLVE = null;

function ask(title, bodyHtml, actions) {
  $('ask-title').textContent = title;
  $('ask-body').innerHTML = bodyHtml;
  $('ask-foot').innerHTML = actions.map((a, i) =>
    `<button class="${a.cls || 'ghost'}" data-i="${i}">${esc(a.label)}</button>`).join('');
  $('ask-foot').querySelectorAll('button').forEach((b) => {
    b.onclick = () => askClose(actions[+b.dataset.i].value);
  });
  $('ask').classList.add('open');
  return new Promise((res) => { ASK_RESOLVE = res; });
}
function askClose(value) {
  $('ask').classList.remove('open');
  if (ASK_RESOLVE) { ASK_RESOLVE(value); ASK_RESOLVE = null; }
}

/* ======================= números animados ======================= */
function animateCounts(scope) {
  scope.querySelectorAll('[data-count]').forEach((el) => {
    const key = el.dataset.key || el.dataset.count;
    const to = +el.dataset.count;
    const from = PREV[key] == null ? 0 : PREV[key];
    PREV[key] = to;
    if (from === to) { el.textContent = to; return; }
    const t0 = performance.now(), dur = 550;
    const step = (t) => {
      const k = Math.min(1, (t - t0) / dur);
      el.textContent = Math.round(from + (to - from) * (1 - Math.pow(1 - k, 3)));
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });
}

/* ======================= navegación ======================= */
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
    // Los buzones cambian desde otra pantalla: se releen al entrar para que
    // el estimado y el botón de enviar reflejen lo que hay ahora.
    if (t.dataset.panel === 'mail' || t.dataset.panel === 'settings') loadSmtp();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };
});
function goToStep(name) {
  const btn = document.querySelector(`.step[data-panel="${name}"]`);
  if (btn) btn.click();
}

/* ======================= métricas ======================= */
async function loadMetrics() {
  const m = await (await fetch('/api/metrics')).json();

  const provs = m.providers.map((p) =>
    `<span class="chip" title="${esc(p.name)}: ${p.enabled ? 'conectado' : 'sin configurar'}">
       <i class="dot ${p.enabled ? 'on' : 'off'}"></i><span class="lbl">${esc(p.name)}</span></span>`);
  provs.push(`<span class="chip" title="CRM: ${m.ghl_configured ? 'conectado' : 'sin configurar'}">
       <i class="dot ${m.ghl_configured ? 'on' : 'off'}"></i><span class="lbl">CRM</span></span>`);
  $('provstat').innerHTML = provs.join('');

  const total = m.total || 0;
  const soloEmail = m.with_email - m.with_both;
  const soloTel = m.with_phone - m.with_both;
  const soloSw = m.only_switchboard || 0;
  const sinDato = total - m.contactable;
  const pct = (n) => (total ? (n / total) * 100 : 0).toFixed(2);

  const leg = (cls, label, n, reach) => `
    <button onclick="applyReach('${reach}')" title="Ver solo estos">
      <i class="sw ${cls}"></i>${label} <b>${n}</b></button>`;

  const src = m.by_source || {};
  const srcDefs = [['apollo', 'Archivo'], ['web', 'Sitio web'], ['prospeo', 'Prospeo'],
                   ['hunter', 'Hunter']];
  const srcTotal = srcDefs.reduce((a, [k]) => a + (src[k] || 0), 0);

  $('metrics').innerHTML = `
    <div class="hero">
      <div class="k">Contactables</div>
      <div class="v" data-count="${m.contactable}" data-key="contactable">0</div>
      <div class="n">${m.pct_contactable}% de ${total} tienen email o teléfono</div>
      <div class="segbar" role="img"
           aria-label="${m.with_both} con email y celular, ${soloEmail} solo email,
                       ${soloTel} solo celular, ${soloSw} solo teléfono no directo,
                       ${sinDato} sin ningún dato">
        <i class="seg-both" style="width:${pct(m.with_both)}%"></i>
        <i class="seg-mail" style="width:${pct(soloEmail)}%"></i>
        <i class="seg-tel"  style="width:${pct(soloTel)}%"></i>
        <i class="seg-sw"   style="width:${pct(soloSw)}%"></i>
      </div>
      <div class="leg">
        ${leg('seg-both', 'Email y celular', m.with_both, 'both')}
        ${leg('seg-mail', 'Solo email', soloEmail, 'email')}
        ${leg('seg-tel', 'Solo celular', soloTel, 'phone')}
        ${soloSw ? leg('seg-sw', 'No directo', soloSw, 'switchboard') : ''}
        ${sinDato ? leg('', 'Sin dato', sinDato, 'none') : ''}
      </div>
    </div>

    <div class="tiles">
        <button class="tile" onclick="applyReach('email')">
          <span class="k">Con email</span>
          <span class="v" data-count="${m.with_email}" data-key="email">0</span>
          <span class="n">${m.pct_with_email}% del total</span>
        </button>
        <button class="tile" onclick="applyReach('phone')">
          <span class="k">Con celular</span>
          <span class="v" data-count="${m.with_phone}" data-key="phone">0</span>
          <span class="n">${m.mobile_available
            ? `${m.mobile_available} más por desbloquear` : 'número directo'}</span>
        </button>
        <button class="tile" onclick="applyGhl('sent')">
          <span class="k">En el CRM</span>
          <span class="v" data-count="${m.by_ghl.sent || 0}" data-key="crm">0</span>
          <span class="n">${(m.by_ghl.error || 0)
            ? `${m.by_ghl.error} fallaron` : 'sin errores'}</span>
        </button>
        <button class="tile" onclick="applyStatus('pending')">
          <span class="k">Falta buscar</span>
          <span class="v" data-count="${m.by_status.pending || 0}" data-key="pend">0</span>
          <span class="n">sin email todavía</span>
        </button>
      <div class="tile src">
        <span class="k" style="margin-bottom:9px">Quién encontró cada email</span>
        ${srcTotal ? `<div class="srcbar">
          ${srcDefs.map(([k]) => `<i class="src-${k}"
            style="width:${(((src[k] || 0) / srcTotal) * 100).toFixed(2)}%"></i>`).join('')}
        </div>
        <div class="srcleg">${srcDefs.map(([k, label]) =>
          `<span><i class="sw dot src-${k}"></i>${label} <b>${src[k] || 0}</b></span>`).join('')}
        </div>` : '<span class="n">Todavía no se buscó ningún email.</span>'}
      </div>
    </div>`;
  animateCounts($('metrics'));

  const opts = '<option value="">Todas</option>' + m.batches.map((b) =>
    `<option value="${b.id}">#${b.id} · ${esc(b.filename)}</option>`).join('');
  for (const id of ['f-batch', 'e-batch']) {
    const el = $(id), cur = el.value;
    el.innerHTML = opts; el.value = cur;
  }

  $('btbody').innerHTML = m.batches.length ? m.batches.map((b) => `<tr>
      <td class="c-main"><b>#${b.id}</b> ${esc(b.filename)}
        <div class="sub">${esc(b.imported_at)}</div></td>
      <td data-label="Archivo" class="hide-sm">${esc(b.filename)}</td>
      <td data-label="Fecha" class="sub hide-sm">${esc(b.imported_at)}</td>
      <td data-label="Filas">${b.total_rows}</td>
      <td data-label="Agregados"><b style="color:var(--ok)">${b.new_contacts}</b></td>
      <td data-label="Repetidos" class="sub">${b.duplicate_contacts}</td>
      <td data-label="Segmento">${esc(b.icp_tag || '—')}</td>
      <td class="c-act"><button class="sm danger"
          onclick="deleteBatch(${b.id}, ${b.new_contacts})">Borrar</button></td></tr>`).join('')
    : `<tr><td colspan="8"><div class="empty"><span class="big">⬆</span>
       <strong>Todavía no cargaste nada</strong>
       Tu primer archivo de Apollo va a aparecer acá.
       <br><button class="primary" onclick="goToStep('import')">Cargar contactos</button>
       </div></td></tr>`;

  const missing = m.providers.filter((p) => !p.enabled).map((p) => p.name);
  $('enrich-warn').innerHTML = missing.length
    ? `<div class="alert warn">Sin configurar: <b>${esc(missing.join(', '))}</b>.
       Se va a usar solo lo conectado.</div>`
    : '';
  return m;
}

/* ======================= filtros ======================= */
const FILTERS = [
  ['f-reach', 'reach', {
    contactable: 'Contactables', both: 'Email y celular', email: 'Con email',
    phone: 'Con celular', switchboard: 'Teléfono no directo', none: 'Sin dato',
  }],
  ['f-status', 'email_status', STATUS_TXT],
  ['f-source', 'email_source', SOURCE_TXT],
  ['f-ghl', 'ghl_status', GHL_TXT],
  ['f-batch', 'import_batch_id', null],
];

function filterParams() {
  const p = new URLSearchParams();
  if ($('f-q').value.trim()) p.set('q', $('f-q').value.trim());
  for (const [id, key] of FILTERS) if ($(id).value) p.set(key, $(id).value);
  return p;
}

function toggleFilters() {
  const box = $('fpanel'), btn = $('btn-filters');
  const open = box.hidden;
  box.hidden = !open;
  btn.setAttribute('aria-expanded', String(open));
}

function renderChips() {
  const chips = [];
  if ($('f-q').value.trim()) {
    chips.push(`<span class="fchip">“${esc($('f-q').value.trim())}”
      <button onclick="clearFilter('f-q')" aria-label="Quitar búsqueda">×</button></span>`);
  }
  for (const [id, , dict] of FILTERS) {
    const el = $(id);
    if (!el.value) continue;
    const label = dict ? (dict[el.value] || el.value)
      : el.options[el.selectedIndex].textContent.trim();
    chips.push(`<span class="fchip">${esc(label)}
      <button onclick="clearFilter('${id}')" aria-label="Quitar filtro">×</button></span>`);
  }
  if (chips.length > 1) {
    chips.push(`<button class="ghost sm" onclick="resetFilters()">Limpiar todo</button>`);
  }
  $('fchips').innerHTML = chips.join('');

  const n = FILTERS.filter(([id]) => $(id).value).length;
  const badge = $('fcount');
  badge.textContent = n; badge.hidden = n === 0;
}

function clearFilter(id) { $(id).value = ''; loadContacts(); }
function resetFilters() {
  $('f-q').value = '';
  FILTERS.forEach(([id]) => { $(id).value = ''; });
  loadContacts();
}
function applyReach(v) { resetTo('f-reach', v); }
function applyGhl(v) { resetTo('f-ghl', v); }
function applyStatus(v) { resetTo('f-status', v); }
function resetTo(id, v) {
  $('f-q').value = '';
  FILTERS.forEach(([i]) => { $(i).value = ''; });
  $(id).value = v;
  goToStep('contacts');
  loadContacts();
}

/* ======================= contactos ======================= */
function skeleton(rows = 5) {
  return Array.from({ length: rows }, () => `
    <tr><td colspan="9" style="padding:0">
      <div class="sk-row">
        <div class="sk" style="width:42%"></div>
        <div class="sk" style="width:68%"></div>
      </div></td></tr>`).join('');
}

async function loadContacts() {
  renderChips();
  $('ctbody').innerHTML = skeleton();
  $('cempty').innerHTML = '';

  const d = await (await fetch('/api/contacts?' + filterParams())).json();
  ALL_IDS = d.all_ids;
  $('ccount').textContent = d.total
    ? `${d.contacts.length} de ${d.total}` : '';

  $('ctbody').innerHTML = d.contacts.map((c) => `
    <tr id="row-${c.id}" class="${SELECTED.has(c.id) ? 'sel' : ''}">
      <td class="c-check"><input type="checkbox" ${SELECTED.has(c.id) ? 'checked' : ''}
          onclick="toggleOne(${c.id},this.checked)"
          aria-label="Marcar ${esc(c.full_name)}"></td>
      <td class="c-main">
        <button class="linkish" onclick="showDetail(${c.id})">${esc(c.full_name)}</button>
        ${c.job_title ? `<div class="sub">${esc(c.job_title)}</div>` : ''}
        ${c.ai_summary ? `<div class="sub" title="${esc(c.ai_summary)}"
          style="margin-top:3px">${esc(c.ai_summary.slice(0, 70))}${
          c.ai_summary.length > 70 ? '…' : ''}</div>` : ''}</td>
      <td data-label="Empresa">${esc(c.company_name || '—')}
        ${c.company_domain ? `<div class="sub">${esc(c.company_domain)}</div>` : ''}</td>
      <td data-label="Email">${c.email
        ? esc(c.email) : '<span class="sub dash">—</span>'}</td>
      <td data-label="Teléfono">${c.phone
        ? esc(c.phone) + telNota(c)
        : '<span class="sub dash">—</span>'}</td>
      <td class="c-tag" data-label="Estado">${pill(c.email_status, STATUS_TXT)}
        ${c.mobile_available && !c.phone
          ? '<div class="sub" title="Prospeo tiene su celular pero no lo reveló. Se desbloquea desde Buscar teléfonos.">hay celular</div>'
          : ''}</td>
      <td class="c-tag" data-label="Fuente">${pill(c.email_source, SOURCE_TXT)}</td>
      <td class="c-tag" data-label="CRM">${pill(c.ghl_status, GHL_TXT)}
        ${c.ghl_error_message
          ? `<div class="sub" title="${esc(c.ghl_error_message)}">${esc(c.ghl_error_message.slice(0, 34))}…</div>`
          : ''}</td>
      <td class="c-act"><button class="sm ghost" onclick="showDetail(${c.id})">Ver detalle</button></td>
    </tr>`).join('');

  if (!d.contacts.length) {
    const sinFiltros = !filterParams().toString();
    $('ctbody').innerHTML = '';
    $('cempty').innerHTML = sinFiltros
      ? `<div class="empty"><span class="big">◆</span>
         <strong>Todavía no hay contactos</strong>Empezá subiendo tu archivo de Apollo.
         <br><button class="primary" onclick="goToStep('import')">Cargar contactos</button></div>`
      : `<div class="empty"><span class="big">⌕</span>
         <strong>Ninguno coincide</strong>Probá con otros filtros.
         <br><button onclick="resetFilters()">Limpiar filtros</button></div>`;
  }
  updateSelInfo();
}

/* De dónde salió el teléfono. Al vendedor le cambia cómo lo usa: al WhatsApp
   le escribe, al conmutador lo llama sabiendo que atiende recepción. */
function telNota(c) {
  if (c.phone_type === 'whatsapp') {
    return '<div class="sub" title="Publicado como WhatsApp: se le escribe directo">WhatsApp</div>';
  }
  if (c.phone_type === 'company') {
    return c.place_id
      ? '<div class="sub" title="El número que el negocio publica en Google Maps">del negocio</div>'
      : '<div class="sub" title="Es el conmutador de la empresa, no el número directo">conmutador</div>';
  }
  return '';
}

/* ======================= selección ======================= */
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
  if (ALL_IDS.length) toast(`${ALL_IDS.length} contactos marcados`, 'info', 2600);
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
  $('selcount').textContent = n;
  $('selhint').textContent = n ? 'listos para enviar' : '';
  $('selbar').classList.toggle('show', n > 0);
  $('btn-ghl').disabled = n === 0;
  for (const id of ['btn-mobile', 'btn-web', 'btn-ai']) {
    const b = $(id);
    if (b) b.disabled = n === 0;
  }
  updateMailBtn();
}

/* ======================= enviar al CRM ======================= */
async function sendToGHL() {
  const ids = [...SELECTED];
  if (!ids.length) return;

  const tag = $('ghl-tag').value.trim();
  const ok = await ask('Enviar al CRM',
    `<p>Se van a enviar <b>${ids.length} contacto(s)</b> a GoHighLevel.</p>
     <p class="help">Los que ya estén en el CRM se saltean. Los que no tengan ni
     email ni teléfono no se pueden enviar.${tag ? ` Etiqueta: <b>${esc(tag)}</b>.` : ''}</p>`,
    [{ label: 'Cancelar', value: false },
     { label: 'Enviar', value: true, cls: 'go' }]);
  if (!ok) return;

  $('btn-ghl').disabled = true;
  $('ghl-result').innerHTML = '<div class="alert info"><span class="pulse">●</span> Enviando…</div>';

  const fd = new FormData();
  fd.append('contact_ids', JSON.stringify(ids));
  fd.append('tag', tag);

  const r = await fetch('/api/ghl/send', { method: 'POST', body: fd });
  const d = await r.json();

  if (d.error) {
    $('ghl-result').innerHTML = `<div class="alert err">${esc(d.error)}</div>`;
    toast('No se pudo enviar al CRM', 'err');
  } else {
    const errs = (d.results || []).filter((x) => x.status === 'error').slice(0, 5);
    const oppErrs = (d.results || []).filter((x) => x.opportunity_error).slice(0, 5);
    const parts = [`<b>${d.sent} contacto(s) enviados al CRM.</b>`];
    if (d.already_in_crm) {
      parts.push(`${d.already_in_crm} ya estaban y no se volvieron a subir`
        + (d.not_verified ? `, ${d.not_verified} sin poder confirmarlo contra el CRM.` : '.'));
    }
    if (d.recreated) {
      parts.push(`${d.recreated} ya no figuraban en el CRM: se volvieron a crear.`);
    }
    if (d.pipeline_configured) {
      const opp = [`Se crearon ${d.opportunities} oportunidad(es) en el embudo.`];
      if (d.opportunities_existing) {
        opp.push(`${d.opportunities_existing} ya tenían la suya.`);
      }
      if (d.opportunities_failed) {
        opp.push(`<b>${d.opportunities_failed} entraron al CRM pero sin oportunidad.</b>`);
      }
      parts.push(opp.join(' '));
    }
    if (d.skipped) {
      parts.push(`${d.skipped} no se enviaron porque no tienen email ni teléfono.`);
    }
    if (d.failed) parts.push(`<b>${d.failed} fallaron.</b>`);

    const detalle = errs.map((e) => esc(e.message))
      .concat(oppErrs.map((e) => esc(e.opportunity_error)));
    const kind = (d.failed || d.opportunities_failed) ? 'warn' : 'ok';
    $('ghl-result').innerHTML = `<div class="alert ${kind}">${parts.join(' ')}
      ${detalle.length ? '<br><br>' + detalle.join('<br>') : ''}</div>`;
    toast(d.sent ? `${d.sent} contacto(s) en el CRM`
                 : 'No había nada nuevo para enviar', kind === 'ok' ? 'ok' : 'warn');
  }
  $('btn-ghl').disabled = false;
  clearSelection();
  await loadMetrics(); await loadContacts();
}

/* ======================= buscar teléfonos ======================= */
async function startMobile() {
  const ids = [...SELECTED];
  if (!ids.length) return;

  const ok = await ask('Buscar teléfonos',
    `<p>Se va a buscar el celular de <b>${ids.length} contacto(s)</b>.</p>
     <div class="alert warn" style="margin-top:12px">Cuesta unos
       <b>${ids.length * 10} créditos</b> — 10 por contacto, diez veces más que
       buscar un email.</div>`,
    [{ label: 'Cancelar', value: false },
     { label: 'Buscar', value: true, cls: 'primary' }]);
  if (!ok) return;

  const fd = new FormData();
  fd.append('contact_ids', JSON.stringify(ids));
  const r = await fetch('/api/mobile/start', { method: 'POST', body: fd });
  const d = await r.json();
  if (!r.ok) {
    toast(esc(d.detail || 'Error'), 'err');
    return;
  }
  if (!d.started) {
    toast(esc(d.message), 'info');
    return;
  }
  $('btn-mobile').disabled = true;
  if (MPOLL) clearInterval(MPOLL);
  MPOLL = setInterval(pollMobile, 900);
  pollMobile();
}

async function pollMobile() {
  const p = await (await fetch('/api/mobile/progress')).json();
  const pct = p.total ? Math.round((p.processed / p.total) * 100) : 0;

  if (p.error) {
    $('mobile-progress').innerHTML = `<div class="alert err">${esc(p.error)}</div>`;
  } else if (p.running) {
    $('mobile-progress').innerHTML = `
      <div class="bignum"><span class="pulse">●</span> ${p.processed} / ${p.total}</div>
      <div class="bar live"><i style="width:${pct}%"></i></div>
      ${p.current_contact ? `<p class="sub">Buscando el teléfono de
        ${esc(p.current_contact)}</p>` : ''}
      <div class="statline"><span style="color:var(--ok)"><b>${p.found}</b> encontrados</span></div>`;
  } else if (p.finished) {
    $('mobile-progress').innerHTML = `<div class="alert ${p.found ? 'ok' : 'info'}">
      <b>Listo.</b> ${p.found} teléfono(s) de ${p.total} contactos.
      ${p.not_found ? ` En ${p.not_found} no había número.` : ''}
      ${foundList(p.found_items, 'Teléfonos encontrados')}</div>`;
  }

  if (!p.running && p.finished) {
    clearInterval(MPOLL); MPOLL = null;
    $('btn-mobile').disabled = SELECTED.size === 0;
    toast(`${p.found} teléfono(s) encontrados`, p.found ? 'ok' : 'info');
    await loadMetrics(); await loadContacts();
  }
}

function foundList(items, label) {
  if (!items || !items.length) return '';
  const rows = items.map((f) => `
    <tr><td style="padding:5px 12px 5px 0;border:0">${esc(f.name || '—')}
        ${f.company ? `<div class="sub">${esc(f.company)}</div>` : ''}</td>
      <td style="padding:5px 12px 5px 0;border:0"><b>${esc(f.value)}</b></td>
      <td style="padding:5px 0;border:0">${f.provider
        ? `<span class="pill ${esc(f.provider)}">${esc(SOURCE_TXT[f.provider] || f.provider)}</span>`
        : ''}</td></tr>`).join('');
  return `<details class="tip" style="margin:12px 0 0">
      <summary>${esc(label)} (${items.length})</summary>
      <div class="tip-b"><table style="font-size:13.5px">${rows}</table></div></details>`;
}

/* ======================= buscar en Google Maps ======================= */
/* Maps y CSV son dos formas de lo mismo —traer contactos— y comparten sección. */
function switchSource(cual) {
  const esMaps = cual === 'maps';
  $('src-maps').hidden = !esMaps;
  $('src-csv').hidden = esMaps;
  for (const [id, on] of [['seg-maps', esMaps], ['seg-csv', !esMaps]]) {
    $(id).classList.toggle('active', on);
    $(id).setAttribute('aria-selected', String(on));
  }
  if (esMaps) loadUsage();
}

/* Google cobra por consulta, no por negocio, y regala las primeras 1.000 del
   mes. Sin este contador no hay forma de saber cuánto queda de ese tramo. */
async function loadUsage() {
  try {
    renderUsage(await (await fetch('/api/places/usage')).json());
  } catch (e) {
    /* el contador nunca puede tumbar la pantalla de búsqueda */
  }
}

const MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
               'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];
function fecha(iso) {
  if (!iso) return '1 del mes que viene';
  const [a, m, d] = iso.split('-').map(Number);
  return `${d} de ${MESES[m - 1]}`;
}

function renderUsage(u) {
  const box = $('p-usage');
  if (!box) return;
  const pct = Math.min(100, (u.requests / u.free_limit) * 100);
  box.className = 'usage ' + (u.billable ? 'over'
    : (u.remaining <= u.free_limit * 0.1 ? 'tight' : ''));
  box.innerHTML = `
    <div class="top">
      <span><b>${u.requests}</b> de ${u.free_limit} consultas gratis este mes</span>
      <span>${u.billable
        ? `<b style="color:var(--danger)">US$${u.estimated_cost}</b> a facturar`
        : `quedan <b>${u.remaining}</b>`}</span>
    </div>
    <div class="bar"><i style="width:${pct}%"></i></div>
    <div>${u.searches} búsqueda(s) · ${u.results} negocios traídos · cada consulta
      trae hasta 20 y cuesta US$${u.usd_per_request} recién pasado el tramo gratis.
      El cupo se renueva el ${fecha(u.resets_on)}.</div>`;
}


let PMAP = null;   // instancia de Leaflet; se recrea en cada búsqueda

// Qué se puede hacer con cada negocio. El color va con los tokens del tema.
const PIN_KINDS = {
  site:  { css: 'pin-site',  label: 'Tiene sitio web',
           hint: 'Se le puede sacar el email gratis' },
  phone: { css: 'pin-phone', label: 'Solo teléfono',
           hint: 'Para llamar o WhatsApp' },
  none:  { css: 'pin-none',  label: 'Sin datos de contacto',
           hint: 'Habría que buscarlos aparte' },
};

const pinKind = (p) => (p.domain ? 'site' : (p.phone ? 'phone' : 'none'));

// Marcador con forma de pin, en SVG para que herede el color del CSS.
function pinIcon(kind) {
  return L.divIcon({
    className: 'pin ' + PIN_KINDS[kind].css,
    iconSize: [26, 34], iconAnchor: [13, 33], popupAnchor: [0, -30],
    html: `<svg viewBox="0 0 26 34" width="26" height="34" aria-hidden="true">
        <path d="M13 0C5.8 0 0 5.8 0 13c0 9.2 11.4 20 12 20.6.6-.6 14-11.4 14-20.6C26 5.8 20.2 0 13 0z"/>
        <circle cx="13" cy="13" r="4.6" class="pin-dot"/>
      </svg>`,
  });
}

function placeCard(p) {
  const k = PIN_KINDS[pinKind(p)];
  return `<div class="pin-card">
      <div class="pin-card-h">
        <b>${esc(p.name)}</b>
        <span class="tag ${esc(k.css)}">${esc(k.label)}</span>
      </div>
      ${p.category ? `<div class="sub">${esc(p.category)}</div>` : ''}
      <dl class="pin-kv">
        ${p.phone ? `<dt>Teléfono</dt><dd>${esc(p.phone)}</dd>` : ''}
        ${p.domain ? `<dt>Sitio</dt><dd>${esc(p.domain)}</dd>` : ''}
        ${p.rating ? `<dt>Google</dt><dd>${p.rating} <span class="sub">(${p.rating_count || 0})</span></dd>` : ''}
        ${p.address ? `<dt>Dirección</dt><dd class="sub">${esc(p.address)}</dd>` : ''}
      </dl>
      ${p.maps_url ? `<a href="${esc(p.maps_url)}" target="_blank" rel="noopener"
         class="pin-link">Ver en Google Maps →</a>` : ''}
    </div>`;
}

function renderMap(places) {
  const box = $('p-map');
  const legend = $('p-map-legend');
  if (!box) return;

  const pts = (places || []).filter((p) => p.lat != null && p.lng != null);
  if (!pts.length || typeof L === 'undefined') {
    // Sin coordenadas (o sin Leaflet) el mapa no aporta: se oculta entero.
    box.style.display = 'none';
    if (legend) legend.style.display = 'none';
    return;
  }
  box.style.display = '';

  // Leyenda: solo los tipos que realmente aparecen en este resultado.
  if (legend) {
    const usados = ['site', 'phone', 'none'].filter((k) => pts.some((p) => pinKind(p) === k));
    legend.style.display = '';
    legend.innerHTML = usados.map((k) => {
      const n = pts.filter((p) => pinKind(p) === k).length;
      return `<span class="lg-item">
          <i class="lg-pin ${PIN_KINDS[k].css}"></i>
          <span><b>${n}</b> ${esc(PIN_KINDS[k].label.toLowerCase())}</span>
        </span>`;
    }).join('');
  }

  if (PMAP) { PMAP.remove(); PMAP = null; }
  PMAP = L.map(box, { scrollWheelZoom: false, attributionControl: false });

  // OpenStreetMap: libre y sin API key. En modo oscuro se atenúa por CSS para
  // que no encandile y los pines sigan destacando.
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    { maxZoom: 19 }).addTo(PMAP);
  L.control.attribution({ prefix: false })
    .addAttribution('&copy; OpenStreetMap').addTo(PMAP);

  const marks = pts.map((p) => {
    const m = L.marker([p.lat, p.lng], {
      icon: pinIcon(pinKind(p)), title: p.name, riseOnHover: true,
    }).addTo(PMAP);
    m.bindPopup(placeCard(p), { closeButton: true, maxWidth: 280, autoPan: true });
    // El popup se abre a mano en los dos casos: con divIcon el clic por
    // defecto de Leaflet no siempre dispara, y así el táctil queda cubierto.
    m.on('mouseover', () => m.openPopup());
    m.on('click', (ev) => { L.DomEvent.stop(ev); m.openPopup(); });
    return m;
  });

  PMAP.fitBounds(L.featureGroup(marks).getBounds().pad(0.18));
  // El contenedor nace dentro de una tarjeta recién insertada: sin esto,
  // Leaflet mide 0 de alto y el mapa sale gris.
  setTimeout(() => PMAP.invalidateSize(), 60);
}

async function searchPlaces() {
  const q = $('p-q').value.trim();
  if (!q) { toast('Escribí qué negocios buscar y dónde', 'warn'); return; }

  $('btn-places').disabled = true;
  $('p-alert').innerHTML =
    '<div class="alert info"><span class="pulse">●</span> Buscando en Google Maps…</div>';
  $('p-box').innerHTML = '';

  const fd = new FormData();
  fd.append('query', q);
  fd.append('max_results', $('p-max').value);
  const r = await fetch('/api/places/search', { method: 'POST', body: fd });
  const d = await r.json();
  $('btn-places').disabled = false;

  if (!r.ok || d.error) {
    $('p-alert').innerHTML =
      `<div class="alert err">${esc(d.error || d.detail || 'No se pudo buscar.')}</div>`;
    return;
  }
  if (!d.total) {
    // Sin nada nuevo hay dos motivos muy distintos, y la salida es distinta.
    $('p-alert').innerHTML = d.already
      ? `<div class="alert info"><b>Nada nuevo por acá.</b>
         Los ${d.already} negocios que Google devuelve para “${esc(q)}” ya los
         habías visto. Google corta en 60 resultados por búsqueda, así que para
         seguir hay que cambiar el texto: acotá la zona (un barrio, una avenida)
         o probá otro nombre del rubro.</div>`
      : `<div class="alert info">Google no devolvió negocios para “${esc(q)}”.
         Probá con otras palabras o una zona más amplia.</div>`;
    if (d.usage) renderUsage(d.usage);
    return;
  }
  $('p-alert').innerHTML = d.warning ? `<div class="alert warn">${esc(d.warning)}</div>` : '';
  if (d.usage) renderUsage(d.usage);

  const filas = d.places.map((p) => `<tr>
    <td class="c-main"><b>${esc(p.name)}</b>
      ${p.category ? `<div class="sub">${esc(p.category)}</div>` : ''}</td>
    <td data-label="Teléfono">${p.phone ? esc(p.phone) : '<span class="sub dash">—</span>'}</td>
    <td data-label="Sitio">${p.domain
      ? esc(p.domain)
      : (p.social_url ? '<span class="sub">solo redes</span>'
                      : '<span class="sub dash">—</span>')}</td>
    <td data-label="Google">${p.rating
      ? `${p.rating} <span class="sub">(${p.rating_count || 0})</span>`
      : '<span class="sub dash">—</span>'}</td>
    <td data-label="Dirección" class="sub">${esc(p.address || '—')}</td></tr>`).join('');

  const salteados = (d.seen || []).map((p) => `<tr>
    <td class="c-main">${esc(p.name)}</td>
    <td data-label="Teléfono" class="sub">${esc(p.phone || '—')}</td>
    <td data-label="Sitio" class="sub">${esc(p.domain || '—')}</td></tr>`).join('');

  $('p-box').innerHTML = `
    <div class="card list">
      <h2>${d.total} negocio${d.total === 1 ? '' : 's'}
        ${d.repeat ? 'nuevo' + (d.total === 1 ? '' : 's') : 'encontrado' + (d.total === 1 ? '' : 's')}</h2>
      <div class="body" style="padding-bottom:0">
        <div class="statline">
          <span><b>${d.with_phone}</b> con teléfono</span>
          <span><b>${d.with_site}</b> con sitio web</span>
          ${d.already ? `<span class="sub"><b>${d.already}</b> ya vistos, salteados</span>` : ''}
        </div>
        ${d.exhausted && d.already ? `<p class="help">Google ya no tiene más para
          este texto. La próxima vez vas a tener que acotar la zona o cambiar
          las palabras para encontrar otros.</p>` : ''}
        ${salteados ? `<details class="tip" style="margin:12px 0 0">
          <summary>Los ${d.already} que se saltearon${d.already_saved
            ? ` (${d.already_saved} ya son contactos tuyos)` : ''}</summary>
          <div class="tip-b"><div class="tbl-scroll"><table class="rtable">
            <thead><tr><th>Negocio</th><th>Teléfono</th><th>Sitio</th></tr></thead>
            <tbody>${salteados}</tbody></table></div></div></details>` : ''}
      </div>
      <div id="p-map-legend" class="map-legend"></div>
      <div id="p-map" class="map"></div>
      <div class="tbl-scroll"><table class="rtable">
        <thead><tr><th>Negocio</th><th>Teléfono</th><th>Sitio</th>
          <th>Google</th><th>Dirección</th></tr></thead>
        <tbody>${filas}</tbody></table></div>
    </div>
    <div class="card"><div class="body">
      <div class="row">
        <div class="field">
          <label class="fld" for="p-tag">Nombre de la carga (opcional)</label>
          <input id="p-tag" value="${esc(q)}">
        </div>
        <button class="primary" id="btn-p-import" onclick="importPlaces()">
          Guardar ${d.total} negocios</button>
      </div>
      <p class="help">Los que ya tenés no se duplican${d.with_site
        ? '. Después podés leerles el sitio para sacarles el email' : ''}.</p>
      <div id="p-result" style="margin-top:14px"></div>
    </div></div>`;

  renderMap(d.places);   // el contenedor ya existe en el DOM
}

async function importPlaces() {
  const btn = $('btn-p-import');
  if (btn) btn.disabled = true;                 // la búsqueda ya se consumió
  const fd = new FormData();
  fd.append('icp_tag', ($('p-tag') && $('p-tag').value.trim()) || '');
  const r = await fetch('/api/places/import', { method: 'POST', body: fd });
  const d = await r.json();
  if (!r.ok) {
    if (btn) btn.disabled = false;
    toast(esc(d.detail || 'Error'), 'err');
    return;
  }

  const dup = d.duplicate_contacts ? ` Se descartaron ${d.duplicate_contacts} repetidos.` : '';
  $('p-result').innerHTML = `<div class="alert ok">
    <b>${d.new_contacts} negocios agregados.</b>${dup}
    <div class="rowend">
      <button class="primary" onclick="goToStep('contacts')">Ver la lista →</button>
    </div></div>`;
  toast(`${d.new_contacts} negocios agregados`, 'ok');
  await loadMetrics(); await loadContacts();
}

/* ======================= ficha con IA ======================= */
let AIPOLL = null;

async function loadAiUsage() {
  const box = $('ai-usage');
  if (!box) return;
  try {
    const u = await (await fetch('/api/ai/usage')).json();
    box.className = 'usage';
    box.innerHTML = u.configured
      ? `<div class="top"><span><b>${u.profiles}</b> ficha(s) este mes</span>
         <span>${(u.tokens_in + u.tokens_out).toLocaleString('es')} tokens</span></div>
         <div>modelo ${esc(u.model)}</div>`
      : `<div>Falta <b>OPENAI_API_KEY</b> en el .env: sin eso no se puede
         armar ninguna ficha. La key sale de platform.openai.com y necesita
         crédito cargado.</div>`;
  } catch (e) { /* el contador no puede tumbar la pantalla */ }
}

async function startAI() {
  const ids = [...SELECTED];
  if (!ids.length) return;

  const ok = await ask('Armar la ficha con IA',
    `<p>Se va a leer el sitio de <b>${ids.length} contacto(s)</b> y armar su ficha.</p>
     <p class="help">Solo se analizan los que tengan sitio web y no tengan ficha
     todavía. Esto sí consume de tu cuenta de IA.</p>`,
    [{ label: 'Cancelar', value: false },
     { label: 'Analizar', value: true, cls: 'primary' }]);
  if (!ok) return;

  const fd = new FormData();
  fd.append('contact_ids', JSON.stringify(ids));
  const r = await fetch('/api/ai/start', { method: 'POST', body: fd });
  const d = await r.json();
  if (!r.ok) { toast(esc(d.detail || 'Error'), 'err'); return; }
  if (!d.started) { toast(esc(d.message), 'info'); return; }

  $('btn-ai').disabled = true;
  if (AIPOLL) clearInterval(AIPOLL);
  AIPOLL = setInterval(pollAI, 1200);
  pollAI();
}

async function pollAI() {
  const p = await (await fetch('/api/ai/progress')).json();
  const pct = p.total ? Math.round((p.processed / p.total) * 100) : 0;

  if (p.error && !p.running && !p.processed) {
    $('ai-progress').innerHTML = `<div class="alert err">${esc(p.error)}</div>`;
  } else if (p.running) {
    $('ai-progress').innerHTML = `
      <div class="bignum"><span class="pulse">●</span> ${p.processed} / ${p.total}</div>
      <div class="bar live"><i style="width:${pct}%"></i></div>
      <p class="sub">${p.current_provider ? `Leyendo ${esc(p.current_provider)}` : 'Arrancando…'}</p>
      <div class="statline">
        <span style="color:var(--ok)"><b>${p.found}</b> fichas</span>
        <span class="sub"><b>${p.not_found}</b> sin resultado</span>
      </div>`;
  } else if (p.finished) {
    $('ai-progress').innerHTML = `<div class="alert ${p.found ? 'ok' : 'warn'}">
      <b>Análisis terminado.</b> ${p.found} ficha(s) de ${p.total} sitio(s).
      ${p.error ? `<br><br>${esc(p.error)}` : ''}
      ${foundList(p.found_items, 'Fichas armadas')}</div>`;
  }

  if (!p.running && p.finished) {
    clearInterval(AIPOLL); AIPOLL = null;
    $('btn-ai').disabled = SELECTED.size === 0;
    toast(`${p.found} ficha(s) armada(s)`, p.found ? 'ok' : 'warn');
    await loadAiUsage(); await loadContacts();
  }
}

/* La ficha en el detalle. El JSON viene del modelo, así que nada se da por
   supuesto: cada bloque aparece solo si trae algo. */
const VENDE_A = {
  empresas: 'a empresas', consumidor_final: 'al consumidor final',
  ambos: 'a empresas y a consumidor final', no_esta_claro: 'no queda claro',
};

function renderPerfil(c) {
  let p;
  try { p = JSON.parse(c.ai_profile); } catch (e) { return ''; }
  if (!p || typeof p !== 'object') return '';

  const lista = (v) => (Array.isArray(v) ? v.filter(Boolean) : []);
  const bloque = (titulo, cuerpo) => (cuerpo
    ? `<dt>${titulo}</dt><dd>${cuerpo}</dd>` : '');
  const chips = (arr) => lista(arr).map((x) =>
    `<span class="pill pending">${esc(String(x))}</span>`).join(' ');

  const personas = lista(p.personas).map((q) => `
    <div style="margin-bottom:6px"><b>${esc(q.nombre || '—')}</b>
      ${q.cargo ? `<span class="sub"> · ${esc(q.cargo)}</span>` : ''}
      ${q.email ? `<div class="sub">${esc(q.email)}</div>` : ''}
      ${q.telefono ? `<div class="sub">${esc(q.telefono)}</div>` : ''}</div>`).join('');

  const conf = { alta: 'verified', media: 'unverified', baja: 'not_found' };
  return `
    <h3 style="font-size:15px;font-weight:700;margin:20px 0 10px">
      Ficha del negocio
      <span class="pill ${conf[p.confianza] || 'pending'}">confianza ${esc(p.confianza || '?')}</span>
    </h3>
    ${p.resumen ? `<p class="lede">${esc(p.resumen)}</p>` : ''}
    <dl class="kv">
      ${bloque('Qué vende', chips(p.que_vende))}
      ${bloque('Le vende', p.vende_a ? esc(VENDE_A[p.vende_a] || p.vende_a) : '')}
      ${bloque('Se diferencia por', p.propuesta_de_valor ? esc(p.propuesta_de_valor) : '')}
      ${bloque('Personas', personas)}
      ${bloque('Antigüedad', p.anios_en_el_mercado ? esc(p.anios_en_el_mercado) : '')}
      ${bloque('Tamaño', p.tamanio ? esc(p.tamanio) : '')}
      ${bloque('Opera en', chips(p.ciudades))}
      ${bloque('Marcas', chips(p.marcas_o_certificaciones))}
      ${bloque('Idiomas', chips(p.idiomas))}
      ${bloque('Vende online', p.vende_online === true ? 'sí'
        : (p.vende_online === false ? 'no' : ''))}
      ${bloque('Redes', lista(p.redes).map((u) =>
        `<a href="${esc(u)}" target="_blank" rel="noopener"
           style="color:var(--brand);display:block">${esc(String(u).slice(0, 60))}</a>`).join(''))}
    </dl>
    ${p.gancho ? `<div class="alert info"><b>Por dónde entrarle.</b>
      ${esc(p.gancho)}</div>` : ''}`;
}

/* ======================= leer el sitio web ======================= */
let WPOLL = null;

async function startWebsite() {
  const ids = [...SELECTED];
  if (!ids.length) return;

  const fd = new FormData();
  fd.append('contact_ids', JSON.stringify(ids));
  const r = await fetch('/api/website/start', { method: 'POST', body: fd });
  const d = await r.json();
  if (!r.ok) { toast(esc(d.detail || 'Error'), 'err'); return; }
  if (!d.started) { toast(esc(d.message), 'info'); return; }

  $('btn-web').disabled = true;
  toast(`Leyendo ${d.queued} sitio(s)…`, 'info', 2600);
  if (WPOLL) clearInterval(WPOLL);
  WPOLL = setInterval(pollWebsite, 900);
  pollWebsite();
}

async function pollWebsite() {
  const p = await (await fetch('/api/website/progress')).json();
  const pct = p.total ? Math.round((p.processed / p.total) * 100) : 0;
  const tel = (p.by_provider || {}).telefono || 0;

  if (p.error) {
    $('web-progress').innerHTML = `<div class="alert err">${esc(p.error)}</div>`;
  } else if (p.running) {
    $('web-progress').innerHTML = `
      <div class="bignum"><span class="pulse">●</span> ${p.processed} / ${p.total}</div>
      <div class="bar live"><i style="width:${pct}%"></i></div>
      <p class="sub">${p.current_provider ? `Leyendo ${esc(p.current_provider)}` : 'Arrancando…'}</p>
      <div class="statline">
        <span style="color:var(--ok)"><b>${p.found}</b> emails</span>
        <span><b>${tel}</b> teléfonos</span>
      </div>`;
  } else if (p.finished) {
    $('web-progress').innerHTML = `<div class="alert ${p.found || tel ? 'ok' : 'info'}">
      <b>Lectura terminada.</b> ${p.found} email(s) y ${tel} teléfono(s) nuevos
      de ${p.total} sitio(s).
      ${foundList(p.found_items, 'Emails encontrados')}</div>`;
  }

  if (!p.running && p.finished) {
    clearInterval(WPOLL); WPOLL = null;
    $('btn-web').disabled = SELECTED.size === 0;
    toast(`${p.found} email(s) y ${tel} teléfono(s) del sitio`,
          p.found || tel ? 'ok' : 'info');
    await loadMetrics(); await loadContacts();
  }
}

/* ======================= correos ======================= */
/* Es lo único que sale hacia afuera y toca gente real: todo pasa por una
   confirmación que dice a cuántos y desde qué cuenta. */
let MAILPOLL = null;

/* Vive en una hoja y no al pie de la lista: con cientos de contactos, llegar
   scrolleando hasta el final para escribir un correo no es un camino. */
let MAILBOXES = [];

/* ------------------------------- buzones ------------------------------- */
async function loadSmtp() {
  const c = await (await fetch('/api/mail/config')).json();
  MAILBOXES = c.mailboxes || [];

  const hint = $('smtp-hint');
  if (hint) {
    hint.textContent = MAILBOXES.length
      ? `${MAILBOXES.filter((m) => m.active).length} activo(s) · ${c.capacity_today} correos hoy`
      : 'sin configurar';
  }

  const lista = $('mb-list');
  if (lista) {
    lista.innerHTML = MAILBOXES.length ? MAILBOXES.map((m) => `
      <div class="mb ${m.active ? '' : 'off'}">
        <div class="mb-h">
          <b>${esc(m.label || m.from_email)}</b>
          <span class="tag ${m.active ? 'pin-site' : 'pin-none'}">
            ${m.active ? 'activo' : 'pausado'}</span>
          ${!m.has_password ? '<span class="tag pin-none">sin contraseña</span>' : ''}
        </div>
        <div class="sub">${esc(m.from_email)} · ${esc(m.host)}:${m.port}</div>
        <div class="mb-bar" title="${m.sent_today} enviados hoy de ${m.daily_cap}">
          <i style="width:${m.daily_cap ? Math.min(100, m.sent_today / m.daily_cap * 100) : 0}%"></i>
        </div>
        <div class="sub">${m.sent_today} de ${m.daily_cap} hoy · quedan ${m.remaining}</div>
        <div class="mb-a">
          <button class="ghost sm" onclick="editMailbox(${m.id})">Editar</button>
          <button class="ghost sm" onclick="toggleMailbox(${m.id})">
            ${m.active ? 'Pausar' : 'Activar'}</button>
          <button class="ghost sm" onclick="testMailbox(${m.id})">Probar</button>
          <button class="ghost sm danger" onclick="deleteMailbox(${m.id})">Borrar</button>
        </div>
        <div id="mb-test-${m.id}"></div>
      </div>`).join('')
      : `<div class="empty"><strong>Todavía no hay buzones</strong>
         Agregá al menos uno para poder enviar correos.</div>`;
  }

  const vars = $('mail-vars');
  if (vars) {
    vars.innerHTML = Object.entries(c.variables || {}).map(([k, desc]) =>
      `<button class="fchip" title="${esc(desc)}" onclick="insertVar('${k}')"
         style="cursor:pointer">{{${k}}}</button>`).join('');
  }

  const rh = $('mail-rotate-help');
  if (rh) {
    const act = MAILBOXES.filter((m) => m.active && m.configured);
    rh.textContent = act.length > 1
      ? `Se van a repartir entre ${act.length} buzones: ${act.map((m) => m.from_email).join(', ')}.`
      : (act.length === 1
          ? `Todos salen desde ${act[0].from_email}. Agregá otro buzón para repartir el volumen.`
          : 'No hay buzones activos: configurá uno en la sección Buzones.');
  }
  updateMailBtn();
}

function newMailbox() { mailboxForm(null); }
function editMailbox(id) { mailboxForm(MAILBOXES.find((m) => m.id === id) || null); }

function mailboxForm(m) {
  const v = m || { port: 587, security: 'starttls', daily_cap: 50, active: 1 };
  $('mb-form').innerHTML = `
    <div class="card" style="margin-top:16px">
      <h2>${m ? 'Editar buzón' : 'Buzón nuevo'}</h2>
      <div class="body">
        <div class="row">
          <div class="field">
            <label class="fld" for="mb-label">Nombre para reconocerlo</label>
            <input id="mb-label" value="${esc(v.label || '')}" placeholder="Ventas">
          </div>
          <div class="field">
            <label class="fld" for="mb-from">Correo que ven</label>
            <input id="mb-from" value="${esc(v.from_email || '')}"
                   placeholder="ventas@gmarketing.co">
          </div>
          <div class="field">
            <label class="fld" for="mb-name">Nombre que ven</label>
            <input id="mb-name" value="${esc(v.from_name || '')}"
                   placeholder="Equipo Gmarketing">
          </div>
        </div>
        <div class="row" style="margin-top:10px">
          <div class="field">
            <label class="fld" for="mb-host">Servidor</label>
            <input id="mb-host" value="${esc(v.host || '')}" placeholder="smtp.gmail.com">
          </div>
          <div class="field" style="flex:0 0 110px">
            <label class="fld" for="mb-port">Puerto</label>
            <input id="mb-port" type="number" inputmode="numeric" value="${v.port || 587}">
          </div>
          <div class="field" style="flex:0 0 170px">
            <label class="fld" for="mb-sec">Seguridad</label>
            <select id="mb-sec">
              <option value="starttls" ${v.security === 'starttls' ? 'selected' : ''}>STARTTLS (587)</option>
              <option value="ssl" ${v.security === 'ssl' ? 'selected' : ''}>SSL (465)</option>
              <option value="none" ${v.security === 'none' ? 'selected' : ''}>Ninguna</option>
            </select>
          </div>
        </div>
        <div class="row" style="margin-top:10px">
          <div class="field">
            <label class="fld" for="mb-user">Usuario</label>
            <input id="mb-user" autocomplete="off" value="${esc(v.username || '')}"
                   placeholder="vos@gmarketing.co">
          </div>
          <div class="field">
            <label class="fld" for="mb-pass">Contraseña de aplicación</label>
            <input id="mb-pass" type="password" autocomplete="new-password"
                   placeholder="${m && m.has_password ? 'dejala vacía para no cambiarla' : 'la de aplicación, no la del correo'}">
          </div>
          <div class="field" style="flex:0 0 170px">
            <label class="fld" for="mb-cap">Tope por día</label>
            <input id="mb-cap" type="number" inputmode="numeric" min="1"
                   value="${v.daily_cap || 50}">
          </div>
        </div>
        <p class="help">Con una cuenta nueva conviene empezar bajo (20–30 por día)
          y subir de a poco: el volumen repentino es lo que dispara los filtros.</p>
        <div class="row" style="margin-top:14px">
          <button class="primary" onclick="saveMailbox(${m ? m.id : ''})">Guardar</button>
          <button class="ghost" onclick="cancelMailboxForm()">Cancelar</button>
        </div>
      </div>
    </div>`;
  $('mb-form').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function cancelMailboxForm() { $('mb-form').innerHTML = ''; }

async function saveMailbox(id) {
  const fd = new FormData();
  if (id) fd.append('id', id);
  for (const [campo, el] of [['label', 'mb-label'], ['from_email', 'mb-from'],
                             ['from_name', 'mb-name'], ['host', 'mb-host'],
                             ['port', 'mb-port'], ['security', 'mb-sec'],
                             ['username', 'mb-user'], ['password', 'mb-pass'],
                             ['daily_cap', 'mb-cap']]) {
    fd.append(campo, ($(el) && $(el).value.trim()) || '');
  }
  const r = await fetch('/api/mail/config', { method: 'POST', body: fd });
  const d = await r.json();
  if (!r.ok) { toast(esc(d.detail || 'Error'), 'err'); return; }
  cancelMailboxForm();
  toast('Buzón guardado', 'ok');
  loadSmtp();
}

async function toggleMailbox(id) {
  const m = MAILBOXES.find((x) => x.id === id);
  if (!m) return;
  const fd = new FormData();
  fd.append('id', id);
  for (const campo of ['label', 'from_email', 'from_name', 'host', 'port',
                       'security', 'username', 'daily_cap']) {
    fd.append(campo, m[campo] == null ? '' : m[campo]);
  }
  fd.append('active', m.active ? '0' : '1');
  await fetch('/api/mail/config', { method: 'POST', body: fd });
  loadSmtp();
}

async function deleteMailbox(id) {
  const m = MAILBOXES.find((x) => x.id === id);
  if (!confirm(`¿Borrar el buzón ${m ? m.from_email : ''}?\n\nLos correos ya enviados desde él no se tocan.`)) return;
  await fetch(`/api/mail/config/${id}/delete`, { method: 'POST' });
  toast('Buzón borrado', 'ok');
  loadSmtp();
}

async function testMailbox(id) {
  const m = MAILBOXES.find((x) => x.id === id);
  const to = prompt('¿A qué dirección mando la prueba?', (m && m.from_email) || '');
  if (!to) return;
  const caja = $('mb-test-' + id);
  caja.innerHTML = '<div class="alert info"><span class="pulse">●</span> Mandando…</div>';
  const fd = new FormData();
  fd.append('to', to.trim());
  fd.append('mailbox_id', id);
  const d = await (await fetch('/api/mail/test', { method: 'POST', body: fd })).json();
  caja.innerHTML = d.ok
    ? `<div class="alert ok">Salió. Revisá ${esc(to)} — si no llegó, mirá el spam.</div>`
    : `<div class="alert err">${esc(d.error || d.detail || 'No se pudo enviar.')}</div>`;
}

/* --------------------------- plantilla guardada --------------------------- */
const TPL_KEY = 'b2k-mail-tpl';

function saveTemplate() {
  try {
    localStorage.setItem(TPL_KEY, JSON.stringify({
      subject: $('mail-subject').value, body: $('mail-body').value,
      every: $('mail-every').value, jitter: $('mail-jitter').value,
    }));
    toast('Plantilla guardada en este navegador', 'ok');
  } catch (e) { toast('No se pudo guardar la plantilla', 'warn'); }
}

function loadTemplate() {
  try {
    const t = JSON.parse(localStorage.getItem(TPL_KEY) || '{}');
    if (t.subject && !$('mail-subject').value) $('mail-subject').value = t.subject;
    if (t.body && !$('mail-body').value) $('mail-body').value = t.body;
    if (t.every) $('mail-every').value = t.every;
    if (t.jitter) $('mail-jitter').value = t.jitter;
  } catch (e) { /* plantilla corrupta: se ignora */ }
}

function insertVar(nombre) {
  // Va donde está el cursor, que es donde el usuario lo está esperando.
  const el = document.activeElement === $('mail-subject') ? $('mail-subject') : $('mail-body');
  const t = `{{${nombre}}}`;
  const i = el.selectionStart ?? el.value.length;
  el.value = el.value.slice(0, i) + t + el.value.slice(el.selectionEnd ?? i);
  el.focus();
  el.selectionStart = el.selectionEnd = i + t.length;
  updateMailBtn();
}

function updateMailBtn() {
  const b = $('btn-mail');
  if (!b) return;
  const activos = MAILBOXES.filter((m) => m.active && m.configured);
  const capacidad = activos.reduce((a, m) => a + (m.remaining || 0), 0);

  b.disabled = SELECTED.size === 0 || !$('mail-subject').value.trim()
               || !$('mail-body').value.trim() || !activos.length;

  const listo = $('mail-ready');
  if (listo) {
    listo.innerHTML = !SELECTED.size
      ? `<div class="alert info">No hay contactos marcados. Andá a
         <b>Enviar</b>, marcá a quién escribirle y volvé.</div>`
      : (!activos.length
        ? `<div class="alert warn">No hay buzones activos. Configurá uno en
           <b>Buzones</b> antes de enviar.</div>`
        : `<div class="alert ok"><b>${SELECTED.size} contacto(s) marcados.</b>
           Saldrán desde ${activos.length === 1 ? esc(activos[0].from_email)
             : activos.length + ' buzones'}.</div>`);
  }

  const n = SELECTED.size;
  const cada = Math.max(1, +$('mail-every').value || 3);
  const limite = +$('mail-limit').value || n;
  const cuantos = Math.min(n, limite);
  const est = $('mail-estimate');
  if (!est) return;
  if (!cuantos || !activos.length) { est.textContent = ''; return; }

  const hoy = Math.min(cuantos, capacidad);
  const minutos = Math.round(hoy * cada);
  const dias = capacidad ? Math.ceil(cuantos / capacidad) : 0;
  est.textContent =
    `${cuantos} correo(s), uno cada ~${cada} min. ` +
    (cuantos > capacidad
      ? `Hoy entran ${hoy} (el tope de los buzones); el resto sigue mañana, ~${dias} día(s) en total.`
      : `Termina en ~${minutos > 90 ? Math.round(minutos / 60) + ' h' : minutos + ' min'}.`);
}

async function previewMail() {
  const ids = [...SELECTED];
  if (!ids.length) { toast('Marcá contactos primero', 'warn'); return; }
  const fd = new FormData();
  fd.append('contact_ids', JSON.stringify(ids));
  fd.append('subject', $('mail-subject').value);
  fd.append('body', $('mail-body').value);
  const d = await (await fetch('/api/mail/preview', { method: 'POST', body: fd })).json();

  const avisos = [];
  if (d.no_email) avisos.push(`${d.no_email} sin email`);
  if (d.already_written) avisos.push(`${d.already_written} ya recibieron uno`);
  const muestras = (d.samples || []).map((m) => `
    <div class="log"><div class="log-h"><b>${esc(m.email)}</b>
      <span class="sub">${esc(m.name || '')}</span></div>
      <div class="log-b">
        <div class="hint">ASUNTO</div><div>${esc(m.subject)}</div>
        <div class="hint" style="margin-top:10px">MENSAJE</div>
        <pre style="white-space:pre-wrap">${esc(m.body)}</pre></div></div>`).join('');

  $('mail-preview').innerHTML = `
    <div class="alert ${d.sendable ? 'info' : 'warn'}">
      <b>Le llega a ${d.sendable} de ${d.selected} marcados.</b>
      ${avisos.length ? ' Quedan afuera: ' + avisos.join(' y ') + '.' : ''}
      ${d.unknown_vars.length ? `<br><br><b>Ojo:</b> ${d.unknown_vars.map((v) =>
        '{{' + esc(v) + '}}').join(', ')} no existe como variable y va a salir vacío.` : ''}
    </div>${muestras}`;
}

async function scheduleMail() {
  const ids = [...SELECTED];
  if (!ids.length) return;

  const fd0 = new FormData();
  fd0.append('contact_ids', JSON.stringify(ids));
  fd0.append('subject', $('mail-subject').value);
  fd0.append('body', $('mail-body').value);
  const prev = await (await fetch('/api/mail/preview', { method: 'POST', body: fd0 })).json();
  const cfg = await (await fetch('/api/mail/config')).json();
  const limite = +$('mail-limit').value || prev.sendable;
  const cuantos = Math.min(prev.sendable, limite);

  if (!cuantos) { toast('Ninguno de los marcados puede recibir el correo', 'warn'); return; }

  const ok = await ask('Programar el envío',
    `<p>Se le va a escribir a <b>${cuantos} contacto(s)</b> desde
     <b>${esc(cfg.from_email || 'sin configurar')}</b>.</p>
     <p class="help">Son correos reales a empresas reales. Salen de a uno cada
     ~${Math.max(1, +$('mail-every').value || 3)} minutos y podés pausarlo en
     cualquier momento.</p>
     ${prev.unknown_vars.length ? `<div class="alert warn" style="margin-top:12px">
       ${prev.unknown_vars.map((v) => '{{' + esc(v) + '}}').join(', ')} no existe
       y va a salir vacío.</div>` : ''}`,
    [{ label: 'Cancelar', value: false },
     { label: `Enviar a ${cuantos}`, value: true, cls: 'go' }]);
  if (!ok) return;

  const fd = new FormData();
  fd.append('contact_ids', JSON.stringify(ids));
  fd.append('subject', $('mail-subject').value);
  fd.append('body', $('mail-body').value);
  fd.append('name', $('mail-subject').value.slice(0, 60));
  fd.append('limit', $('mail-limit').value || '');
  fd.append('every_seconds', String(Math.max(1, +$('mail-every').value || 3) * 60));
  fd.append('jitter_seconds', String(Math.max(0, +$('mail-jitter').value || 0) * 60));
  const cap = MAILBOXES.filter((m) => m.active && m.configured)
                       .reduce((a, m) => a + (m.daily_cap || 0), 0);
  fd.append('daily_cap', String(cap || 50));

  const r = await fetch('/api/mail/schedule', { method: 'POST', body: fd });
  const d = await r.json();
  if (!r.ok) { toast(esc(d.detail || 'Error'), 'err'); return; }
  if (!d.started) { toast(esc(d.message), 'info'); return; }
  toast(`${d.queued} correo(s) en cola`, 'ok');
  const caja = $('mail-sched-result');
  if (caja) {
    caja.innerHTML = `<div class="alert ok"><b>${d.queued} correo(s) programados.</b>
      Salen de a poco; podés cerrar la app y sigue. El avance se ve arriba.</div>`;
  }
  clearSelection();
  pollMail(true);
}

async function pollMail(seguir) {
  const d = await (await fetch('/api/mail/status')).json();
  const caja = $('mail-status');
  if (!caja) return;
  if (!d.campaign) { caja.innerHTML = ''; return; }
  // El envío en curso se ve al entrar al panel, sin abrir nada.

  const c = d.campaign;
  const total = d.pending + d.sent + d.error + d.cancelled;
  const pct = total ? Math.round(((d.sent + d.error) / total) * 100) : 0;
  const vivo = c.status === 'running' && d.pending;
  const ESTADO = { running: 'En curso', paused: 'En pausa',
                   done: 'Terminado', cancelled: 'Cancelado' };
  const ultimos = (d.last || []).map((x) => `
    <div class="srcline"><span>${esc(x.full_name || x.email)}
      ${x.status === 'error' ? `<span class="sub" title="${esc(x.error || '')}">
        — ${esc((x.error || '').slice(0, 40))}</span>` : ''}</span>
      <span class="pill ${x.status === 'sent' ? 'verified' : 'not_found'}">
        ${x.status === 'sent' ? 'enviado' : 'falló'}</span></div>`).join('');

  caja.innerHTML = `
    <div class="card" style="margin:0"><div class="body">
      <div class="statline" style="margin:0 0 6px">
        <b>${esc(ESTADO[c.status] || c.status)}</b>
        <span style="color:var(--ok)"><b>${d.sent}</b> enviados</span>
        <span class="sub"><b>${d.pending}</b> en cola</span>
        ${d.error ? `<span style="color:var(--danger)"><b>${d.error}</b> fallaron</span>` : ''}
      </div>
      <div class="bar ${vivo ? 'live' : ''}"><i style="width:${pct}%"></i></div>
      ${d.next_at && vivo ? `<p class="sub">Próximo alrededor de las
        ${esc(d.next_at.slice(11, 16))} UTC</p>` : ''}
      ${ultimos ? `<div style="margin-top:10px">${ultimos}</div>` : ''}
      <div class="rowend" style="margin-top:12px">
        ${c.status === 'running' ? `<button class="sm" onclick="mailControl(${c.id},'pause')">
          Pausar</button>` : ''}
        ${c.status === 'paused' ? `<button class="sm primary" onclick="mailControl(${c.id},'resume')">
          Reanudar</button>` : ''}
        ${d.pending ? `<button class="sm danger" onclick="mailControl(${c.id},'cancel')">
          Cancelar lo que falta</button>` : ''}
      </div>
    </div></div>`;

  if (MAILPOLL) { clearInterval(MAILPOLL); MAILPOLL = null; }
  if (vivo || seguir) MAILPOLL = setInterval(() => pollMail(), 15000);
}

async function mailControl(id, accion) {
  if (accion === 'cancel') {
    const ok = await ask('Cancelar el envío',
      '<p>Los que ya salieron no se pueden volver atrás. Se cancela lo que falta.</p>',
      [{ label: 'Seguir enviando', value: false },
       { label: 'Cancelar lo que falta', value: true, cls: 'danger' }]);
    if (!ok) return;
  }
  const fd = new FormData();
  fd.append('campaign_id', id);
  fd.append('action', accion);
  await fetch('/api/mail/control', { method: 'POST', body: fd });
  pollMail(true);
}

/* ======================= importar ======================= */
async function doPreview() {
  const f = $('csvfile').files[0];
  if (!f) return;
  $('prev-alert').innerHTML = '<div class="alert info"><span class="pulse">●</span> Leyendo el archivo…</div>';
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
    $('prev-box').innerHTML = `<div class="card"><div class="body">
      <div class="alert err"><b>Exportaste empresas, no personas.</b>
        Este archivo no trae nombres ni emails de contactos.
        En Apollo entrá a la pestaña <b>People</b> y exportá desde ahí.</div>
      <p class="help">Columnas encontradas: ${esc(d.headers.slice(0, 12).join(' · '))}…</p>
      </div></div>`;
    return;
  }

  const rows = d.preview.map((c) => `<tr>
    <td class="c-main"><b>${esc(c.full_name || '—')}</b>
      ${c.job_title ? `<div class="sub">${esc(c.job_title)}</div>` : ''}</td>
    <td data-label="Empresa">${esc(c.company_name || '—')}</td>
    <td data-label="Email">${esc(c.email || '—')}</td>
    <td data-label="Estado">${pill(c.email_status, STATUS_TXT)}</td></tr>`).join('');

  const conEmail = d.preview.filter((c) => c.email).length;
  const warn = d.unmapped_fields.filter((x) => ['first_name', 'company_domain'].includes(x));

  $('prev-box').innerHTML = `
    <div class="card list">
      <h2>Así se ve tu archivo <span class="hint">${d.total_rows} contactos</span></h2>
      <div class="body" style="padding-bottom:0">
        ${warn.length ? `<div class="alert warn">No se reconoció una columna
          importante: la búsqueda necesita el nombre de la persona y el sitio
          de la empresa.</div>` : ''}
        <p class="lede">De los primeros ${d.preview.length}, ${conEmail} ya traen email.</p>
      </div>
      <div class="tbl-scroll"><table class="rtable">
        <thead><tr><th>Persona</th><th>Empresa</th><th>Email</th><th>Estado</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
    </div>

    <div class="card"><div class="body">
      <div class="row">
        <div class="field">
          <label class="fld" for="icp-tag">Nombre de la carga (opcional)</label>
          <input id="icp-tag" placeholder="Ej: inmobiliarias Bogotá">
        </div>
        <button class="primary" onclick="doConfirm()">Guardar ${d.total_rows} contactos</button>
      </div>
      <p class="help">Los que ya tengas cargados no se duplican.</p>
      <div id="confirm-result" style="margin-top:14px"></div>
    </div></div>`;
}

async function doConfirm() {
  const fd = new FormData();
  fd.append('icp_tag', $('icp-tag').value.trim());
  const r = await fetch('/api/import/confirm', { method: 'POST', body: fd });
  const d = await r.json();
  if (!r.ok) {
    toast(esc(d.detail || 'Error'), 'err');
    return;
  }
  const dupTxt = d.duplicate_contacts
    ? ` Se descartaron ${d.duplicate_contacts} repetidos.` : '';
  $('confirm-result').innerHTML = `<div class="alert ok">
    <b>${d.new_contacts} contactos agregados.</b>${dupTxt}
    <div class="rowend"><button class="primary" onclick="goToStep('enrich')">
      Buscar sus emails →</button></div></div>`;
  toast(`${d.new_contacts} contactos agregados`, 'ok');
  $('csvfile').value = '';
  await loadMetrics(); await loadContacts(); await refreshPending();
}

/* ======================= buscar emails ======================= */
async function refreshPending() {
  const d = await (await fetch('/api/enrich/pending-count')).json();
  $('pendinfo').textContent = d.pending === 0
    ? 'No hay contactos pendientes.'
    : (d.pending === 1 ? 'Falta 1 contacto.' : `Faltan ${d.pending} contactos.`);
  $('btn-enrich').disabled = d.pending === 0;

  const box = $('retry-box');
  if (!box) return;

  if (d.not_found_new) {
    box.innerHTML = `<div class="alert info">
       <b>${d.not_found_new} contacto(s)</b> sin email que todavía no se reintentaron.
       <div class="rowend"><button class="sm" onclick="retryNotFound(false)">
         Reintentar esos ${d.not_found_new}</button></div></div>`;
  } else if (d.not_found_retried) {
    box.innerHTML = `<div class="alert mute">
       <b>${d.not_found_retried} contacto(s)</b> ya pasaron por los tres servicios
       más de una vez sin resultado.
       <div class="rowend"><button class="sm" onclick="retryNotFound(true)">
         Intentar igual</button>
       <span class="help">Consume créditos.</span></div></div>`;
  } else {
    box.innerHTML = '';
  }
}

async function retryNotFound(includeRetried) {
  const fd = new FormData();
  if (includeRetried) fd.append('include_retried', '1');
  const d = await (await fetch('/api/enrich/retry-not-found',
    { method: 'POST', body: fd })).json();
  toast(d.reset ? `${d.reset} listos para reintentar. Dale a "Empezar".`
                : 'No había contactos para reintentar.', d.reset ? 'ok' : 'info');
  await refreshPending();
  await loadMetrics();
}

async function startEnrich() {
  const fd = new FormData();
  fd.append('limit', $('e-limit').value || '');
  fd.append('batch_id', $('e-batch').value || '');
  const r = await fetch('/api/enrich/start', { method: 'POST', body: fd });
  const d = await r.json();
  if (!r.ok) { toast(esc(d.detail || 'Error'), 'err'); return; }
  if (!d.started) { toast(esc(d.message), 'info'); return; }
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
    const mins = Math.max(1, Math.round((p.total - p.processed) * 1.5 / 60));
    $('progress-box').innerHTML = `
      <div class="card" style="margin:0"><div class="body">
        <div class="bignum"><span class="pulse">●</span> ${p.processed} / ${p.total}</div>
        <div class="bar live"><i style="width:${pct}%"></i></div>
        <p class="sub">${p.current_contact
          ? `Consultando <b>${esc(SOURCE_TXT[p.current_provider] || p.current_provider || '…')}</b>
             para ${esc(p.current_contact)}`
          : 'Arrancando…'} · quedan ~${mins} min</p>
        <div class="statline">
          <span style="color:var(--ok)"><b>${p.found}</b> encontrados</span>
          <span class="sub"><b>${p.not_found}</b> sin resultado</span>
        </div>
        ${by ? `<div style="margin-top:11px;display:flex;gap:6px;flex-wrap:wrap">${by}</div>` : ''}
      </div></div>`;
  } else if (p.finished) {
    $('progress-box').innerHTML = `
      <div class="alert ${p.found ? 'ok' : 'info'}">
        <b>Búsqueda terminada.</b> ${p.found} email(s) nuevos de ${p.total} revisados.
        ${p.not_found ? ` En ${p.not_found} no hubo resultado.` : ''}
        ${by ? `<div style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap">${by}</div>` : ''}
        ${foundList(p.found_items, 'Emails encontrados')}
        <div class="rowend"><button class="primary" onclick="goToStep('contacts')">
          Ver y enviar al CRM →</button></div></div>`;
  }

  if (!p.running && p.finished) {
    clearInterval(POLL); POLL = null;
    toast(`${p.found} email(s) encontrados`, p.found ? 'ok' : 'info');
    await loadMetrics(); await loadContacts(); await refreshPending();
  }
}

/* ======================= detalle ======================= */
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
        ${l.error_message ? `<div class="alert err">${esc(l.error_message)}</div>` : ''}
        <details><summary class="help" style="cursor:pointer">Detalle técnico</summary>
          <div class="hint" style="margin-top:10px">LO QUE SE PREGUNTÓ</div>
          <pre>${esc(JSON.stringify(l.request_payload, null, 2))}</pre>
          <div class="hint" style="margin-top:10px">LO QUE RESPONDIÓ</div>
          <pre>${esc(JSON.stringify(l.response_payload, null, 2))}</pre>
        </details>
      </div></div>`).join('')
    : '<div class="empty">Todavía no se buscó el email de este contacto.</div>';

  $('modal-body').innerHTML = `
    <dl class="kv">
      <dt>Email</dt><dd>${c.email
        ? `<a href="mailto:${esc(c.email)}" style="color:var(--brand)">${esc(c.email)}</a>`
        : 'Sin email todavía'}</dd>
      <dt>Estado</dt><dd>${pill(c.email_status, STATUS_TXT)}</dd>
      <dt>Encontrado por</dt><dd>${pill(c.email_source, SOURCE_TXT)}</dd>
      <dt>Teléfono</dt><dd>${c.phone
        ? `<a href="tel:${esc(c.phone)}" style="color:var(--brand)">${esc(c.phone)}</a>`
          + (c.phone_type === 'company'
            ? ' <span class="sub">(conmutador de la empresa)</span>'
            : c.phone_type === 'personal' ? ' <span class="sub">(directo)</span>' : '')
        : '—'}</dd>
      <dt>Cargo</dt><dd>${esc(c.job_title || '—')}</dd>
      <dt>Empresa</dt><dd>${esc(c.company_name || '—')}</dd>
      <dt>Sitio web</dt><dd>${esc(c.company_domain || '—')}</dd>
      <dt>LinkedIn</dt><dd>${c.linkedin_url
        ? `<a href="${esc(c.linkedin_url)}" target="_blank" rel="noopener"
             style="color:var(--brand)">Ver perfil</a>` : '—'}</dd>
      ${c.address ? `<dt>Dirección</dt><dd>${esc(c.address)}</dd>` : ''}
      ${c.rating ? `<dt>Google</dt><dd>${c.rating} de 5
        <span class="sub">(${c.rating_count || 0} reseñas)</span>
        ${c.maps_url ? ` · <a href="${esc(c.maps_url)}" target="_blank" rel="noopener"
          style="color:var(--brand)">Ver en Maps</a>` : ''}</dd>` : ''}
      ${c.category ? `<dt>Rubro</dt><dd>${esc(c.category)}</dd>` : ''}
      ${c.social_url ? `<dt>Redes</dt><dd><a href="${esc(c.social_url)}" target="_blank"
        rel="noopener" style="color:var(--brand)">${esc(c.social_url.slice(0, 60))}</a></dd>` : ''}
      <dt>En el CRM</dt><dd>${pill(c.ghl_status, GHL_TXT)}</dd>
      ${c.ghl_error_message
        ? `<dt>Error del CRM</dt><dd style="color:var(--danger)">${esc(c.ghl_error_message)}</dd>`
        : ''}
    </dl>
    ${c.ai_profile ? renderPerfil(c) : ''}
    <h3 style="font-size:15px;font-weight:700;margin:20px 0 10px">Cómo se buscó su email</h3>
    ${logs}`;
  $('modal').classList.add('open');
}
function closeModal() { $('modal').classList.remove('open'); }
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if ($('ask').classList.contains('open')) askClose(null);
  else closeModal();
});

/* ======================= embudo ======================= */
let PIPELINES = [];

async function loadPipelines() {
  const box = $('pipeline-box');
  if (!box) return;
  box.innerHTML = '<p class="help">Cargando embudos del CRM…</p>';

  const [pipes, cfg] = await Promise.all([
    (await fetch('/api/ghl/pipelines')).json(),
    (await fetch('/api/ghl/settings')).json(),
  ]);

  if (pipes.error) {
    box.innerHTML = `<div class="alert warn">No se pudieron leer los embudos:
      ${esc(pipes.error)}</div>`;
    return;
  }
  PIPELINES = pipes.pipelines || [];
  if (!PIPELINES.length) {
    box.innerHTML = '<p class="help">No hay embudos en este sub-account.</p>';
    return;
  }

  const opts = PIPELINES.map((p) =>
    `<option value="${esc(p.id)}" ${p.id === cfg.pipeline_id ? 'selected' : ''}>
       ${esc(p.name)}</option>`).join('');

  box.innerHTML = `
    <details class="tip" ${cfg.pipeline_id ? '' : 'open'} style="margin:0">
      <summary>Embudo de oportunidades${cfg.pipeline_id ? '' : ' — sin configurar'}</summary>
      <div class="tip-b">
        <p style="margin-bottom:12px">Cada contacto que envíes abre además una
          oportunidad en el embudo que elijas.</p>
        <div class="row">
          <div class="field">
            <label class="fld" for="pipe-sel">Embudo</label>
            <select id="pipe-sel" onchange="renderStages()">
              <option value="">— No crear oportunidades —</option>${opts}
            </select>
          </div>
          <div class="field">
            <label class="fld" for="stage-sel">Etapa</label>
            <select id="stage-sel"></select>
          </div>
          <button onclick="savePipeline()">Guardar</button>
        </div>
      </div>
    </details>`;
  renderStages(cfg.stage_id);
}

function renderStages(selected) {
  const pid = $('pipe-sel').value;
  const pipe = PIPELINES.find((p) => p.id === pid);
  const sel = $('stage-sel');
  if (!pipe) {
    sel.innerHTML = '<option value="">—</option>';
    sel.disabled = true;
    return;
  }
  sel.disabled = false;
  sel.innerHTML = pipe.stages.map((st, i) =>
    `<option value="${esc(st.id)}" ${st.id === selected || (!selected && i === 0)
      ? 'selected' : ''}>${esc(st.name)}</option>`).join('');
}

async function savePipeline() {
  const fd = new FormData();
  fd.append('pipeline_id', $('pipe-sel').value);
  fd.append('stage_id', $('stage-sel').value || '');
  const d = await (await fetch('/api/ghl/settings', { method: 'POST', body: fd })).json();
  toast(d.pipeline_id
    ? (d.persisted_to_env ? 'Embudo guardado.'
                          : 'Guardado (se pierde al reiniciar el contenedor).')
    : 'Listo: no se crearán oportunidades.', 'ok');
}

/* ======================= borrar cargas ======================= */
async function deleteBatch(id, contactCount) {
  let wipe = false;

  if (contactCount > 0) {
    const opt = await ask(`Borrar la carga #${id}`,
      `<p>Tiene <b>${contactCount} contacto(s)</b>. ¿Qué querés borrar?</p>
       <p class="help">Los que ya enviaste al CRM nunca se borran.</p>`,
      [{ label: 'Cancelar', value: null },
       { label: 'Solo el historial', value: 'hist' },
       { label: 'También los contactos', value: 'all', cls: 'danger' }]);
    if (!opt) return;
    if (opt === 'all') {
      const sure = await ask('Confirmar',
        `<div class="alert err">Se van a borrar los contactos de la carga #${id}.
         Esto no se puede deshacer.</div>`,
        [{ label: 'Cancelar', value: false },
         { label: 'Borrar', value: true, cls: 'danger' }]);
      if (!sure) return;
      wipe = true;
    }
  } else {
    const sure = await ask(`Borrar la carga #${id}`,
      '<p>No tiene contactos asociados.</p>',
      [{ label: 'Cancelar', value: false },
       { label: 'Borrar', value: true, cls: 'danger' }]);
    if (!sure) return;
  }

  const fd = new FormData();
  if (wipe) fd.append('delete_contacts', '1');
  const r = await fetch(`/api/batches/${id}/delete`, { method: 'POST', body: fd });
  const d = await r.json();
  if (!r.ok) { toast(esc(d.detail || 'No se pudo borrar.'), 'err'); return; }

  let msg = `Carga #${d.batch_id} borrada.`;
  if (d.deleted_contacts) msg += ` ${d.deleted_contacts} contacto(s) borrados.`;
  if (d.kept_sent_to_crm) msg += ` ${d.kept_sent_to_crm} se conservaron (están en el CRM).`;
  toast(msg, 'ok', 6000);
  await loadMetrics(); await loadContacts(); await refreshPending();
}

/* ======================= arranque ======================= */
let QT = null;
$('f-q').addEventListener('input', () => {
  clearTimeout(QT);
  QT = setTimeout(loadContacts, 320);   // busca solo cuando dejás de tipear
});
FILTERS.forEach(([id]) => { $(id).addEventListener('change', loadContacts); });

for (const id of ['mail-subject', 'mail-body', 'mail-limit', 'mail-every',
                  'mail-jitter']) {
  const el = $(id);
  if (el) el.addEventListener('input', updateMailBtn);
}

(async () => {
  loadMe();
  pollMail();
  loadUsage();
  loadAiUsage();
  await loadMetrics();
  await loadContacts();
  await refreshPending();
  loadPipelines();   // en segundo plano: depende de una llamada al CRM
})();
