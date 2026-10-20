// Speicheranalyse – Bedienleiste. Wird nur von app.py in Berichte eingefügt;
// als Datei geöffnete Berichte bleiben unverändert und funktionieren offline.
(function () {
  const TOKEN = new URL(document.currentScript.src).searchParams.get('t');
  const api = (path, opt = {}) => fetch(path, { ...opt, headers: { 'X-Token': TOKEN, 'Content-Type': 'application/json' } })
    .then(r => r.json());
  const el = (tag, props = {}, ...kids) => { const e = document.createElement(tag); Object.assign(e, props); e.append(...kids); return e; };
  const gb = b => (b / 2 ** 30).toLocaleString('de-DE', { maximumFractionDigits: b >= 10 * 2 ** 30 ? 0 : 1 }) + ' GB';
  const PHASES = [['scan', 'Scannen'], ['klassifizieren', 'Einordnen'], ['abgleich', 'Abgleich'], ['bericht', 'Bericht']];
  const current = (location.pathname.match(/bericht_([A-Z])_/) || [])[1];

  const css = el('style', { textContent: `
    .app-bar { display: flex; flex-wrap: wrap; gap: 10px 14px; align-items: center; background: var(--surface); border: 1px solid var(--ring);
      border-radius: 10px; padding: 10px 14px; font-size: 13px; color: var(--ink-2); }
    .app-bar select, .app-bar button { font: inherit; color: var(--ink); background: var(--bg); border: 1px solid var(--axis); border-radius: 6px; padding: 4px 8px; }
    .app-bar button { cursor: pointer; }
    .app-bar button.primary { background: var(--ink); color: var(--surface); border-color: var(--ink); }
    .app-bar button:disabled { opacity: .5; cursor: default; }
    .app-bar label { display: inline-flex; gap: 6px; align-items: center; cursor: pointer; }
    .app-bar .grow { flex: 1; }
    .app-run { background: var(--surface); border: 1px solid var(--ring); border-radius: 10px; padding: 14px 16px; display: grid; gap: 8px; font-size: 13px; }
    .app-steps { display: flex; flex-wrap: wrap; gap: 6px 18px; color: var(--muted); }
    .app-steps .now { color: var(--ink); font-weight: 600; }
    .app-steps .done { color: var(--ink-2); }
    .app-run .pathline { font-family: Consolas, ui-monospace, monospace; font-size: 12px; color: var(--ink-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .app-run .err { color: var(--ink); border-left: 3px solid var(--k-cache); padding-left: 10px; }
    .app-run pre { white-space: pre-wrap; font-size: 11px; max-height: 240px; overflow: auto; margin: 6px 0 0; }
    .app-bar button:focus-visible, .app-bar select:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }` });
  document.head.append(css);

  const driveSel = el('select', { title: 'Laufwerk' });
  const admin = el('input', { type: 'checkbox' });
  const startBtn = el('button', { className: 'primary', type: 'button', textContent: 'Neue Analyse' });
  const reportSel = el('select', { title: 'Frühere Berichte' });
  const quitBtn = el('button', { type: 'button', textContent: 'Beenden' });
  const bar = el('div', { className: 'app-bar' },
    el('span', { textContent: 'Laufwerk' }), driveSel,
    el('label', { title: 'Liest auch gesperrte Ordner und misst Wiederherstellungspunkte. Windows fragt per UAC nach.' }, admin, 'mit Admin-Rechten'),
    startBtn, el('span', { className: 'grow' }), el('span', { textContent: 'Bericht' }), reportSel, quitBtn);
  const run = el('div', { className: 'app-run', hidden: true });
  const wrap = document.querySelector('.wrap') || document.body;
  const header = wrap.querySelector('header');
  header ? header.after(bar, run) : wrap.prepend(bar, run);

  api('/api/laufwerke').then(ds => {
    ds.forEach(d => driveSel.append(el('option', {
      value: d.lw, selected: current ? d.lw[0] === current : d.system,
      textContent: `${d.lw} ${d.label || d.typ} · ${gb(d.total)}${d.system ? ' · System' : d.typ === 'Wechseldatenträger' ? ' · USB' : ''}`
    })));
  });
  api('/api/berichte').then(rs => {
    if (!rs.length) reportSel.append(el('option', { textContent: '–' }));
    rs.forEach(r => reportSel.append(el('option', {
      value: r.name, selected: location.pathname.endsWith(r.name),
      textContent: `${r.lw} · ${new Date(r.zeit).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })}`
    })));
  });
  reportSel.onchange = () => { location.href = `/bericht/${reportSel.value}?t=${encodeURIComponent(TOKEN)}`; };

  let timer = null, watching = false;  // watching: Lauf auf dieser Seite gestartet oder miterlebt
  function show(st) {
    const active = !['bereit', 'fertig', 'fehler'].includes(st.phase);
    startBtn.disabled = active;
    watching = watching || active;
    clearTimeout(timer);
    timer = setTimeout(poll, active ? 1000 : 60000);  // laufend: jede Sekunde, sonst Lebenszeichen
    if (st.phase === 'bereit' || !watching) { run.hidden = true; return; }  // alte Ergebnisse nicht erneut zeigen
    run.hidden = false;
    const idx = PHASES.findIndex(p => p[0] === st.phase);
    const steps = el('div', { className: 'app-steps' }, ...PHASES.map(([id, name], i) =>
      el('span', { className: i === idx ? 'now' : (idx > i || st.phase === 'fertig') ? 'done' : '', textContent: (idx > i || st.phase === 'fertig' ? '✓ ' : '') + name })));
    const head = el('div', { textContent: `Analyse von ${st.lw}${st.admin ? ' mit Admin-Rechten' : ''} · ${st.elapsed} s` });
    const kids = [head, steps];
    if (!st.phase || st.phase === 'start') kids.push(el('div', { textContent: st.admin_gewuenscht ? 'Warte auf die Bestätigung im Windows-Dialog …' : 'Startet …' }));
    if (st.fortschritt && st.fortschritt.files != null) {
      kids.push(el('div', { textContent: `${st.fortschritt.files.toLocaleString('de-DE')} Dateien in ${st.fortschritt.dirs.toLocaleString('de-DE')} Ordnern` }),
        el('div', { className: 'pathline', textContent: st.fortschritt.path || '' }));
    }
    if (st.phase === 'fehler') {
      kids.push(el('div', { className: 'err', textContent: st.fehler || 'Unbekannter Fehler.' }));
      if (st.protokoll) kids.push(el('details', {}, el('summary', { textContent: 'Protokoll' }), el('pre', { textContent: st.protokoll })));
    }
    if (st.phase === 'fertig') kids.push(el('div', { textContent: 'Fertig – neuer Bericht wird geöffnet …' }));
    run.replaceChildren(...kids);
    if (st.phase === 'fertig' && st.bericht) { clearTimeout(timer); location.href = `/bericht/${st.bericht}?t=${encodeURIComponent(TOKEN)}`; }
  }
  const poll = () => api('/api/status').then(show).catch(() => { run.hidden = false; run.replaceChildren(el('div', { className: 'err', textContent: 'Die App läuft nicht mehr. Über das Startmenü „Speicheranalyse“ neu starten.' })); startBtn.disabled = true; });

  startBtn.onclick = () => {
    startBtn.disabled = true;
    watching = true;
    api('/api/scan', { method: 'POST', body: JSON.stringify({ lw: driveSel.value, admin: admin.checked }) })
      .then(st => st.fehler && !st.phase ? show({ phase: 'fehler', fehler: st.fehler, lw: driveSel.value, elapsed: 0 }) : show(st))
      .catch(poll);
  };
  quitBtn.onclick = () => {
    api('/api/beenden', { method: 'POST' }).finally(() => {
      clearTimeout(timer);
      bar.replaceChildren(el('span', { textContent: 'App beendet. Der Bericht bleibt lesbar; neu starten über das Startmenü „Speicheranalyse“.' }));
      run.hidden = true;
    });
  };
  addEventListener('pagehide', () => navigator.sendBeacon(`/api/tschuess?t=${encodeURIComponent(TOKEN)}`));
  poll();
})();
