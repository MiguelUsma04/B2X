/* ============================================================
   Íconos de B2K.
   Pablo pidió íconos y no emojis: los emojis le quitan seriedad y cada
   sistema los dibuja distinto. Son íconos de línea al estilo Lucide
   (licencia ISC), dibujados acá mismo para no pedirlos a otro servidor.
   Se usan con icono('nombre') desde el JavaScript, o en el HTML con
   <svg class="ic"><use href="#i-nombre"/></svg>.
   ============================================================ */
const ICONOS = {
  subir: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M17 8l-5-5-5 5"/><path d="M12 3v12"/>',
  sobre: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-10 5L2 7"/>',
  rombo: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
  avion: '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
  grafico: '<path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/>',
  engranaje: '<path d="M20 7h-9"/><path d="M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
  mas: '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
  chequeo: '<path d="M20 6 9 17l-5-5"/>',
  cruz: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  basura: '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  exportar: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
  ojo: '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
  kommo: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  chispa: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9Z"/><path d="M5 3v4"/><path d="M3 5h4"/><path d="M19 17v4"/><path d="M17 19h4"/>',
  lapiz: '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
  paleta: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/><path d="M9 21V9"/>',
  reloj: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  buzon: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
  pulso: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  flechas: '<path d="m16 3 4 4-4 4"/><path d="M20 7H4"/><path d="m8 21-4-4 4-4"/><path d="M4 17h16"/>',
  lista: '<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/>',
  historial: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
  disco: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/>',
  libro: '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
  alerta: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  'flecha-izq': '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
  'flecha-der': '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
  telefono: '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.8 19.8 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z"/>',
  whatsapp: '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>',
  sitio: '<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
  ia: '<rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4"/><path d="M8 16h.01"/><path d="M16 16h.01"/>',
  buscar: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  salir: '<path d="M12 2v10"/><path d="M18.4 6.6a9 9 0 1 1-12.77.04"/>',
};

(function ponerIconos() {
  const ns = 'http://www.w3.org/2000/svg';
  const sprite = document.createElementNS(ns, 'svg');
  sprite.setAttribute('aria-hidden', 'true');
  sprite.style.display = 'none';
  sprite.innerHTML = Object.entries(ICONOS).map(([n, d]) =>
    `<symbol id="i-${n}" viewBox="0 0 24 24" fill="none" stroke="currentColor"
       stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</symbol>`).join('');
  document.body.prepend(sprite);
})();

function icono(nombre, clase = '') {
  return `<svg class="ic ${clase}" aria-hidden="true"><use href="#i-${nombre}"/></svg>`;
}

/* ⓘ: la explicación que no hace falta leer para usar la pantalla. Se abre
   al tocarlo y se cierra con Escape o tocando afuera. */
function info(textoHtml) {
  return `<button type="button" class="info-btn" aria-expanded="false"
    aria-label="Más información">${icono('info')}<span class="pop" role="tooltip">${textoHtml}</span></button>`;
}

/* ⋯: lo que se usa poco, y lo peligroso, fuera de la vista. */
function menuMas(items) {
  return `<span class="mas-wrap"><button type="button" class="mas-btn" aria-expanded="false"
      aria-haspopup="menu" aria-label="Más acciones">${icono('mas')}</button>
    <span class="mas-menu" role="menu">${items.map((it) => `
      <button type="button" role="menuitem" class="${it.peligro ? 'peligro' : ''}"
        onclick="${it.onclick}">${it.icono ? icono(it.icono) : ''}${it.label}</button>`).join('')}
    </span></span>`;
}

function cerrarDesplegables(salvo) {
  document.querySelectorAll('.info-btn[aria-expanded="true"], .mas-btn[aria-expanded="true"]')
    .forEach((b) => { if (b !== salvo) b.setAttribute('aria-expanded', 'false'); });
}

/* Un solo manejador para toda la página: sirve también para los ⓘ y ⋯ que
   se dibujan después, cuando la lista o la actividad se vuelven a pintar. */
document.addEventListener('click', (e) => {
  const boton = e.target.closest('.info-btn, .mas-btn');
  if (boton && !e.target.closest('.pop')) {
    e.preventDefault();
    e.stopPropagation();
    const abrir = boton.getAttribute('aria-expanded') !== 'true';
    cerrarDesplegables(boton);
    boton.setAttribute('aria-expanded', abrir ? 'true' : 'false');
    if (abrir) acomodarPop(boton);
    return;
  }
  if (e.target.closest('.mas-menu button')) { cerrarDesplegables(); return; }
  if (!e.target.closest('.pop, .mas-menu')) cerrarDesplegables();
}, true);

document.addEventListener('keydown', (e) => { if (e.key === 'Escape') cerrarDesplegables(); });

/* Que el globo no se salga de la pantalla en el teléfono. */
function acomodarPop(boton) {
  const pop = boton.querySelector('.pop') || boton.parentNode.querySelector('.mas-menu');
  if (!pop) return;
  pop.style.left = '';
  pop.style.right = '';
  const r = pop.getBoundingClientRect();
  if (r.right > innerWidth - 8) pop.style.left = `${Math.min(0, innerWidth - 8 - r.right)}px`;
  if (r.left < 8) pop.style.left = `${8 - boton.getBoundingClientRect().left}px`;
}
