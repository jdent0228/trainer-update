'use strict';

// ── DOM helpers ───────────────────────────────────────────────
function el(tag, cls) { const e = document.createElement(tag); if (cls) e.className = cls; return e; }
function div(cls) { return el('div', cls); }
function span(cls, text) { const e = el('span', cls); if (text) e.textContent = text; return e; }
function txt(tag, cls, text) { const e = el(tag, cls); e.textContent = text; return e; }

// ── API ───────────────────────────────────────────────────────
async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch('/api' + path, opts);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

// ── Component builders ────────────────────────────────────────
// 우상단 상태 — 어느 탭에서나 같은 값. 진행 중인 것만 센다(성공·종료는 표시하지 않는다).
function makeStatusPill() {
  const pill = div('status-pill');
  const dot  = div('status-pill-dot');
  const lbl  = span('', '대기 중');
  pill.append(dot, lbl);
  function update(jobs) {
    const list = Array.isArray(jobs) ? jobs : [];
    let watch = 0, booked = 0;
    list.forEach(j => {
      if (!j.running) return;
      if (j.scheduled) booked++; else watch++;
    });
    const parts = [];
    if (watch) parts.push(`감시 ${watch}`);
    if (booked) parts.push(`예약 ${booked}`);
    lbl.textContent = parts.join(' ') || '대기 중';
    pill.className = 'status-pill' + (parts.length ? ' running' : '');
  }
  return { el: pill, update };
}

// 어느 탭에 있든 같은 값을 보여주려면 한 곳에서 주기적으로 받아 갱신해야 한다
let _pillJobs = [];
function refreshHeaderPill() {
  if (window.__DEMO__) return;
  api('GET', '/jobs').then(d => {
    _pillJobs = (d && d.jobs) || [];
    if (_headerPill) _headerPill.update(_pillJobs);
    const run = _pillJobs.some(j => j.running);
    const tabEl = document.getElementById('tab-results');
    if (tabEl) tabEl.classList.toggle('bot-running', run);
  }).catch(() => {});
}

function makeGridCard() { return div('card card-grid'); }

function _gridCell(labelText, content, fullWidth) {
  const cell = div(fullWidth ? 'grid-cell-full' : 'grid-cell');
  cell.appendChild(txt('div', 'grid-cell-label', labelText));
  const action = div('grid-cell-action');
  if (content instanceof Node) action.appendChild(content);
  else if (content) action.innerHTML = content;
  cell.appendChild(action);
  return cell;
}

function appendGridPair(card, left, right) {
  if (card.children.length > 0) card.appendChild(div('grid-divider-h'));
  card.appendChild(_gridCell(left.label, left.content, false));
  card.appendChild(div('grid-divider-v'));
  card.appendChild(right ? _gridCell(right.label, right.content, false) : div('grid-cell'));
}

function appendGridFull(card, labelText, content) {
  if (card.children.length > 0) card.appendChild(div('grid-divider-h'));
  card.appendChild(_gridCell(labelText, content, true));
}

// 라벨+입력 한 줄 아래에 붉은 에러 문구가 붙는 셀. 에러 div를 반환한다.
function appendGridFullErr(card, labelText, content, errEl) {
  if (card.children.length > 0) card.appendChild(div('grid-divider-h'));
  const cell = div('grid-cell-full grid-cell-err');
  const top = div('grid-cell-toprow');
  top.appendChild(txt('div', 'grid-cell-label', labelText));
  const action = div('grid-cell-action');
  if (content instanceof Node) action.appendChild(content);
  top.appendChild(action);
  errEl = errEl || txt('div', 'field-err', '');
  if (!errEl.style.display) errEl.style.display = 'none';
  cell.append(top, errEl);
  card.appendChild(cell);
  return errEl;
}

function makeFieldInput(value, suffix, width) {
  const wrap = div('');
  wrap.style.cssText = 'display:flex;align-items:center;gap:3px';
  const inp = el('input', 'field-input');
  inp.type = 'text'; inp.inputMode = 'numeric';
  inp.value = value || '';
  inp.autocomplete = 'off'; inp.spellcheck = false;
  if (width) inp.style.width = width + 'px';
  wrap.appendChild(inp);
  if (suffix) wrap.appendChild(span('field-suffix', suffix));
  return { wrap, inp };
}

function makeTextInput(value, width, placeholder) {
  const inp = el('input', 'field-input');
  inp.type = 'text'; inp.inputMode = 'text';
  inp.value = value || '';
  inp.autocomplete = 'off'; inp.spellcheck = false;
  if (placeholder) inp.placeholder = placeholder;
  if (width) inp.style.width = width + 'px';
  return inp;
}

function makeDateInput(value) {
  const inp = el('input', ''); inp.type = 'date'; inp.value = value || ''; return inp;
}
function makeTimeInput(value) {
  const inp = el('input', ''); inp.type = 'time'; inp.value = value || ''; return inp;
}
function makePrimaryButton(label, colorClass, onClick) {
  const btn = el('button', `btn-primary ${colorClass}`);
  btn.textContent = label;
  btn.addEventListener('click', onClick);
  return btn;
}

// 재생/정지 아이콘(미니멀, currentColor)
const _ICON_PLAY = '<svg class="btn-ico" width="15" height="15" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.6v12.8c0 .8.86 1.3 1.54.9l10.3-6.4a1.05 1.05 0 0 0 0-1.8L9.54 4.7A1.05 1.05 0 0 0 8 5.6z" fill="currentColor"/></svg>';
const _ICON_CLOCK = '<svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 7.5V12l3 2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const _ICON_GEAR = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>';
function _gearEl() { const g = span('acct-gear'); g.innerHTML = _ICON_GEAR; return g; }
const _ICON_USER = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>';
const _ICON_PAUSE = '<svg class="btn-ico" width="14" height="14" viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="5" width="4" height="14" rx="1.4" fill="currentColor"/><rect x="14" y="5" width="4" height="14" rx="1.4" fill="currentColor"/></svg>';
const _ICON_STOP = '<svg class="btn-ico" width="14" height="14" viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="3" fill="currentColor"/></svg>';
// 깔끔한 정보 아이콘(배경 없음, 외곽선)
const _ICON_INFO = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9.2"/><path d="M12 11v5"/><path d="M12 7.6h.01"/></svg>';

function makePwInput(value, placeholder, numeric, maxlen) {
  const inp = el('input', 'field-input');
  inp.type = 'password'; inp.value = value || '';
  inp.autocomplete = 'off'; inp.spellcheck = false;
  if (numeric) inp.inputMode = 'numeric';
  if (maxlen) inp.maxLength = maxlen;
  if (placeholder) inp.placeholder = placeholder;
  inp.style.width = '120px';
  return inp;
}
function makeSelect(options, value) {
  const sel = el('select', 'field-input');
  sel.style.cssText = 'text-align:right;border:none;background:transparent;color:var(--blue);font-size:13px;-webkit-appearance:none;cursor:pointer';
  options.forEach(([v, label]) => {
    const o = el('option'); o.value = v; o.textContent = label;
    if (v === value) o.selected = true;
    sel.appendChild(o);
  });
  return sel;
}
function makeCheckbox(checked) {
  const inp = el('input', '');
  inp.type = 'checkbox'; inp.checked = !!checked;
  inp.style.cssText = 'width:20px;height:20px;accent-color:var(--blue);margin:0';
  return inp;
}

// ── Log helpers ───────────────────────────────────────────────
const _TS_RE = /^(\d{4}-\d{2}-\d{2} )?(\d{2}:\d{2}:\d{2}),\d{3} \[(WARNING|ERROR|INFO)\] /;

function pickLogClass(line) {
  const m = line.match(_TS_RE);
  const rest = m ? line.slice(m[0].length) : line;
  const l = rest.toLowerCase();
  if (/error|실패|차단|로그인 실패/.test(l) || (m && m[3] === 'ERROR')) return 'err';
  if (/warning|warn|갱신 실패/.test(l)     || (m && m[3] === 'WARNING')) return 'warn';
  if (/예약 성공|로그인 성공|감시 시작/.test(rest)) return 'ok';
  if (/취소|중단됨|대기/.test(rest)) return 'dim';
  return '';
}

const _REP_RE = /^#(\d+) 후보 없음/;
const _CODE_RE_G = /\(((?:[A-Z]{1,4}\d{3,9})|S000)\)/g;
const _CODE_RE = /\(((?:[A-Z]{1,4}\d{3,9})|S000)\)/;
function _logAtBottom(el) { return el.scrollHeight - el.scrollTop - el.clientHeight < 40; }
function appendLog(logEl, rawLine) {
  if (_isHeartbeat(rawLine)) return;        // 조회 현황·로그인 진행은 화면에 남기지 않는다
  const m = rawLine.match(_TS_RE);
  const ts = m ? m[2] : '';
  const body = m ? rawLine.slice(m[0].length) : rawLine;
  const wasBottom = _logAtBottom(logEl);
  // 반복되는 '후보 없음' 줄은 마지막 줄에 ×N으로 합침(명절 장기 감시 시 도배 방지)
  // 후보 줄은 '시각' 다음 줄에 열차를 적고, 연달아 나오면 시각을 반복하지 않는다
  const cand = body.match(/^후보([^|]*)\|([^|]+)\|(.+)$/);
  if (cand) {
    const wasBottom0 = _logAtBottom(logEl);
    const last = logEl.lastElementChild;
    const sameTs = last && last.dataset.candTs === ts;
    if (!sameTs) {
      const head = div('log-line ok');
      if (ts) head.appendChild(span('log-ts', ts));
      head.appendChild(txt('span', 'log-body', '후보' + (cand[1] || '')));
      head.dataset.candTs = ts;
      logEl.appendChild(head);
    }
    const item = div('log-line ok log-cand');
    item.appendChild(span('log-ts', ''));
    item.appendChild(txt('span', 'log-body', `${cand[2]}  ${cand[3]}`));
    item.dataset.candTs = ts;
    logEl.appendChild(item);
    if (logEl.children.length > 400) logEl.removeChild(logEl.firstChild);
    if (wasBottom0) { logEl.scrollTop = logEl.scrollHeight; logEl._pending = 0; if (logEl._onPending) logEl._onPending(0); }
    return;
  }
  const rep = body.match(_REP_RE);
  const lastEl = logEl.lastElementChild;
  if (rep && lastEl && lastEl.dataset.rep) {
    const n = (+lastEl.dataset.repN || 1) + 1; lastEl.dataset.repN = String(n);
    lastEl.querySelector('.log-body').textContent = `후보 없음 ×${n} · ${lastEl.dataset.repFirst}~${ts}`;
  } else {
    const lineDiv = div('log-line ' + pickLogClass(rawLine));
    if (ts) lineDiv.appendChild(span('log-ts', ts));
    const bodyEl = span('log-body');
    if (_CODE_RE.test(body)) {   // (ERR299929) 같은 에러코드는 흐리게
      let i = 0;
      body.replace(_CODE_RE_G, (mm, _c, off) => { bodyEl.appendChild(document.createTextNode(body.slice(i, off))); bodyEl.appendChild(span('log-code', mm)); i = off + mm.length; return mm; });
      bodyEl.appendChild(document.createTextNode(body.slice(i)));
    } else bodyEl.textContent = body;
    lineDiv.appendChild(bodyEl);
    if (rep) { lineDiv.dataset.rep = '1'; lineDiv.dataset.repN = '1'; lineDiv.dataset.repFirst = ts; }
    logEl.appendChild(lineDiv);
    if (logEl.children.length > 400) logEl.removeChild(logEl.firstChild);
  }
  if (wasBottom) { logEl.scrollTop = logEl.scrollHeight; logEl._pending = 0; if (logEl._onPending) logEl._onPending(0); }
  else { logEl._pending = (logEl._pending || 0) + 1; if (logEl._onPending) logEl._onPending(logEl._pending); }
}

// ── Date / time conversion ────────────────────────────────────
function dateInputToYYMMDD(v) { return v ? v.replace(/-/g, '').slice(2) : ''; }
function timeInputToHHMM(v)   { return v ? v.replace(':', '') : ''; }

// ── Shared header + tab state ─────────────────────────────────
let _currentTab  = 'urgent';
let _headerTitle = null;
let _headerPill  = null;

const _TAB_TITLES = { book: '예매', results: '결과', tickets: '내 티켓', settings: '설정' };
const _TAB_PANEL = { book: 'book-panel', results: 'results-panel', tickets: 'tickets-panel', settings: 'settings-panel' };
const _TABS = ['book', 'results', 'tickets', 'settings'];
const _MODE_TABS = ['book'];   // 예매 패널은 하나(작업은 여러 개 등록)

function switchTab(name) {
  _currentTab = name;
  try { localStorage.setItem('kor_ktx_tab', name); } catch {}
  if (location.hash !== '#' + name) history.replaceState(null, '', '#' + name);
  _TABS.forEach(t => {
    const pan = document.getElementById(_TAB_PANEL[t]);
    if (pan) pan.classList.toggle('active', t === name);
    const tab = document.getElementById('tab-' + t);
    if (tab) tab.classList.toggle('active', t === name);
  });
  if (name === 'settings') _renderSettings();
  document.dispatchEvent(new Event('ktx-acct-sync'));   // 다른 탭에서 계정·N카드를 바꿨을 수 있다
  if (name === 'tickets' && typeof _loadTickets === 'function') _loadTickets(false);
  if (name === 'results' && typeof _loadResults === 'function') _loadResults();
  if (typeof renderUpdateHero === 'function' && _updLast) renderUpdateHero(_updLast);
  if (_headerTitle) _headerTitle.textContent = _TAB_TITLES[name];
  refreshHeaderPill();
}

// 아래로 당겨서 새로고침 — 스크롤 맨 위에서만 동작
// 화면이 옛 CSS/JS를 들고 있으면(서버 파일이 바뀌었으면) 한 번 새로고침한다.
// 앱을 켜 둔 채로 수정이 배포되면 사용자가 옛 화면을 계속 보게 되는 걸 막는다.
// 자동 업데이트 상태 — 윈도우 단독 실행에서만 켜진다. 준비되면 설정 탭에 알린다.
let _renderUpdateCard = null;
let _updNotified = '';
let _updLast = null;
async function _pollUpdate() {
  if (window.__DEMO__) return;
  const st = await api('GET', '/update').catch(() => null);
  if (!st || !st.ok) return;
  if (st.checking) setTimeout(_pollUpdate, 3000);   // 확인이 도는 중이면 끝나는 대로 반영
  if (_renderUpdateCard) _renderUpdateCard(st);
  _updLast = st;
  renderUpdateHero(st);
  const ready = st.enabled && st.ready && st.ready !== st.current;
  markUpdateAttention(!!ready || !!(st.enabled && st.needs_exe && st.latest && st.latest !== st.current));
  if (ready && _updNotified !== st.ready) {
    _updNotified = st.ready;
    toast(`새 버전 ${st.ready} 준비 완료 — 설정 탭에서 적용하세요`);
  }
}
function markUpdateAttention(on) { _setAttn('update', on); }

// 감시를 어디서 이어서 돌릴지 고른다. 켜져 있는 서버가 하나뿐이면 묻지 않는다.
function _chooseRunner(list, cur) {
  return new Promise(resolve => {
    const ov = div('picker-overlay'); const sheet = div('picker-sheet');
    sheet.appendChild(txt('div', 'wheel-title', '어디서 이어서 실행할까요?'));
    const close = v => { if (ov.parentNode) document.body.removeChild(ov); resolve(v); };
    list.forEach(r => {
      const it = div('picker-sheet-item' + (r.runner === cur ? ' selected' : ''));
      it.textContent = r.label + (r.here ? ' (이 기기)' : '');
      it.addEventListener('click', () => close(r.runner));
      sheet.appendChild(it);
    });
    ov.appendChild(sheet);
    ov.addEventListener('click', e => { if (e.target === ov) close(null); });
    document.body.appendChild(ov);
  });
}
async function _resumeJob(j, load) {
  let runners = [];
  try { const r = await api('GET', '/runners'); runners = (r && r.runners) || []; } catch {}
  const online = runners.filter(x => x.online);
  let pick = online.length ? online[0].runner : '';
  if (online.length > 1) {
    // 원래 돌던 서버가 켜져 있으면 그것을 기본으로 보여준다(사용자 지시)
    const def = online.some(x => x.runner === j.runner) ? j.runner : online[0].runner;
    pick = await _chooseRunner(online, def);
    if (pick === null) return;   // 취소
  }
  const r = await api('POST', `/jobs/run?mode=${encodeURIComponent(j.job)}&runner=${encodeURIComponent(pick || '')}`,
                      _jobBodies[j.job] || null).catch(() => null);
  if (!r || !r.ok) notify('재개 실패: ' + ((r && r.error) || '예매 탭에서 다시 등록하세요'));
  else if (r.queued) notify('해당 기기에 재개를 요청했습니다 — 곧 시작됩니다');
  load();
}

// 업데이트가 준비되면 각 탭 맨 위에 히어로 배너를 띄운다
const _ICON_UPD = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
  + ' stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">'
  + '<path d="M12 4v10"/><path d="M8 10l4 4 4-4"/><path d="M4 18h16"/></svg>';
let _updHeroHidden = '';
function renderUpdateHero(st) {
  document.querySelectorAll('.upd-hero').forEach(e => e.remove());
  if (!st || !st.enabled) return;
  const ready = st.ready && st.ready !== st.current;
  const needsExe = st.needs_exe && st.latest && st.latest !== st.current;
  if (!ready && !needsExe) return;
  const ver = ready ? st.ready : st.latest;
  if (_updHeroHidden === ver) return;            // 이번 버전은 닫아 뒀다

  const panel = document.querySelector('.panel.active');
  if (!panel) return;
  const hero = div('upd-hero');
  const ic = div('upd-hero-ic'); ic.innerHTML = _ICON_UPD;
  const tx = div('upd-hero-tx');
  tx.append(txt('div', 'upd-hero-t', `새 버전 ${ver} 준비 완료`),
            txt('div', 'upd-hero-s', needsExe ? '새 실행 파일을 내려받아야 합니다'
                                              : '버튼 한 번이면 최신 버전으로 바뀝니다'));
  hero.append(ic, tx);

  if (needsExe) {
    const a = document.createElement('a'); a.className = 'upd-hero-btn';
    a.href = 'https://github.com/jdent0228/trainer-releases/releases/latest';
    a.target = '_blank'; a.rel = 'noopener'; a.textContent = '받으러 가기';
    hero.appendChild(a);
  } else {
    const b = el('button', 'upd-hero-btn'); b.type = 'button'; b.textContent = '지금 업데이트';
    b.addEventListener('click', () => _applyUpdate(b));
    hero.appendChild(b);
  }
  const x = el('button', 'upd-hero-x'); x.type = 'button'; x.textContent = '×'; x.title = '이번 버전은 숨기기';
  x.addEventListener('click', () => { _updHeroHidden = ver; hero.remove(); });
  hero.appendChild(x);
  panel.insertBefore(hero, panel.firstChild);
}

// 설정 탭 버튼과 히어로 배너가 함께 쓰는 적용 절차
async function _applyUpdate(btn) {
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = '적용하는 중…';
  const r = await api('POST', '/update/apply').catch(() => null);
  if (!r || !r.ok) {
    btn.disabled = false; btn.textContent = label;
    notify((r && r.error) || '적용에 실패했습니다'); return;
  }
  if (r.manual) {
    btn.textContent = '창을 닫았다 여세요';
    notify('업데이트 적용 완료 — 프로그램을 닫았다 다시 실행해 주세요');
    return;
  }
  btn.textContent = '다시 시작하는 중…';
  for (let i = 0; i < 30; i++) {
    await new Promise(res => setTimeout(res, 1000));
    const v = await api('GET', '/version').catch(() => null);
    if (v && v.ok) { location.reload(); return; }
  }
  btn.textContent = '창을 닫았다 여세요';
}

function installVersionWatch() {
  if (window.__DEMO__ || !window.__ASSET_V) return;
  let checking = false;
  const check = () => {
    if (checking || document.hidden) return;
    checking = true;
    api('GET', '/version')
      .then(r => {
        if (!(r && r.ok && r.v && String(r.v) !== String(window.__ASSET_V))) return;
        // 감시를 막 시작한 직후에 통째로 새로고침하면 결과 카드가 10초 넘게 늦게 뜬다(2026-09-09 실측:
        // 터널에서 전체 재로딩). 30초만 미루면 다음 검사(60초)에서 자연스럽게 갱신된다.
        if (Date.now() - (window.__startedAt || 0) < 30000) return;
        location.reload();
      })
      .catch(() => {})
      .finally(() => { checking = false; });
  };
  document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); });
  setInterval(check, 60000);
  check();
}

function installPullToRefresh() {
  const bar = div('ptr'); bar.innerHTML = '<span class="ptr-spin"></span>';
  document.body.appendChild(bar);
  let startY = null, dy = 0, active = false;
  const THRESH = 68;
  const scroller = () => document.querySelector('.panel.active') || document.scrollingElement;
  const atTop = () => { const el0 = scroller(); return (el0 ? el0.scrollTop : 0) <= 0; };
  const NO_PTR = '.picker-overlay, .pop-menu, .station-dd, .hs-inner, .seat-hwrap, .terminal-log, .anchor-ask, .alerts-body';
  document.addEventListener('touchstart', e => {
    if (e.touches.length !== 1 || !atTop() || (e.target.closest && e.target.closest(NO_PTR))) { startY = null; return; }
    startY = e.touches[0].clientY; dy = 0; active = false;
  }, { passive: true });
  document.addEventListener('touchmove', e => {
    if (startY == null) return;
    dy = e.touches[0].clientY - startY;
    if (dy > 6 && atTop()) {
      active = true;
      const d = Math.min(dy * 0.5, 84);
      bar.style.transform = `translate(-50%, ${d}px)`;
      bar.classList.toggle('ready', dy > THRESH);
      bar.classList.add('on');
    }
  }, { passive: true });
  document.addEventListener('touchend', () => {
    if (active && dy > THRESH) { bar.classList.add('spin'); location.reload(); return; }
    bar.classList.remove('on', 'ready'); bar.style.transform = 'translate(-50%, 0)';
    startY = null; dy = 0; active = false;
  }, { passive: true });
}

// ── 디자인 토큰(관리자 페이지에서 조정) ───────────────────────
const UI_TOKENS = [
  { k: '--ui-cell-py', label: '행 세로 여백', min: 4, max: 22, step: 1, unit: 'px' },
  { k: '--ui-cell-px', label: '행 좌우 여백', min: 8, max: 26, step: 1, unit: 'px' },
  { k: '--ui-cell-min', label: '행 최소 높이', min: 32, max: 64, step: 1, unit: 'px' },
  { k: '--ui-gap', label: '요소 간격', min: 2, max: 18, step: 1, unit: 'px' },
  { k: '--ui-section-gap', label: '섹션 간격', min: 4, max: 30, step: 1, unit: 'px' },
  { k: '--ui-card-radius', label: '카드 모서리', min: 0, max: 26, step: 1, unit: 'px' },
  { k: '--ui-font-label', label: '항목 이름 크기', min: 11, max: 20, step: 0.5, unit: 'px' },
  { k: '--ui-font-value', label: '값 크기', min: 11, max: 20, step: 0.5, unit: 'px' },
  { k: '--ui-font-header', label: '섹션 제목 크기', min: 10, max: 18, step: 0.5, unit: 'px' },
  { k: '--ui-font-small', label: '보조 문구 크기', min: 9, max: 16, step: 0.5, unit: 'px' },
  { k: '--ui-accent', label: '포인트 색', type: 'color' },
  { k: '--ui-text', label: '본문 색', type: 'color' },
  { k: '--ui-muted', label: '흐린 글자 색', type: 'color' },
];
function uiThemeLoad() { try { return JSON.parse(localStorage.getItem('kor_ui_theme') || '{}') || {}; } catch { return {}; } }
function uiThemeSave(o) { try { localStorage.setItem('kor_ui_theme', JSON.stringify(o)); } catch {} if (typeof syncPush === 'function') syncPush(); }
function uiThemeApply(o) {
  const t = o || uiThemeLoad();
  UI_TOKENS.forEach(x => {
    const v = t[x.k];
    if (v == null || v === '') document.documentElement.style.removeProperty(x.k);
    else document.documentElement.style.setProperty(x.k, v);
  });
  if (t['--ui-accent']) document.documentElement.style.setProperty('--blue', t['--ui-accent']);
  else document.documentElement.style.removeProperty('--blue');
}
uiThemeApply();
// 스와이프 화살표 시안 적용(/arrows.html 에서 선택)
try { document.documentElement.dataset.arrow = localStorage.getItem('kor_ui_arrow') || '4'; } catch {}
// 관리자 페이지(iframe 부모)에서 값을 밀어 넣으면 즉시 반영
window.addEventListener('message', e => {
  const d = e.data;
  if (d && d.type === 'ui-theme') { uiThemeApply(d.theme); }
});

// ── Shared log stream ─────────────────────────────────────────
const _logEls = [];
// 감시별 로그 버퍼/DOM(결과 탭) — job 태그가 없는 줄은 시스템 로그로 알림에 모은다
const _jobLogBuf = {}, _jobLogEls = {}, _sysLog = [];
// 로그 한 줄의 시각(앞머리 'YYYY-MM-DD HH:MM:SS,mmm'). 없으면 0 — 항상 맨 앞으로.
function _logTime(line) {
  const m = String(line || '').match(/^(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}):(\d{2}),(\d{3})/);
  if (!m) return 0;
  return Date.parse(`${m[1]}T${m[2]}:${m[3]}:${m[4]}.${m[5]}`) || 0;
}
// 중복을 걷어내고 시간순으로. 시각이 같으면 원래 순서를 지킨다.
function _sortByTime(lines) {
  const seen = new Set(), out = [];
  (lines || []).forEach((l, i) => { if (seen.has(l)) return; seen.add(l); out.push([_logTime(l), i, l]); });
  out.sort((a, b) => (a[0] - b[0]) || (a[1] - b[1]));
  return out.map(x => x[2]);
}

// 조회 현황(카드에 이미 있는 '조회 N회 · 다음 N초')은 실시간 로그에서 뺀다
const _HEARTBEAT_RE = /(조회 \d+회 · 다음 \d+초|#\d+ 후보 없음 · 다음 \d+초)\s*$/;
// 화면에 남길 가치가 없는 줄 — 조회 현황과 로그인 진행 표시
const _NOISE_RE = /(조회 \d+회 · 다음 \d+초|#\d+ 후보 없음 · 다음 \d+초|로그인 중(\s|·|$))/;
function _isHeartbeat(line) { return _HEARTBEAT_RE.test(String(line || '')) || _NOISE_RE.test(String(line || '')); }

function _pushJobLog(job, line) {
  if (_isHeartbeat(line)) return;
  const buf = _jobLogBuf[job] || (_jobLogBuf[job] = []);
  buf.push(line); if (buf.length > 200) buf.shift();
  const el = _jobLogEls[job]; if (el) appendLog(el, line);
}
function _pushSysLog(line) { _sysLog.push(line); if (_sysLog.length > 100) _sysLog.shift(); }

// 구글 로그인/관리자 승인 등 인증 관련 로그는 실시간 로그에 노출하지 않는다
function _isAuthLog(line) {
  return /인증\s*모드|oauth|google|gmail|승인대기|\[승인|승인 후|세션|쿠키|approved|pending|cf-connecting/i.test(line || '');
}

function openSharedLogStream() {
  if (window.__DEMO__) {   // 데모: 서버 연결 없이 샘플 로그만 표시
    const demo = [
      '감시 시작 · 서울→부산 07/01 08:00~12:00 · 1명 · 30초 · 전체',
      '#1 후보 없음 · 다음 30초',
      '#2 후보 없음 · 다음 30초',
      '#3 후보 2개',
      '예약 성공 · 서울→부산 2026.07.01 (수) 08:24 / 101열차 / 일반실',
    ];
    setTimeout(() => _logEls.forEach(({ el }) => demo.forEach(l => appendLog(el, l))), 300);
    return;
  }
  let _es = null;
  let _retryTimer = null;

  function _connect() {
    if (_es) { try { _es.close(); } catch {} _es = null; }
    _es = new EventSource('/api/logs/stream');

    _es.onmessage = e => {
      let d; try { d = JSON.parse(e.data); } catch { return; }
      if (d.ping) return;
      if (_isAuthLog(d.line)) return;   // 구글/인증/승인 관련은 실시간 로그에서 숨김(예약 내용만)
      if (d.job) _pushJobLog(d.job, d.line);
      else if (/\[(WARNING|ERROR)\]/.test(d.line) && !/\[기록\//.test(d.line)) _pushSysLog(d.line);
      _logEls.forEach(({ el }) => appendLog(el, d.line));
    };

    _es.onerror = () => {
      _es.close(); _es = null;
      _logEls.forEach(({ el }) => appendLog(el, '⚠ 로그 재연결 중...'));
      clearTimeout(_retryTimer);
      _retryTimer = setTimeout(_connect, 3000);
    };
  }

  _connect();
}

// ── Panel shell (buttons → success → status → log) ───────────
// cfg: { apiStart, apiStop, apiStatus, tabId, successNote }
// cfgCard: already appended to panel; getStartBody(): returns request body or null on validation fail

// ── KTX panel ──────────────────────────────────────────
// cfg: { defaultDep, defaultArr, defaultInterval, apiStart, apiStop, apiStatus, tabId, successNote }

// ── Korail 역 목록 (자유 입력 허용, 큐레이션) ──────────────────
const STATIONS = [...new Set([
  '서울','용산','영등포','광명','수원','평택','평택지제','천안','천안아산','아산','온양온천','신창',
  '오송','조치원','대전','서대전','신탄진','계룡','논산','강경','연무대','옥천','영동','김천','김천구미',
  '구미','왜관','대구','동대구','서대구','경산','청도','밀양','삼랑진','구포','부산','부전','물금','화명',
  '수서','동탄','공주','익산','김제','정읍','장성','광주송정','광주','서광주','나주','함평','무안','목포',
  '전주','남원','곡성','구례구','순천','여천','여수엑스포','임실','오수','벌교','보성','득량','광양','진상',
  '진주','마산','창원','창원중앙','진영','함안','북천','완사','하동','횡천',
  '청량리','상봉','망우','양평','용문','지평','서원주','원주','만종','횡성','둔내','평창','진부','강릉',
  '정동진','묵호','동해','제천','단양','풍기','영주','안동','의성','영천','경주','신경주','포항','태화강',
  '울산','영월','민둥산','사북','고한','태백','철암','분천','춘양','봉화',
  '춘천','남춘천','김유정','강촌','가평','청평','대성리','백마고지','연천','전곡','동두천',
  '예산','홍성','광천','대천','웅천','서천','장항','군산','신성','개태사',
]) ];

const _CHO = ['ㄱ','ㄲ','ㄴ','ㄷ','ㄸ','ㄹ','ㅁ','ㅂ','ㅃ','ㅅ','ㅆ','ㅇ','ㅈ','ㅉ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ'];
function _chosung(str) {
  let out = '';
  for (const ch of str) {
    const c = ch.charCodeAt(0) - 0xAC00;
    out += (c >= 0 && c < 11172) ? _CHO[Math.floor(c / 588)] : ch;
  }
  return out;
}
function _stationScore(s, q) {
  // 0=no, 1=초성포함, 2=문자포함, 3=초성시작, 4=문자시작
  if (s.startsWith(q)) return 4;
  if (s.includes(q)) return 2;
  if (/^[ㄱ-ㅎ]+$/.test(q)) {
    const cs = _chosung(s);
    if (cs.startsWith(q)) return 3;
    if (cs.includes(q)) return 1;
  }
  return 0;
}
function _filterStations(q, stations) {
  stations = stations || STATIONS;
  q = (q || '').trim();
  if (!q) return [];
  return stations.map(s => [s, _stationScore(s, q)]).filter(x => x[1] > 0)
                 .sort((a, b) => b[1] - a[1]).map(x => x[0]).slice(0, 8);
}

// 자주 쓰는 역 — 아무것도 입력하지 않아도 바로 고를 수 있게
const _MAJOR_STATIONS = ['서울', '용산', '광명', '천안아산', '오송', '대전', '동대구', '부산',
                         '광주송정', '익산', '전주', '목포', '여수엑스포', '강릉', '포항', '창원중앙', '진주'];
const _ICON_STAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 3l2.7 5.5 6.1.9-4.4 4.3 1 6-5.4-2.8-5.4 2.8 1-6L3.2 9.4l6.1-.9z"/></svg>';
const _ICON_STAR_ON = '<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><path d="M12 3l2.7 5.5 6.1.9-4.4 4.3 1 6-5.4-2.8-5.4 2.8 1-6L3.2 9.4l6.1-.9z"/></svg>';
const _FAV_DEFAULT = ['용산', '광주송정', '서울', '수서'];
function _favLoad() {
  try {
    const raw = localStorage.getItem('kor_ktx_favstn');
    if (raw == null) return _FAV_DEFAULT.slice();   // 처음에는 자주 쓰는 역을 켜둔다
    return JSON.parse(raw) || [];
  } catch { return _FAV_DEFAULT.slice(); }
}
function _favSave(l) { try { localStorage.setItem('kor_ktx_favstn', JSON.stringify(l)); } catch {} if (typeof syncPush === 'function') syncPush(); }
function _favToggle(name) {
  const l = _favLoad();
  const i = l.indexOf(name);
  if (i >= 0) l.splice(i, 1); else l.unshift(name);
  _favSave(l.slice(0, 8)); return l.indexOf(name) < 0;
}

function makeStationField(value, placeholder, onChange, stations, opts) {
  const stationList = stations || STATIONS;
  // 반대편 역(출발↔도착)은 아예 고를 수 없게 목록에서 빼고, 값으로도 인정하지 않는다.
  // 서울→서울 같은 조합은 코레일에 물어볼 필요조차 없다(2026-09-10 사용자 지시).
  const _ex = () => String((opts && opts.exclude && opts.exclude()) || '').trim();
  const _list = () => { const ex = _ex(); return ex ? stationList.filter(x => x !== ex) : stationList; };
  const inp = el('input', 'field-input');
  inp.type = 'text'; inp.value = value || ''; inp.placeholder = placeholder || '';
  inp.autocomplete = 'off'; inp.spellcheck = false;
  const errEl = txt('div', 'field-err center', '입력값을 확인해주세요');
  errEl.style.display = 'none';
  const dd = div('shop-dropdown station-dd');
  document.body.appendChild(dd);
  function pos() {
    const box = inp.closest('.station-box') || inp;
    const r = box.getBoundingClientRect();
    dd.style.width = Math.round(r.width) + 'px';
    dd.style.minWidth = '0';
    placeAnchoredBox(dd, box, { maxH: 240, minShow: 110, gap: 4 });
  }
  const _repos = () => { if (dd.style.display === 'block') pos(); };
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', _repos);
    window.visualViewport.addEventListener('scroll', _repos);
  }
  function hide() { dd.style.display = 'none'; }
  let touched = false;
  let prev = '';        // 칸을 눌러 비우기 직전의 역(취소하면 되돌린다)
  let picked = false;   // 이번 편집에서 역을 골랐는지
  const isValid = () => _list().includes(inp.value.trim());
  function refresh() {
    const v = inp.value.trim();
    const same = v && v === _ex();
    errEl.textContent = same ? '출발역과 도착역이 같습니다' : '입력값을 확인해주세요';
    errEl.style.display = (touched && v && !_list().includes(v)) ? '' : 'none';   // 칸에서 벗어난 뒤에만 표시
    if (onChange) onChange();
  }
  function show(list, headItem) {
    dd.innerHTML = '';
    const favs = _favLoad().filter(x => _list().includes(x));
    const rest = list.filter(x => x !== headItem && favs.indexOf(x) < 0);
    const items = (headItem ? [headItem] : []).concat(favs.filter(x => x !== headItem), rest);
    if (!items.length) { hide(); return; }
    items.forEach((st, i) => {
      const isRecent = !!headItem && i === 0;
      const it = div('shop-dropdown-item' + (isRecent ? ' recent' : '')); it.textContent = st;
      const star = el('button', 'station-fav' + (favs.indexOf(st) >= 0 ? ' on' : '')); star.type = 'button';
      star.innerHTML = favs.indexOf(st) >= 0 ? _ICON_STAR_ON : _ICON_STAR;
      star.title = '자주 쓰는 역';
      const tog = e => { e.preventDefault(); e.stopPropagation(); _favToggle(st); show(list, headItem); };
      star.addEventListener('mousedown', tog);
      star.addEventListener('touchstart', tog, { passive: false });
      it.appendChild(star);
      const pick = e => { e.preventDefault(); picked = true; inp.value = st; hide(); refresh(); inp.blur(); };
      it.addEventListener('mousedown', pick);
      it.addEventListener('touchstart', pick, { passive: false });
      dd.appendChild(it);
    });
    pos(); dd.style.display = 'block';
  }
  // 누르면 일단 비우고 주요 역을 띄운다(맨 위는 방금 지운 역) — 아직 onChange는 부르지 않아 열차 목록이 유지된다
  const _allSorted = () => _list().slice().sort((a, b) => a.localeCompare(b, 'ko'));
  inp.addEventListener('focus', () => {
    prev = inp.value.trim(); picked = false;
    inp.value = '';
    // '최근'은 실제 역일 때만(오타·초성 입력은 제외)
    show(_allSorted(), _list().includes(prev) ? prev : null);
  });
  inp.addEventListener('input', () => {
    const v = inp.value.trim();
    show(v ? _filterStations(v, _list()) : _allSorted(),
         v ? null : (_list().includes(prev) ? prev : null));
    if (v) refresh();
  });
  inp.addEventListener('keydown', e => { if (e.key === 'Escape') { inp.value = prev; hide(); inp.blur(); } });
  inp.addEventListener('blur', () => {
    setTimeout(() => {
      hide();
      if (!picked && !inp.value.trim() && prev) { inp.value = prev; return; }   // 그냥 취소 → 원래 역 복원(변경 없음)
      touched = true; refresh();
    }, 180);
  });
  window.addEventListener('scroll', () => { if (dd.style.display === 'block') pos(); }, true);
  return { el: inp, errEl, isValid, refresh,
    markTouched: () => { touched = true; refresh(); },
    getValue: () => inp.value.trim(), setValue: v => { inp.value = v || ''; refresh(); } };
}

// 시:분 드롭다운 (24시간, 00:00~23:59)
// 시간을 한 번에 고르는 단일 드롭다운 (HH:MM, 10분 단위)
function makeTimePicker(value) {
  const STEP = 10;
  const opts = [];
  for (let h = 0; h < 24; h++) for (let m = 0; m < 60; m += STEP) {
    const v = String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0');
    opts.push([v, v]);
  }
  let v0 = value || '00:00';
  if (!opts.some(o => o[0] === v0)) {
    const [h, m] = v0.split(':').map(Number);
    v0 = String(h || 0).padStart(2, '0') + ':' + String(Math.floor((m || 0) / STEP) * STEP).padStart(2, '0');
    if (!opts.some(o => o[0] === v0)) v0 = '00:00';
  }
  return makeSheetPicker(opts, v0);
}

// iOS풍 휠(스크롤 스냅) — 가운데 항목이 선택
function makeWheel(options, value, onChange) {
  const IH = 40;
  const wrap = div('wheel'); const list = div('wheel-list');
  options.forEach(([v, l]) => { const it = div('wheel-item'); it.textContent = l; list.appendChild(it); });
  const band = div('wheel-band');
  wrap.append(list, band);
  let cur = value;
  const idxOf = v => Math.max(0, options.findIndex(o => String(o[0]) === String(v)));
  const mark = i => [...list.children].forEach((c, j) => c.classList.toggle('on', j === i));
  let t;
  wrap.addEventListener('scroll', () => {
    clearTimeout(t);
    t = setTimeout(() => {
      const i = Math.max(0, Math.min(options.length - 1, Math.round(wrap.scrollTop / IH)));
      const nv = options[i][0]; const changed = nv !== cur;
      cur = nv; mark(i);
      if (changed && onChange) onChange(cur);
    }, 70);
  });
  requestAnimationFrame(() => { const i = idxOf(value); wrap.scrollTop = i * IH; mark(i); });
  // 프로그램적으로 특정 값으로 이동(스무스)
  function scrollTo(v) { const i = idxOf(v); cur = options[i][0]; mark(i); wrap.scrollTo({ top: i * IH, behavior: 'smooth' }); }
  return { el: wrap, getValue: () => cur, scrollTo };
}

// ── 날짜·시각 휠 선택 ──────────────────────────────────────────
// 예약 시작 일시처럼 '년/월/일 + 요일'과 '오전·오후/시/분'을 각각 휠로 고르는 필드.
// 값은 <input type=date/time>과 같은 문자열('YYYY-MM-DD' / 'HH:MM')이라 호출부를 그대로 둔다.
function _wheelSheet(title, cols, onDone) {
  const ov = div('picker-overlay'); const sheet = div('picker-sheet wheel-sheet');
  sheet.appendChild(txt('div', 'wheel-title', title));
  const row = div('wheel-cols');
  cols.forEach(c => { const col = div('wheel-col'); col.append(txt('div', 'wheel-col-label', c.label), c.wheel.el); row.appendChild(col); });
  sheet.appendChild(row);
  const bar = div('picker-sheet-cancel');
  const cancel = div('picker-sheet-cancel-btn'); cancel.textContent = '취소';
  const ok = div('picker-sheet-cancel-btn'); ok.textContent = '확인'; ok.style.color = 'var(--blue)';
  const close = () => { if (ov.parentNode) document.body.removeChild(ov); };
  cancel.addEventListener('click', close);
  ok.addEventListener('click', () => { onDone(); close(); });
  bar.append(cancel, ok);
  ov.append(sheet, bar);
  ov.addEventListener('click', e => { if (e.target === ov) close(); });
  document.body.appendChild(ov);
}

function makeWheelDateField(value, onChange) {
  let v = value || _todayStr();
  let min = '';
  const lab = span(''); lab.classList.add('u-picker-lab');
  const caret = span(''); caret.textContent = '▾'; caret.classList.add('u-picker-sub');
  const wrap = div('picker-value'); wrap.append(lab, caret);
  const fmt = d => { const t = _parseLocal(d + 'T00:00'); return t ? `${t.getFullYear()}. ${t.getMonth() + 1}. ${t.getDate()} (${_WD[t.getDay()]})` : d; };
  const render = () => { lab.textContent = fmt(v); };
  render();
  wrap.addEventListener('click', () => {
    const now = new Date();
    let [Y, M, D] = v.split('-').map(Number);
    const yOpts = Array.from({ length: 3 }, (_, i) => [now.getFullYear() + i, (now.getFullYear() + i) + '년']);
    const mOpts = Array.from({ length: 12 }, (_, i) => [i + 1, (i + 1) + '월']);
    const dayOpts = () => {
      const last = new Date(Y, M, 0).getDate();
      return Array.from({ length: last }, (_, i) => [i + 1, `${i + 1}일 (${_WD[new Date(Y, M - 1, i + 1).getDay()]})`]);
    };
    let dW;
    const dCol = div('wheel-col');   // 일 휠은 년·월이 바뀌면 통째로 갈아 끼운다
    const remakeDay = () => {
      const opts = dayOpts(); D = Math.min(D, opts.length);
      const nw = makeWheel(opts, D, x => { D = x; });
      if (dW) dCol.replaceChild(nw.el, dW.el);
      dW = nw;
    };
    const yW = makeWheel(yOpts, Y, x => { Y = x; remakeDay(); });
    const mW = makeWheel(mOpts, M, x => { M = x; remakeDay(); });
    remakeDay();
    dCol.append(txt('div', 'wheel-col-label', '일'), dW.el);
    const ov = div('picker-overlay'); const sheet = div('picker-sheet wheel-sheet');
    sheet.appendChild(txt('div', 'wheel-title', '날짜 선택'));
    const row = div('wheel-cols');
    const mk = (l, w) => { const c = div('wheel-col'); c.append(txt('div', 'wheel-col-label', l), w.el); return c; };
    row.append(mk('년', yW), mk('월', mW), dCol);
    sheet.appendChild(row);
    const bar = div('picker-sheet-cancel');
    const cancel = div('picker-sheet-cancel-btn'); cancel.textContent = '취소';
    const ok = div('picker-sheet-cancel-btn'); ok.textContent = '확인'; ok.style.color = 'var(--blue)';
    const close = () => { if (ov.parentNode) document.body.removeChild(ov); };
    cancel.addEventListener('click', close);
    ok.addEventListener('click', () => {
      const nv = `${Y}-${String(M).padStart(2, '0')}-${String(D).padStart(2, '0')}`;
      if (min && nv < min) { notify('오늘 이전 날짜는 고를 수 없습니다', ok); return; }
      v = nv; render(); close(); if (onChange) onChange(v);
    });
    bar.append(cancel, ok);
    ov.append(sheet, bar);
    ov.addEventListener('click', e => { if (e.target === ov) close(); });
    document.body.appendChild(ov);
  });
  return { el: wrap, get value() { return v; }, set value(x) { v = x; render(); },
           set min(x) { min = x; }, get min() { return min; } };
}

function makeWheelTimeField(value, onChange) {
  let v = value || '07:00';
  const lab = span(''); lab.classList.add('u-picker-lab');
  const caret = span(''); caret.textContent = '▾'; caret.classList.add('u-picker-sub');
  const wrap = div('picker-value'); wrap.append(lab, caret);
  const render = () => {
    const [H, Mi] = v.split(':').map(Number);
    lab.textContent = `${H < 12 ? '오전' : '오후'} ${String(H % 12 || 12)}:${String(Mi).padStart(2, '0')}`;
  };
  render();
  wrap.addEventListener('click', () => {
    let [H, Mi] = v.split(':').map(Number);
    let ap = H < 12 ? 0 : 1, h12 = H % 12 || 12;
    const apW = makeWheel([[0, '오전'], [1, '오후']], ap, x => { ap = x; });
    const hW = makeWheel(Array.from({ length: 12 }, (_, i) => [i + 1, String(i + 1) + '시']), h12, x => { h12 = x; });
    const mW = makeWheel(Array.from({ length: 60 }, (_, i) => [i, String(i).padStart(2, '0') + '분']), Mi, x => { Mi = x; });
    _wheelSheet('시각 선택', [{ label: '오전 / 오후', wheel: apW }, { label: '시', wheel: hW }, { label: '분', wheel: mW }],
      () => { const H24 = (h12 % 12) + ap * 12; v = `${String(H24).padStart(2, '0')}:${String(Mi).padStart(2, '0')}`; render(); if (onChange) onChange(v); });
  });
  return { el: wrap, get value() { return v; }, set value(x) { v = x; render(); } };
}

function _timeOpts() {
  const o = [];
  for (let h = 0; h < 24; h++) for (let m = 0; m < 60; m += 10) {
    const v = String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0');
    o.push([v, v]);
  }
  return o;
}

// 시작~종료 시간을 한 모달의 두 휠로 동시 조정
// ── 열차 목록 로컬 캐시(localStorage): (역|역|날짜)별 하루 전체. 규칙:
//    · 날짜(또는 역)를 고르면 그 날짜 하루를 병렬로 1회 조회해 저장
//    · 마지막 갱신이 24시간 이내면 실제 호출 없이 캐시 그대로 사용
//    · 오늘 이전 날짜 항목만 폐기
const _TC_KEY = 'kor_ktx_traincache2', _TC_TTL = 24 * 3600 * 1000;   // v2: 옛 형식(시각 어긋난) 캐시 폐기
function _tcLoad() { try { const o = JSON.parse(localStorage.getItem(_TC_KEY) || '{}') || {}; const today = _todayStr().replace(/-/g, ''); let ch = false; Object.keys(o).forEach(k => { const d = k.split('|')[2] || ''; if (d < today) { delete o[k]; ch = true; } }); if (ch) localStorage.setItem(_TC_KEY, JSON.stringify(o)); return o; } catch { return {}; } }
function _tcFresh(key) {
  const e = _tcLoad()[key];
  if (!e || !e.updatedAt || !e.trains || typeof e.trains !== 'object') return false;   // 손상·옛 형식이면 새로 받는다
  // 예매 열린 날은 좌석 매진/가능 상태가 실시간으로 바뀌므로 60초만 캐시(그 안에서는 시간대 변경=화면 필터).
  // 예매 전(preview) 날은 좌석 상태가 '예매 전'으로 고정이라 오래 캐시해도 된다. 시각표(times)는 어차피 안 바뀐다.
  const ttl = e.preview ? 3600000 : 60000;
  return Date.now() - e.updatedAt < ttl;
}
// 시각이 없거나 소요시간이 말이 안 되면 캐시가 깨진 것으로 보고 버린다
function _tcValid(tr) {
  if (!tr || !/^\d{4,6}$/.test(String(tr.dep_time || '')) || !/^\d{4,6}$/.test(String(tr.arr_time || ''))) return false;
  const d = _durMin(tr.dep_time, tr.arr_time);
  return d >= 8 && d <= 720;
}
function _tcList(key, a, b) {
  const e = _tcLoad()[key];
  if (!e || !e.trains || typeof e.trains !== 'object') return null;   // 옛 형식·손상 캐시는 없는 것으로 본다
  try {
    return Object.values(e.trains).filter(_tcValid)
      .filter(tr => { const t = tr.dep_time.slice(0, 2) + ':' + tr.dep_time.slice(2, 4); return t >= a && t <= b; })
      .sort((x, y) => x.dep_time < y.dep_time ? -1 : 1);
  } catch { return null; }
}
function _tcFrom(key, a, limit) {
  const e = _tcLoad()[key];
  if (!e || !e.trains || typeof e.trains !== 'object') return null;
  try {
    return Object.values(e.trains).filter(_tcValid)
      .filter(tr => (tr.dep_time.slice(0, 2) + ':' + tr.dep_time.slice(2, 4)) >= a)
      .sort((x, y) => x.dep_time < y.dep_time ? -1 : 1)
      .slice(0, limit || 10);
  } catch { return null; }
}
function _tcFind(key, no) {
  const e = _tcLoad()[key];
  if (!e || !e.trains || typeof e.trains !== 'object') return null;
  return e.trains[no] || null;
}
function _tcMergeWindow(key, trains) {
  try {
    const o = _tcLoad(); const e = o[key] || { trains: {}, updatedAt: 0, preview: false };
    (trains || []).forEach(tr => { e.trains[tr.no] = tr; });   // 창 안 열차만 최신값으로 덮어씀
    e.updatedAt = Date.now(); o[key] = e; localStorage.setItem(_TC_KEY, JSON.stringify(o));
  } catch {}
}
function _tcSetDay(key, trains, preview) { try { const o = _tcLoad(); const e = { trains: {}, updatedAt: Date.now(), preview: !!preview }; (trains || []).forEach(tr => { e.trains[tr.no] = tr; }); o[key] = e; localStorage.setItem(_TC_KEY, JSON.stringify(o)); } catch {} }

// ensureTrains(key): 캐시가 24시간 이내면 즉시 resolve, 아니면 하루 전체 조회 후 resolve. getKey(): '역|역|yyyymmdd'.
function makeTimeRangePicker(startVal, endVal, onChange, ensureTrains, getMinTime, getKey, selApi) {
  let s = startVal, e = endVal;
  const lab = span(''); lab.classList.add('u-picker-lab');
  const cnt = span(''); cnt.classList.add('u-picker-cnt');
  const caret = span(''); caret.textContent = '▾'; caret.classList.add('u-picker-sub');
  const wrap = div('picker-value'); wrap.append(lab, cnt, caret);
  const render = () => { lab.textContent = s + ' ~ ' + e; const L = getKey ? _tcList(getKey(), s, e) : null; cnt.textContent = L ? `${L.length}편` : ''; };
  render();
  wrap.addEventListener('click', () => {
    const minT = getMinTime ? getMinTime() : null;
    let opts = _timeOpts();
    if (minT) { opts = opts.filter(o => o[0] >= minT); if (!opts.length) opts = [[minT, minT]]; if (s < minT) s = opts[0][0]; if (e < s) e = opts[Math.min(opts.length - 1, opts.findIndex(o => o[0] === s) + 1)][0] || s; }
    const ov = div('picker-overlay'); const sheet = div('picker-sheet wheel-sheet');
    sheet.appendChild(txt('div', 'wheel-title', '출발시간 범위'));
    const eW = makeWheel(opts, e, () => onRange());
    // 시작이 종료보다 같거나 늦으면 종료를 시작 +1단계(10분)로 자동 조정
    const sW = makeWheel(opts, s, sv => {
      const si = opts.findIndex(o => o[0] === sv);
      const ei = opts.findIndex(o => o[0] === eW.getValue());
      if (si >= 0 && ei <= si) eW.scrollTo(opts[Math.min(opts.length - 1, si + 1)][0]);
      onRange();
    });
    const sCol = div('wheel-col'); sCol.append(txt('div', 'wheel-col-label', '시작'), sW.el);
    const eCol = div('wheel-col'); eCol.append(txt('div', 'wheel-col-label', '종료'), eW.el);
    const tld = div('wheel-tilde'); tld.textContent = '~';
    const cols = div('wheel-cols'); cols.append(sCol, tld, eCol);
    sheet.appendChild(cols);
    // 조회 대상 열차 — 캐시(24h)에서 필터. 범위를 바꿀 때마다 목록을 잠깐 흐리게(로딩 느낌) 한 뒤 다시 그림.
    const box = div('train-list'); const head = div('train-list-head');
    const headN = txt('span', 'train-list-n', ''); head.append(txt('span', '', '조회 대상 열차'), headN);
    const list = div('train-list-body'); const ovl = div('train-list-ov'); ovl.appendChild(div('spinner'));
    box.append(head, list, ovl); sheet.appendChild(box);
    const key = getKey ? getKey() : '';
    let dimT = null, dimSince = 0;
    function setDim(on) { box.classList.toggle('loading', on); }
    function renderRows() {
      const a = sW.getValue(), b = eW.getValue();
      list.innerHTML = '';
      const rows = _tcList(key, a, b);
      if (!rows) { headN.textContent = ''; list.appendChild(txt('div', 'train-list-msg', '')); return; }
      headN.textContent = `${rows.length}편`;
      if (!rows.length) { list.appendChild(txt('div', 'train-list-msg', '이 시간대에 출발하는 열차가 없습니다')); return; }
      const minDur = Math.min(...rows.map(t => _durMin(t.dep_time, t.arr_time) || 9999));
      if (selApi && selApi.ensureDefault) selApi.ensureDefault(rows, sW.getValue(), eW.getValue());
      rows.forEach(tr => {
        const on = !!(selApi && selApi.has(tr.no));
        const dm = _durMin(tr.dep_time, tr.arr_time), slow = minDur > 0 && dm > minDur * _SLOW_RATIO;
        const r = div('train-row' + (selApi ? ' pick' : '') + (on ? ' on' : ''));
        if (selApi) r.appendChild(span('train-cb' + (on ? ' on' : '')));
        r.append(txt('span', 'train-time', `${tr.dep_time.slice(0, 2)}:${tr.dep_time.slice(2, 4)}→${tr.arr_time.slice(0, 2)}:${tr.arr_time.slice(2, 4)}`),
                 txt('span', 'train-dur' + (slow ? ' slow' : ''), _durTxt(dm)),
                 txt('span', 'train-name', _trainLabel(tr)),
                 txt('span', 'train-seat ' + tr.seat, _seatBadge(tr.seat)));
        if (selApi) r.addEventListener('click', () => { selApi.toggle(tr); renderRows(); });
        list.appendChild(r);
      });
    }
    // 짧게라도 무조건 로딩 표시: 최소 260ms 흐리게 → 다시 그림
    function pulse(work) { clearTimeout(dimT); setDim(true); dimSince = Date.now(); Promise.resolve(work ? work() : null).then(() => { const left = Math.max(0, 260 - (Date.now() - dimSince)); dimT = setTimeout(() => { renderRows(); setDim(false); }, left); }); }
    function onRange() { pulse(); }
    renderRows();
    pulse(() => ensureTrains ? ensureTrains(key).then(r => { if (r && !r.ok) { list.innerHTML = ''; list.appendChild(txt('div', 'train-list-msg', r.error || '조회 실패')); headN.textContent = ''; } }).catch(() => {}) : null);
    const bar = div('picker-sheet-cancel'); const cb = div('picker-sheet-cancel-btn'); cb.textContent = '확인';
    cb.style.color = 'var(--blue)';
    cb.addEventListener('click', () => { s = sW.getValue(); e = eW.getValue(); render(); document.body.removeChild(ov); if (onChange) onChange(); });
    bar.appendChild(cb);
    ov.append(sheet, bar);
    ov.addEventListener('click', ev => { if (ev.target === ov) document.body.removeChild(ov); });
    document.body.appendChild(ov);
  });
  return { el: wrap, getStart: () => s, getEnd: () => e, setRange: (a, b) => { if (a) s = a; if (b) e = b; render(); }, refreshLabel: render };
}

// ⓘ 정보 아이콘 + 마우스오버 툴팁
function makeInfoIcon(text) {
  const wrap = div('info-icon'); wrap.innerHTML = _ICON_INFO;
  const tip = div('info-tip'); tip.textContent = text;
  document.body.appendChild(tip);   // 카드 overflow에 안 잘리게 body로
  function show() {
    const r = wrap.getBoundingClientRect();
    tip.style.display = 'block';
    const w = tip.offsetWidth || 230;
    tip.style.left = Math.max(10, Math.min(r.left + r.width / 2 - w / 2, window.innerWidth - w - 10)) + 'px';
    tip.style.top = (r.bottom + 8) + 'px';
  }
  function hide() { tip.style.display = 'none'; }
  wrap.addEventListener('mouseenter', show);
  wrap.addEventListener('mouseleave', hide);
  wrap.addEventListener('click', e => { e.stopPropagation(); show(); setTimeout(hide, 2600); });
  return wrap;
}

// 로딩·빈 상태·보조 버튼 — 여러 화면에서 같은 모양을 쓰므로 한 곳에서 만든다
function loadingBox(msg, host) {
  const ld = div('train-list-loading');
  ld.append(div('spinner'), txt('span', '', msg || '불러오는 중…'));
  if (host) { host.innerHTML = ''; host.appendChild(ld); }
  return ld;
}
function emptyBox(msg, host) {
  const e = div('card tk-empty'); e.textContent = msg;
  if (host) host.appendChild(e);
  return e;
}
function ghostBtn(label, onClick) {
  const b = el('button', 'btn-primary btn-ghost'); b.type = 'button'; b.textContent = label;
  if (onClick) b.addEventListener('click', onClick);
  return b;
}
// '섹션 제목 + 카드' 한 벌
function section(panel, title) {
  panel.appendChild(txt('div', 'group-header', title));
  const card = makeGridCard();
  panel.appendChild(card);
  return card;
}

// 호차 칩(예매 탭·후보 편집기 공용) — c: {car_no, cls, rest?, total?}
function makeCarChip(c, on, onClick, mirrorNo) {
  const spec = c.cls === '2';
  const rest = (c.rest != null) ? c.rest : c.total;
  const n = parseInt(c.car_no, 10);
  const ch = el('button', 'car-chip' + (on ? ' on' : '') + ((c.rest != null && c.rest <= 0) ? ' full' : '') + (spec ? ' spec' : ''));
  ch.type = 'button'; ch.dataset.no = String(n);
  ch.appendChild(txt('span', 'car-chip-no', `${n}호차`));
  // 중련 편성이면 같은 자리가 11~18호차로도 붙는다 — 두 번째 호차 번호를 괄호로 병기
  if (mirrorNo) ch.appendChild(txt('span', 'car-chip-mirror', `(${mirrorNo}호차)`));
  ch.appendChild(txt('span', 'car-chip-sub', (c.rest != null ? `잔여 ${rest}` : `${rest || 0}석`)));
  ch.title = `${spec ? '특실' : '일반실'} ${n}호차` + (mirrorNo ? ` / 중련 시 ${mirrorNo}호차` : '') + (c.total ? ` · ${rest}/${c.total}석` : '');
  if (onClick) ch.addEventListener('click', () => {
    onClick();
    try { ch.scrollIntoView({ inline: 'center', block: 'nearest', behavior: 'smooth' }); } catch {}
  });
  return ch;
}

// ── 좌석맵 렌더러(예매 탭·후보 편집기 공용) ───────────────────
// seats: [{label,row,col,seq,dir,avail?}], opts: { dirNote, pending, isSel(x), isDisabled(x), badge(x), onPick(x) }
// 좌석 속성 — 코레일 웹 번들에서 확인한 공식 코드표
const SEAT_ATT_NAMES = {
  '003': '자유석', '015': '일반석', '018': '2층석', '019': '유아동반석',
  '020': '노인석', '021': '휠체어석', '024': '할당석', '027': '가족석',
  '028': '전동휠체어석', '032': '자전거거치석', '033': '입석', '039': '온돌마루실석',
  '040': '커플별실석', '041': '패밀리별실석', '051': '정기권좌석지정석',
  '052': '대피도우미석', '055': '장애인보호자석',
};
// 자격자 전용이거나 좌석 개념이 아니라 자동 예매에서 제외하는 자리
// 대피도우미석(052)은 위치 표시일 뿐이라 일반석 취급. 할당석(024)·정기권석(051)은 판매 상태.
const SEAT_ATT_BLOCKED = new Set(['020', '021', '028', '032', '033', '055']);
// 유아동반석 — 고를 수는 있으나 어떤 자리인지 알아볼 수 있게 표시한다
const _ICON_WHEEL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="10" cy="4" r="2"/><path d="M10 8v6h5l3 5"/><path d="M14.5 15.5a5.5 5.5 0 1 1-5.2-6.4"/></svg>';
const _ICON_BABY = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="6.5" r="3"/><path d="M6.5 21v-4a5.5 5.5 0 0 1 11 0v4"/><path d="M9.5 6.2h5"/></svg>';
function seatAttOf(x) { return String((x && x.attr) || '').padStart(3, '0'); }
// 이 호차에 실제로 있는 특수 좌석만 범례에 덧붙인다(없으면 아무것도 안 붙는다)
function _attLegend(seats) {
  const kinds = [...new Set((seats || []).map(seatAttOf)
    .filter(a => SEAT_ATT_BLOCKED.has(a) || a === '019'))];
  if (!kinds.length) return '';
  return ' ' + kinds.map(a => (a === '019'
    ? '<span class="seat-cell avail lg att-baby"></span>'
    : '<span class="seat-cell lg att-blocked' + (['021','028','055','052'].includes(a) ? ' att-wheel' : '') + '"></span>')
    + SEAT_ATT_NAMES[a]).join(' ');
}

function _renderSeatNumGrid(host, ordered, o) {
  const wrap = div('seat-hwrap'); const table = div('seat-numgrid');
  table.appendChild(txt('div', 'seat-numgrid-note', (o.dirNote || '') + '  ·  좌·우 끝이 창측(추정)'));
  const per = 4;   // 2+2 좌석. 가운데(2번째 뒤)에 통로 간격.
  for (let i = 0; i < ordered.length; i += per) {
    const rowEl = div('seat-numrow');
    const grp = ordered.slice(i, i + per);
    grp.forEach((x, j) => {
      if (j === 2) rowEl.appendChild(div('seat-numgap'));   // 통로
      const cell = el('button', 'seat-cell'); cell.type = 'button';
      if (!x || x.noseat) { cell.classList.add('none'); cell.disabled = true; rowEl.appendChild(cell); return; }
      // 4석(2+2) 묶음의 양끝(화면 좌·우 끝)을 창측으로 본다(2026-09-09 지시). API엔 창측 정보가 없어 위치로 추정.
      const isWin = (j === 0 || j === (grp.length - 1)) && grp.length >= 3;
      x.window = isWin; x.aisle = !isWin;
      const att = seatAttOf(x); const attName = SEAT_ATT_NAMES[att] || '';
      const blocked = SEAT_ATT_BLOCKED.has(att); const badge = o.badge ? o.badge(x) : '';
      const hasIcon = att === '019' || ['021', '028', '055', '052'].includes(att);
      cell.textContent = hasIcon && !badge ? '' : (badge || String(x.label || ''));
      if (o.pending) cell.classList.add('seat-pending');
      else cell.classList.add(x.avail === false ? 'na' : 'avail');
      if (x.dir === 'fwd') cell.classList.add('fwd'); else if (x.dir === 'rev') cell.classList.add('rev');
      if (isWin) cell.classList.add('seat-win');
      if (o.isSel && o.isSel(x)) cell.classList.add('sel');
      const icon = att === '019' ? _ICON_BABY : ['021', '028', '055', '052'].includes(att) ? _ICON_WHEEL : '';
      if (icon) { cell.classList.add(att === '019' ? 'att-baby' : 'att-wheel');
        const ic = document.createElement('span'); ic.className = 'seat-ic'; ic.innerHTML = icon; cell.appendChild(ic); }
      if (blocked) { cell.classList.add('att-blocked'); cell.title = `${x.label} · ${attName}`; }
      else cell.title = x.label + (isWin ? ' · 창측' : ' · 복도측');
      cell.disabled = o.pending ? true : (blocked || !!(o.isDisabled && o.isDisabled(x)));
      if (o.onPick && !blocked) cell.addEventListener('click', () => o.onPick(x));
      if (o.onCell) o.onCell(x, cell);
      rowEl.appendChild(cell);
    });
    table.appendChild(rowEl);
  }
  wrap.appendChild(table); host.appendChild(wrap);
  return { hw: wrap, numeric: true };   // 호출부(직접 선택)가 hw로 높이 고정·로딩 오버레이를 건다
}

function renderSeatTable(host, seats, opts) {
  const o = opts || {};
  // 행(좌석 번호)은 응답을 뒤집어 그린다 — 응답이 14→1이면 화면은 1→14 (2026-09-08 확인)
  const ordered = (seats || []).slice().sort((a, b) => (b.seq ?? 0) - (a.seq ?? 0));
  // 열(A~D)의 위아래 순서는 뒤집으면 안 된다. 코레일 응답이 이미 그 호차의 실제 배치로 온다:
  //   상행 404편 → 1D,1C,1B,1A …  (열 D,C,B,A)      하행 453편 → 14A,14B,14C,14D … (열 A,B,C,D)
  // 위 정렬은 행을 뒤집으려던 것인데 열까지 같이 뒤집혀, 두 방향 모두 반대로 그려졌다(2026-09-10 실측).
  const bySeq = (seats || []).slice().sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
  const cols = [...new Set(bySeq.map(x => x.col))];   // 응답 순서 = 그 호차의 실제 위아래 배치
  // 무궁화호처럼 열(A~D) 없이 좌석이 숫자만인 경우: 4열(2+2) 숫자 그리드로 그린다.
  // 창측/방향 정보가 없어 표시하지 않는다(2026-09-09 사용자 선택).
  if (cols.every(c => !c)) return _renderSeatNumGrid(host, ordered, o) || { hw: null };   // 호출부가 {hw}를 구조분해한다(무궁화 직접선택 TypeError, QA 2026-09-10)
  const rows = [...new Set(ordered.map(x => x.row))];
  const byKey = {}; ordered.forEach(x => { byKey[x.row + x.col] = x; });
  const wrap = div('seat-hwrap'); const table = div('seat-htable');
  const hd = div('seat-hrow'); hd.appendChild(txt('span', 'seat-cl', ''));
  rows.forEach(rn => hd.appendChild(txt('span', 'seat-hn', String(rn))));
  table.appendChild(hd);
  // 통로는 '몇 번째 열'이 아니라 '묶음이 바뀌는 자리'에 넣는다. 열 개수로 가운데를 잡으면
  // 방향이 뒤집힌 호차(D→A, C→A)에서 통로가 반대편에 들어간다(2026-09-10 실측: 캐시 248건 중 116건이 역순).
  //  · 일반실 2+2 : A·B | C·D   — 코레일 창측/내측 플래그로 확인(A·D=창측, B·C=내측 → 통로는 B와 C 사이)
  //  · 특실  2+1 : A | B·C     — A가 혼자다. 이건 API로 알 수 없다(B만 내측이라 통로가 A쪽인지 C쪽인지
  //                              플래그로는 갈리지 않는다). 실제 배치 기준(2026-09-10 사용자 확인).
  const _single = (o.layout === '2+1') || (o.layout !== '2+2' && cols.length === 3);
  const _pair = _single ? (c => (c === 'A' ? 1 : 2)) : (c => ((c === 'A' || c === 'B') ? 1 : 2));
  let aisleAt = cols.findIndex((c, i) => i > 0 && _pair(c) !== _pair(cols[i - 1]));
  if (aisleAt < 0) aisleAt = Math.ceil(cols.length / 2);   // 한 묶음만 있는 특수 배치
  cols.forEach((c, ci) => {
    if (ci === aisleAt) {
      const aisle = div('seat-aisle');
      if (o.dirNote) aisle.appendChild(txt('span', 'seat-aisle-note', o.dirNote));
      table.appendChild(aisle);
    }
    const r = div('seat-hrow'); r.appendChild(txt('span', 'seat-cl', c));
    rows.forEach(rn => {
      const x = byKey[rn + c];
      const cell = el('button', 'seat-cell'); cell.type = 'button';
      if (!x || x.noseat) { cell.classList.add('none'); cell.disabled = true; r.appendChild(cell); return; }
      const att = seatAttOf(x);
      const attName = SEAT_ATT_NAMES[att] || '';
      const blocked = SEAT_ATT_BLOCKED.has(att);
      const badge = o.badge ? o.badge(x) : '';
      const _att = seatAttOf(x);
      const _hasIcon = _att === '019' || ['021', '028', '055', '052'].includes(_att);
      // 아이콘이 붙는 자리는 숫자를 지우고 아이콘만 크게 — 숫자와 겹쳐 읽기 어렵다(2026-09-09 지시)
      cell.textContent = _hasIcon && !badge ? '' : (badge || String(rn));
      if (o.pending) cell.classList.add('seat-pending');
      else cell.classList.add(x.avail === false ? 'na' : 'avail');
      if (x.dir === 'fwd') cell.classList.add('fwd'); else if (x.dir === 'rev') cell.classList.add('rev');
      if (o.isSel && o.isSel(x)) cell.classList.add('sel');
      // 아이콘: 유아동반석 · 휠체어 계열(휠체어/전동휠체어/보호자/대피도우미)
      const icon = att === '019' ? _ICON_BABY
                 : ['021', '028', '055', '052'].includes(att) ? _ICON_WHEEL : '';
      if (icon) {
        cell.classList.add(att === '019' ? 'att-baby' : 'att-wheel');
        const ic = document.createElement('span'); ic.className = 'seat-ic'; ic.innerHTML = icon;
        cell.appendChild(ic);
      }
      if (blocked) {                 // 자격자 전용 — 고를 수 없고, 마우스오버로 종류를 알린다
        cell.classList.add('att-blocked');
        cell.title = `${x.label} · ${attName}`;
      } else {
        cell.title = x.label + (x.window ? ' · 창측' : '') + (x.dir === 'fwd' ? ' · 순방향' : x.dir === 'rev' ? ' · 역방향' : '');
      }
      cell.disabled = o.pending ? true : (blocked || !!(o.isDisabled && o.isDisabled(x)));
      if (o.onPick && !blocked) cell.addEventListener('click', () => o.onPick(x));
      if (o.onCell) o.onCell(x, cell);
      r.appendChild(cell);
    });
    table.appendChild(r);
  });
  wrap.appendChild(table);
  const hw = wrapHScroll(wrap);
  host.appendChild(hw);
  const lg = div('seat-legend');
  lg.innerHTML = (o.legend || '<span class="seat-cell avail fwd lg"></span>순방향 <span class="seat-cell avail rev lg"></span>역방향 <span class="seat-cell avail sel lg"></span>선택');
  host.appendChild(lg);
  return { wrap, hw };
}

// 가로 스크롤 영역을 프리셋 바처럼 감싼다(스크롤바 숨김 + 좌우 흐림/화살표)
function wrapHScroll(inner) {
  const wrap = div('hs-wrap');
  inner.classList.add('hs-inner');
  const l = div('hs-fade left'); l.innerHTML = '‹';
  const r = div('hs-fade right'); r.innerHTML = '›';
  const step = () => Math.max(120, Math.round(inner.clientWidth * 0.7));
  l.addEventListener('click', () => inner.scrollBy({ left: -step(), behavior: 'smooth' }));
  r.addEventListener('click', () => inner.scrollBy({ left: step(), behavior: 'smooth' }));
  const upd = () => {
    const max = inner.scrollWidth - inner.clientWidth;
    wrap.classList.toggle('has-left', inner.scrollLeft > 2);
    wrap.classList.toggle('has-right', inner.scrollLeft < max - 2);
  };
  inner.addEventListener('scroll', upd);
  // PC: 마우스로 잡아끌어 스크롤
  let down = false, sx = 0, sl = 0, moved = false;
  inner.addEventListener('mousedown', e => {
    if (e.button !== 0) return;
    down = true; moved = false; sx = e.clientX; sl = inner.scrollLeft;
    inner.classList.add('dragging');
  });
  window.addEventListener('mousemove', e => {
    if (!down) return;
    const dx = e.clientX - sx;
    if (Math.abs(dx) > 3) { moved = true; e.preventDefault(); }
    inner.scrollLeft = sl - dx;
  });
  window.addEventListener('mouseup', () => {
    if (!down) return;
    down = false; inner.classList.remove('dragging');
    if (moved) {   // 드래그였으면 그 클릭은 무시
      const kill = ev => { ev.stopPropagation(); ev.preventDefault(); };
      inner.addEventListener('click', kill, { capture: true, once: true });
    }
  });
  wrap.append(inner, l, r);
  requestAnimationFrame(upd); setTimeout(upd, 150);
  return wrap;
}

// 앵커 아래(공간 없으면 위)에 띄우되, 어떤 경우에도 앵커를 덮지 않는다.
// 모바일 함정 3가지를 여기서 함께 처리한다:
//  ① 키보드가 열리면 보이는 영역이 visualViewport(offsetTop/height)로 줄어든다
//  ② 스크롤은 window가 아니라 .panel 안에서 일어난다 → 캡처 리스너로 따라간다
//  ③ display:none 상태에서 높이를 재면 0이라 '아래 공간 충분' 판정이 틀린다
function placeAnchoredBox(box, anchor, opt) {
  const o = opt || {};
  const gap = o.gap == null ? 4 : o.gap, pad = o.pad == null ? 8 : o.pad;
  const r = anchor && anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : null;
  if (!r || (!r.width && !r.height)) return;
  const prevVis = box.style.visibility, prevDisp = box.style.display;
  if (getComputedStyle(box).display === 'none') { box.style.visibility = 'hidden'; box.style.display = 'block'; }
  if (box._naturalH == null) {           // 자연 높이는 한 번만 잰다(잴 때마다 스크롤이 초기화된다)
    const keep = box.style.maxHeight;
    box.style.maxHeight = '';
    box._naturalH = box.scrollHeight || 0;
    box.style.maxHeight = keep;
  }
  const natural = Math.min(box._naturalH || 0, o.maxH || 240) || (o.maxH || 240);
  const vv = window.visualViewport;
  const vTop = vv ? vv.offsetTop : 0;
  const vH = vv ? vv.height : window.innerHeight;
  const vLeft = vv ? vv.offsetLeft : 0;
  const vW = vv ? vv.width : window.innerWidth;
  const below = (vTop + vH) - r.bottom - gap - pad;
  const above = r.top - vTop - gap - pad;
  let useBelow = below >= Math.min(natural, o.minShow || 96) || below >= above;
  let h = Math.max(0, Math.min(natural, useBelow ? below : above));
  if (h < (o.minShow || 96) && (useBelow ? above : below) > h) {   // 반대쪽이 더 넓으면 뒤집는다
    useBelow = !useBelow;
    h = Math.max(0, Math.min(natural, useBelow ? below : above));
  }
  box.style.maxHeight = Math.max(60, h) + 'px';
  let top = useBelow ? r.bottom + gap : r.top - gap - Math.max(60, h);
  top = Math.min(Math.max(vTop + pad, top), vTop + vH - Math.max(60, h) - pad);
  // 최후 방어: 그래도 앵커와 겹치면 넓은 쪽으로 밀어낸다
  const bh = Math.max(60, h);
  if (top < r.bottom && top + bh > r.top) top = (below >= above) ? r.bottom + gap : Math.max(vTop + pad, r.top - gap - bh);
  const w = box.offsetWidth;
  let left = o.alignCenter ? (r.left + r.width / 2 - w / 2) : r.left;
  left = Math.min(Math.max(vLeft + pad, left), vLeft + vW - w - pad);
  box.style.top = Math.round(top) + 'px';
  box.style.left = Math.round(left) + 'px';
  box.style.right = 'auto';
  box.style.visibility = prevVis; box.style.display = prevDisp;
}

function _placePop(pop, anchor, textAnchor) {
  const r = anchor.getBoundingClientRect();
  const tr = (textAnchor || anchor).getBoundingClientRect();   // 캐럿(▾)을 뺀 '값 텍스트' 기준
  const vw = window.innerWidth, vh = (window.visualViewport ? window.visualViewport.height : window.innerHeight), gap = 2, pad = 8;
  const ROWS = 5.5, ROW_H = 32;   // 마지막 한 줄은 절반만 — 목록이 더 있다는 신호
  pop.style.width = 'max-content';                       // 가장 긴 항목에 맞춘다
  pop.style.minWidth = Math.max(72, Math.round(tr.width) + 20) + 'px';
  if (pop.offsetWidth > 280) pop.style.width = '280px';
  placeAnchoredBox(pop, textAnchor || anchor, { maxH: Math.round(ROWS * ROW_H), minShow: 76, gap: 2, alignCenter: true });
}

// 선택형 칩 그룹 — 드롭다운 대신 한 줄(넘치면 줄바꿈)로 고른다. getVal/setVal 로 기존 필드와 값 공유.
function makeChipGroup(options, getVal, setVal) {
  const wrap = div('chip-group'); const btns = [];
  options.forEach(([v, label]) => {
    const b = el('button', 'opt-chip'); b.type = 'button'; b.textContent = label;
    b.addEventListener('click', () => { setVal(v); sync(); });
    btns.push({ v, b }); wrap.appendChild(b);
  });
  function sync() { const cur = String(getVal()); btns.forEach(x => x.b.classList.toggle('on', String(x.v) === cur)); }
  sync();
  return { el: wrap, sync };
}

function makeSheetPicker(options, value, onChange) {
  let cur = value;
  const wrap = div('picker-value');
  const lab = span(''); const caret = span(''); caret.textContent = '▾';
  caret.classList.add('u-muted');
  wrap.append(lab, caret);
  function render() { const o = options.find(o => String(o[0]) === String(cur)); lab.textContent = o ? o[1] : cur; }
  render();
  wrap.addEventListener('click', () => {
    const ov = div('picker-overlay pop'); const sheet = div('picker-sheet pop-menu');
    options.forEach(([v, l]) => {
      const it = div('picker-sheet-item' + (String(v) === String(cur) ? ' selected' : ''));
      it.textContent = l;
      it.addEventListener('click', () => { cur = v; render(); if (ov._cleanup) ov._cleanup(); document.body.removeChild(ov); if (onChange) onChange(cur); });
      sheet.appendChild(it);
    });
    ov.appendChild(sheet);
    ov.addEventListener('click', e => { if (e.target === ov) { if (ov._cleanup) ov._cleanup(); document.body.removeChild(ov); } });
    document.body.appendChild(ov);
    const reposition = e => {
      if (e && e.target && sheet.contains(e.target)) return;   // 메뉴 안에서 스크롤 중이면 건드리지 않는다
      _placePop(sheet, wrap, lab);
    };
    reposition();
    // 화면이 움직여도 계속 따라간다(패널 스크롤·키보드·회전)
    window.addEventListener('scroll', reposition, true);
    window.addEventListener('resize', reposition);
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', reposition);
      window.visualViewport.addEventListener('scroll', reposition);
    }
    ov._cleanup = () => {
      window.removeEventListener('scroll', reposition, true);
      window.removeEventListener('resize', reposition);
      if (window.visualViewport) {
        window.visualViewport.removeEventListener('resize', reposition);
        window.visualViewport.removeEventListener('scroll', reposition);
      }
    };
    const fade = () => sheet.classList.toggle('at-end', sheet.scrollTop >= sheet.scrollHeight - sheet.clientHeight - 2);
    sheet.addEventListener('scroll', fade);
    fade(); setTimeout(fade, 60);
    const selEl = sheet.querySelector('.picker-sheet-item.selected');
    if (selEl) sheet.scrollTop = selEl.offsetTop;   // 선택된 값이 목록 맨 위에 오도록
  });
  return { el: wrap, getValue: () => cur, setValue: v => { cur = v; render(); } };
}

// 날짜를 년/월/일 드롭다운으로: "2026년 06월 08일 (월)"
function makeDateDropdowns(value) {
  const base = (value && /^\d{4}-\d{2}-\d{2}$/.test(value)) ? value : new Date().toISOString().slice(0, 10);
  let [y, m, d] = base.split('-').map(Number);
  const cy = new Date().getFullYear();
  const yOpts = []; for (let yy = cy; yy <= cy + 1; yy++) yOpts.push([yy, yy + '년']);
  const mOpts = []; for (let i = 1; i <= 12; i++) mOpts.push([i, String(i).padStart(2, '0') + '월']);
  const dim = () => new Date(y, m, 0).getDate();
  const dayOpts = () => { const a = []; for (let i = 1; i <= dim(); i++) a.push([i, String(i).padStart(2, '0') + '일 (' + _WD[new Date(y, m - 1, i).getDay()] + ')']); return a; };
  const wrap = div(''); wrap.style.cssText = 'display:flex;align-items:center;gap:6px;justify-content:flex-end';
  const yP = makeSheetPicker(yOpts, y, v => { y = v; rebuildDay(); });
  const mP = makeSheetPicker(mOpts, m, v => { m = v; rebuildDay(); });
  let dP = makeSheetPicker(dayOpts(), Math.min(d, dim()), v => { d = v; });
  wrap.append(yP.el, mP.el, dP.el);
  function rebuildDay() {
    if (d > dim()) d = dim();
    const n = makeSheetPicker(dayOpts(), d, v => { d = v; });
    wrap.replaceChild(n.el, dP.el); dP = n;
  }
  return {
    el: wrap,
    getValue: () => `${y}-${String(m).padStart(2, '0')}-${String(Math.min(d, dim())).padStart(2, '0')}`,
    setValue: s => { if (!/^\d{4}-\d{2}-\d{2}$/.test(s || '')) return; [y, m, d] = s.split('-').map(Number); yP.setValue(y); mP.setValue(m); rebuildDay(); },
  };
}

// 저장된 날짜의 요일과 같으면서 오늘 이후(오늘 포함)인 가장 가까운 날짜
function _nextSameWeekday(savedDateStr) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(savedDateStr || '')) return null;
  const wd = new Date(savedDateStr + 'T00:00:00').getDay();
  const r = new Date(); r.setHours(0, 0, 0, 0);
  while (r.getDay() !== wd) r.setDate(r.getDate() + 1);
  return `${r.getFullYear()}-${String(r.getMonth() + 1).padStart(2, '0')}-${String(r.getDate()).padStart(2, '0')}`;
}

function _todayStr() {
  const t = new Date();
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`;
}
// 저장된 날짜가 과거면 '오늘 이후 동일 요일', 오늘 이후면 그대로 사용
function _resolveDate(dateStr) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(dateStr || '')) return null;
  return dateStr >= _todayStr() ? dateStr : _nextSameWeekday(dateStr);
}

function makeSegmentedField(options, value, onChange) {
  let cur = value;
  const seg = div('segmented'); const btns = [];
  const sync = () => btns.forEach(x => x.b.classList.toggle('active', x.v === cur));
  options.forEach(([v, l], i) => {
    const item = div('segmented-item');
    if (i > 0) item.appendChild(div('segmented-sep'));
    const b = el('button', 'segmented-btn' + (v === cur ? ' active' : ''));
    b.type = 'button'; b.textContent = l;
    b.addEventListener('click', () => { cur = v; sync(); if (onChange) onChange(cur); });
    item.appendChild(b); seg.appendChild(item); btns.push({ v, b });
  });
  return { el: seg, btns, getValue: () => cur, setValue: v => { cur = v; sync(); } };
}

// 세그먼트의 특정 항목만 비활성(조건이 안 될 때 자리는 두고 못 누르게)
// 마지막으로 누른 요소 — 안내 문구를 그 아래에 띄우기 위해 기억한다
let _lastClickEl = null;
document.addEventListener('click', e => { _lastClickEl = e.target; }, true);
document.addEventListener('touchstart', e => { _lastClickEl = e.target; }, { capture: true, passive: true });

function _anchorBox(cls) {
  const b = div(cls); document.body.appendChild(b); return b;
}
function _placeAnchored(box, anchor) {
  const r = (anchor && anchor.getBoundingClientRect) ? anchor.getBoundingClientRect() : null;
  const vw = window.innerWidth, vh = (window.visualViewport ? window.visualViewport.height : window.innerHeight);
  if (!r || (!r.width && !r.height)) {   // 앵커가 없으면 화면 아래 가운데
    box.style.left = '50%'; box.style.transform = 'translateX(-50%)';
    box.style.bottom = 'calc(84px + env(safe-area-inset-bottom, 0px))'; box.style.top = 'auto';
    return;
  }
  box.style.transform = 'none'; box.style.bottom = 'auto';
  const w = box.offsetWidth, h = box.offsetHeight, pad = 10, gap = 6;
  let top = r.bottom + gap;
  if (top + h > vh - pad) top = Math.max(pad, r.top - gap - h);
  let left = r.left + r.width / 2 - w / 2;
  left = Math.min(Math.max(pad, left), vw - w - pad);
  box.style.top = Math.round(top) + 'px';
  box.style.left = Math.round(left) + 'px';
}
// alert 대체 — 누른 자리 바로 아래에 문구
let _noteT = null;
function notify(msg, anchor) {
  const box = document.querySelector('.anchor-note') || _anchorBox('anchor-note');
  box.textContent = _byPeriod(msg); box.classList.add('on');   // 마침표에서 줄을 바꿔 읽기 쉽게
  _placeAnchored(box, anchor || _lastClickEl);
  clearTimeout(_noteT); _noteT = setTimeout(() => box.classList.remove('on'), 3200);
}
// confirm 대체 — 같은 자리에 확인/취소
function askConfirm(msg, anchor) {
  return new Promise(resolve => {
    document.querySelectorAll('.anchor-ask').forEach(x => x.remove());
    const box = _anchorBox('anchor-ask on');
    box.appendChild(txt('div', 'anchor-ask-msg', msg));
    const row = div('anchor-ask-row');
    const no = el('button', 'anchor-ask-btn'); no.type = 'button'; no.textContent = '취소';
    const yes = el('button', 'anchor-ask-btn primary'); yes.type = 'button'; yes.textContent = '확인';
    const close = v => { box.remove(); document.removeEventListener('mousedown', onOut, true); resolve(v); };
    no.addEventListener('click', () => close(false));
    yes.addEventListener('click', () => close(true));
    row.append(no, yes); box.appendChild(row);
    const onOut = e => { if (!box.contains(e.target)) close(false); };
    setTimeout(() => document.addEventListener('mousedown', onOut, true), 0);
    _placeAnchored(box, anchor || _lastClickEl);
  });
}

let _toastT = null;
function _toastHide() {
  const e = document.querySelector('.toast');
  if (e) e.classList.remove('on');
  clearTimeout(_toastT); _toastT = null;
}
function toast(msg) {
  let el0 = document.querySelector('.toast');
  if (!el0) { el0 = div('toast'); document.body.appendChild(el0); }
  el0.textContent = _byPeriod(msg); el0.classList.add('on');   // 마침표에서 줄바꿈
  clearTimeout(_toastT); _toastT = setTimeout(_toastHide, 2600);
}
// 떠 있는 토스트는 화면 아무 곳이나 누르면 바로 닫는다.
// pointerdown + capture — 다른 핸들러가 stopPropagation을 걸어도 먼저 받는다.
document.addEventListener('pointerdown', () => { if (_toastT) _toastHide(); }, true);

function _setSegDisabled(field, value, disabled) {
  if (!field || !field.btns) return;
  const hit = field.btns.find(x => x.v === value);
  if (!hit) return;
  hit.b.dataset.blocked = disabled ? '1' : '';
  hit.b.classList.toggle('seg-disabled', !!disabled);
}

// ── 후보 좌석 템플릿(설정에서 등록) ────────────────────────────
// {id, name, trainNo, seats:[{car_no,seat_no,label}], fallback:'watch'|'auto'}
let _seatGroupsP = null;
function _seatGroups() {
  if (!_seatGroupsP) {
    _seatGroupsP = api('GET', '/seatgroups')
      .then(d => { if (d && d.ok) {
        _byTrain = d.by_train || {};
        _byClsf = d.by_clsf || {};
        _mirror = d.mirror || {}; _mirrorCars = d.mirror_cars || {};
        _groupsById = Object.fromEntries((d.groups || []).map(g => [g.id, g]));
        document.dispatchEvent(new Event('ktx-groups-loaded'));   // 그룹 로드 완료 → 선호좌석 재매칭
        return d.groups;
      } return []; })
      .catch(() => []);
  }
  return _seatGroupsP;
}
let _byTrain = null, _byClsf = null;
function _seatGroupsInit() {
  return api('GET', '/seatgroups').then(d => { if (d && d.ok) { _byTrain = d.by_train || {}; _byClsf = d.by_clsf || {}; } }).catch(() => {});
}
// ⚠️ 번호만으로 찾지 않는다 — 시각표 개정으로 같은 번호에 다른 열차가 배정된다
// (2026-09-09 실측: 420이 7월 KTX 18량 → 9월 KTX-산천 8량)
// 열차 목록이 group 을 직접 실어 준다(h_trn_clsf_cd 기준). 없을 때만 번호표로 폴백한다.
// 열차번호 홀수=하행, 짝수=상행(서버 train_updown과 동일). _groupIdOf가 최상위에서 부르므로 여기 둔다
// (이전: 패널 클로저 안에만 있어 group 없는 열차에서 ReferenceError → 시작 버튼이 조용히 죽었다. QA 2026-09-10)
function _udOf(no) { return (parseInt(no, 10) % 2) ? 'down' : 'up'; }
function _groupIdOf(trainType, trainNo, row) {
  if (row && row.group) return row.group;
  const byTrain = (_byTrain || {})[`${trainType}|${String(trainNo || '')}`];
  if (byTrain) return byTrain;
  // 마지막 안전망: clsf 코드 + 방향으로 by_clsf 에서 찾는다. 옛 열차 캐시(그룹 필드 없음)에서도
  // 그룹을 즉시 얻어, 스켈레톤·프리페치가 20~30초 라이브 샘플로 새는 것을 막는다(2026-09-09).
  const clsf = row && row.clsf;
  if (clsf) return (_byClsf || {})[`${clsf}|${_udOf(String(trainNo || ''))}`] || '';
  return '';
}

// B와 C는 같은 편성인데 4·5호차 길이만 뒤바뀌어 있다. B에 등록하면 C가 자동으로 따라간다.
let _mirror = null, _mirrorCars = null;
function _mirrorOf(gid) { return (_mirror || {})[gid] || ''; }
function _isMirrored(gid) { return Object.values(_mirror || {}).includes(gid); }
function _mirrorSeats(seats) {
  const map = _mirrorCars || {};
  return (seats || []).map(x => {
    const c = String(parseInt(x.car_no, 10) || '');
    return map[c] ? { ...x, car_no: map[c] } : { ...x };
  });
}

// 후보 좌석의 호차 번호는 저장된 그대로 보여준다. 같은 열차번호라도 운행일에 따라 1~8 또는
// 11~18호차로 편성되므로(2026-09 실측) 고정 표(trains_by_band)로 번호대를 짐작하면 틀린다.
// 실제 번호대는 예매 시점에 서버가 그날 호차 목록으로 맞춘다(korail.align_cand_cars).
let _groupsById = null;
function _carForTrain(gid, trainNo, carNo) { return carNo; }
// 최근 3개월 이용 내역에서 그룹별 탑승 횟수를 센다(설정 목록에 '내가 탄 편성'을 표시)
let _ridesByGroupP = null;
function _ridesByGroup() {
  if (_ridesByGroupP) return _ridesByGroupP;
  const a = lsGetAccount('ktx');
  if (!a.memberNo || !a.password) return Promise.resolve({});
  _ridesByGroupP = api('POST', '/history/presets', { member_no: a.memberNo, password: a.password })
    .then(d => {
      const out = {};
      if (!d || !d.ok) return out;
      (d.rides || []).forEach(r => {
        const gid = _groupIdOf(r.train_type, r.train_no);
        if (gid) out[gid] = (out[gid] || 0) + 1;
      });
      return out;
    })
    .catch(() => ({}));
  return _ridesByGroupP;
}

// 'KTX-산천 A' 처럼 뒤에 붙는 편성 글자는 띄어쓰지 않는다
function _groupName(g) { return String(g.name || g.train_type || '').replace(/\s+([A-Z])$/, '$1'); }
function _groupLabel(g) { return `${_groupName(g)} (${g.updown === 'up' ? '상행' : '하행'})`; }
// 방향은 2px 작게 — 이름과 한 덩어리로 보이되 덜 튀게
function _groupLabelEl(g) {
  const w = document.createElement('span');
  w.appendChild(document.createTextNode(_groupName(g)));
  w.appendChild(txt('span', 'grp-dir', ` (${g.updown === 'up' ? '상행' : '하행'})`));
  return w;
}

// 갖다 대면 바로 뜨는 설명(기본 title 은 1초쯤 늦다)
let _tipEl = null;
function hoverTip(el, text) {
  const show = () => {
    if (!_tipEl) { _tipEl = div('hovertip'); document.body.appendChild(_tipEl); }
    _tipEl.textContent = text;
    const r = el.getBoundingClientRect();
    _tipEl.style.left = (r.left + r.width / 2) + 'px';
    _tipEl.style.top = (r.bottom + 7) + 'px';
    requestAnimationFrame(() => _tipEl && _tipEl.classList.add('on'));
  };
  const hide = () => { if (_tipEl) _tipEl.classList.remove('on'); };
  el.addEventListener('mouseenter', show);
  el.addEventListener('mouseleave', hide);
  el.addEventListener('click', hide);
}

// i 아이콘을 눌렀을 때 뜨는 간단한 정보 시트
function _infoSheet(title, rows) {
  const ov = div('picker-overlay'); const sheet = div('picker-sheet'); sheet.classList.add('u-sheet');
  sheet.appendChild(txt('div', 'wheel-title', title));
  const card = makeGridCard();
  rows.forEach(([k, v], i) => {
    if (i) card.appendChild(div('grid-divider-h'));
    const cell = div('grid-cell-stack');
    cell.append(txt('div', 'sub-label', k), txt('div', 'info-val', v));
    card.appendChild(cell);
  });
  sheet.appendChild(card);
  const bar = div('preset-actions');
  const b = el('button', 'btn-primary'); b.classList.add('btn-ghost'); b.textContent = '닫기';
  b.addEventListener('click', () => document.body.removeChild(ov));
  bar.appendChild(b); sheet.appendChild(bar);
  ov.appendChild(sheet); ov.addEventListener('click', e => { if (e.target === ov) document.body.removeChild(ov); });
  document.body.appendChild(ov);
}
// 좌석 한 자리를 "15-1A"(호차-좌석)로 — 목록이 길어도 한눈에 보이게 짧게
// ⚠️ _seatLabel 은 좌석등급 이름 변환용으로 이미 있다 — 이름이 겹치면 덮어써져
// 좌석 객체가 [object Object] 로 찍힌다(2026-09-09). 고유한 이름을 쓴다.
function _seatText(s) {
  const car = String(s.car_no || '').replace(/^0+/, '') || '?';
  const no = String(s.seat_no || s.label || '').replace(/^0+/, '');
  return `${car}-${no}`;
}

function _candLoad() { try { return JSON.parse(localStorage.getItem('kor_ktx_cand') || '[]') || []; } catch { return []; } }
function _candSave(list) { try { localStorage.setItem('kor_ktx_cand', JSON.stringify(list)); } catch {} if (typeof syncPush === 'function') syncPush(); }

// 라벨 노드(아이콘 포함 가능) + 컨텐츠로 한 줄 셀
function appendRowNode(card, labelNode, content) {
  if (card.children.length > 0) card.appendChild(div('grid-divider-h'));
  const cell = div('grid-cell-full');
  cell.appendChild(labelNode instanceof Node ? labelNode : txt('div', 'grid-cell-label', labelNode));
  const action = div('grid-cell-action');
  if (content instanceof Node) action.appendChild(content);
  cell.appendChild(action);
  card.appendChild(cell);
}

const _WD = ['일','월','화','수','목','금','토'];
function makeCalendarField(value, onChange, getGuard) {
  let sel = value || new Date().toISOString().slice(0, 10);
  const wrap = div('picker-value');
  const lab = span(''); const caret = span(''); caret.textContent = '▾';
  caret.classList.add('u-muted');
  wrap.append(lab, caret);
  function fmt(d) { const [y, m, da] = d.split('-'); const wd = _WD[new Date(+y, +m - 1, +da).getDay()]; return `${y}.${m}.${da} (${wd})`; }
  function render() { lab.textContent = fmt(sel); }
  render();
  wrap.addEventListener('click', () => {
    let [vy, vm] = sel.split('-').map(Number);
    const ov = div('picker-overlay center'); const sheet = div('picker-sheet cal-sheet');
    let pendingNote = null, tentative = null;   // 확인을 눌러야 실제로 반영(취소하면 이전 날짜 유지)
    let slideDir = '';        // 좌우 스와이프로 월을 넘길 때 방향(애니메이션용)
    function goMonth(delta) {
      slideDir = delta > 0 ? 'left' : 'right';
      vm += delta;
      if (vm > 12) { vm = 1; vy++; } else if (vm < 1) { vm = 12; vy--; }
      draw();
    }
    // 좌우 스와이프로 월 이동(터치 + 마우스 드래그)
    let sx = null, sy = null;
    const onDown = (x, y) => { sx = x; sy = y; };
    const onUp = (x, y) => {
      if (sx == null) return;
      const dx = x - sx, dy = y - sy; sx = null;
      if (Math.abs(dx) < 45 || Math.abs(dx) < Math.abs(dy) * 1.2) return;
      goMonth(dx < 0 ? 1 : -1);
    };
    sheet.addEventListener('touchstart', e => { const t = e.changedTouches[0]; onDown(t.clientX, t.clientY); }, { passive: true });
    sheet.addEventListener('touchend', e => { const t = e.changedTouches[0]; onUp(t.clientX, t.clientY); }, { passive: true });
    sheet.addEventListener('mousedown', e => onDown(e.clientX, e.clientY));
    sheet.addEventListener('mouseup', e => onUp(e.clientX, e.clientY));
    // 헤더·요일줄·범례는 한 번만 만들고, 월을 넘길 때는 그리드만 교체한다
    const head = div('cal-head');
    const prevBtn = el('button', 'cal-nav'); prevBtn.type = 'button'; prevBtn.textContent = '‹';
    const title = div('cal-title');
    const nextBtn = el('button', 'cal-nav'); nextBtn.type = 'button'; nextBtn.textContent = '›';
    prevBtn.addEventListener('click', () => goMonth(-1));
    nextBtn.addEventListener('click', () => goMonth(1));
    head.append(prevBtn, title, nextBtn);
    const wdRow = div('cal-wd');
    _WD.forEach((w, i) => { const c = div('cal-wd-c'); c.textContent = w; if (i === 0) c.style.color = 'var(--red)'; wdRow.appendChild(c); });
    const gridHost = div('cal-grid-host');
    const legend = div('cal-legend');
    legend.innerHTML = '<span class="cal-dot today"></span>오늘 <span class="cal-dot holiday"></span>명절 <span class="cal-dot preopen"></span>예매 전(흐림)';
    const noteHost = div('');
    sheet.append(head, wdRow, gridHost, legend, noteHost);
    function draw() {
      title.textContent = `${vy}년 ${vm}월`;
      gridHost.innerHTML = ''; noteHost.innerHTML = '';
      const wd = div('cal-wd-unused');

      const grid = div('cal-grid');
      const first = new Date(vy, vm - 1, 1).getDay();
      const days = new Date(vy, vm, 0).getDate();
      const today = new Date(); today.setHours(0, 0, 0, 0);
      const guard = getGuard ? getGuard() : null;   // null이면 제한 없음(오픈런)
      const reasons = [];
    // 첫 주·마지막 주의 빈칸을 앞뒤 달 날짜로 채운다(흐리게, 누르면 그 달로 이동)
    const adjacent = (y, m, d, delta) => {
      const cell = div('cal-day adj'); cell.textContent = d;
      cell.title = `${m}월로 이동`;
      cell.addEventListener('click', () => goMonth(delta));
      return cell;
    };
      const prevDays = new Date(vy, vm - 1, 0).getDate();
      const pm = vm === 1 ? 12 : vm - 1;
      for (let i = first; i > 0; i--) grid.appendChild(adjacent(vy, pm, prevDays - i + 1, -1));
      for (let d = 1; d <= days; d++) {
        const cell = div('cal-day'); cell.textContent = d;
        const ds = `${vy}-${String(vm).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
        const cd = new Date(vy, vm - 1, d);
        const why = (guard && cd >= today) ? (guard(ds) || '') : '';
        const info = _dayInfo(ds);
        const isHoliday = !!(info && info.status === 'holiday');
        // 예매 가능일과 명절만 또렷하게, 아직 예매 전(오픈런) 날짜는 흐리게
        const preOpen = cd >= today && !isHoliday && !_isOpenNow(ds);
        if (cd.getTime() === today.getTime()) { cell.classList.add('today'); cell.appendChild(txt('span', 'cal-today-tag', '오늘')); }
        if (isHoliday) { cell.classList.add('holiday'); cell.title = '명절 특별수송기간 — 코레일 별도 일정으로 예매'; }
        else if (preOpen) { cell.classList.add('preopen'); cell.title = '아직 예매 전 — 고르면 오픈런으로 설정됩니다'; }
        if (cd < today || why) {
          cell.classList.add('disabled');
          if (why) { cell.title = why; cell.classList.add('blocked'); if (reasons.indexOf(why) < 0) reasons.push(why); }
        } else {
          if (ds === (tentative || sel)) cell.classList.add('sel');
          if (cd.getDay() === 0) cell.classList.add('sun');
          cell.addEventListener('click', () => {
            const note = isHoliday
              ? '명절 특별수송기간입니다. 코레일 별도 일정으로만 예매되고, 잔여석이 풀리는 순간을 노리는 오픈런으로 설정됩니다.'
              : (preOpen ? '아직 예매가 열리지 않은 날짜입니다. 오픈런으로 설정되어 예매가 열리는 순간 자동으로 잡습니다.' : '');
            if (!note) { sel = ds; render(); document.body.removeChild(ov); if (onChange) onChange(sel); return; }
            tentative = ds;
            // 오픈런이 되는 날짜는 달력 아래에 안내를 띄우고 '확인'을 눌러야 닫는다
            pendingNote = note; draw();
          });
        }
        grid.appendChild(cell);
      }
      const nm = vm === 12 ? 1 : vm + 1;
      for (let d = 1, need = (7 - ((first + days) % 7)) % 7; d <= need; d++) grid.appendChild(adjacent(vy, nm, d, 1));
      if (slideDir) { grid.classList.add('cal-slide-' + slideDir); slideDir = ''; }
      gridHost.appendChild(grid);
      if (pendingNote) {
        const box = div('cal-note');
        box.append(txt('div', 'cal-note-title', '오픈런으로 설정됩니다'), txt('div', 'cal-note-body', pendingNote));
        const ok = el('button', 'btn-primary blue'); ok.textContent = '확인'; ok.classList.add('u-btn-wide'); ok.style.marginTop = '10px';
        ok.addEventListener('click', () => { sel = tentative || sel; render(); document.body.removeChild(ov); if (onChange) onChange(sel); });
        box.appendChild(ok); noteHost.appendChild(box);
      }
      reasons.forEach(r => noteHost.appendChild(txt('div', 'cal-hint', r)));
    }
    draw();
    const cancel = div('picker-sheet-cancel'); const cb = div('picker-sheet-cancel-btn');
    cb.textContent = '취소'; cb.addEventListener('click', () => document.body.removeChild(ov));
    cancel.appendChild(cb);
    ov.append(sheet, cancel);
    ov.addEventListener('click', e => { if (e.target === ov) document.body.removeChild(ov); });
    document.body.appendChild(ov);
  });
  return { el: wrap, getValue: () => sel, setValue: s => { if (/^\d{4}-\d{2}-\d{2}$/.test(s || '')) { sel = s; render(); } } };
}

function appendStacked(card, labelText, node) {
  if (card.children.length > 0) card.appendChild(div('grid-divider-h'));
  const cell = div('grid-cell-stack');
  if (labelText) cell.appendChild(txt('div', 'grid-cell-label', labelText));
  cell.appendChild(node);
  card.appendChild(cell);
}

// ── 프리셋 ─────────────────────────────────────────────────────
const _SIDE_OPTS = [['window','창측 우선'],['aisle','내측 우선'],['window_only','창측만'],['aisle_only','내측만']];
const _DIR_OPTS = [['fwd','순방향 우선'],['rev','역방향 우선'],['fwd_only','순방향만'],['rev_only','역방향만']];
function _optLabel(opts, v) { const o = opts.find(x => x[0] === v); return o ? o[1] : opts[0][1]; }
const _SEAT_OPTS = [['general_first','일반실 우선'],['general_only','일반실'],['special_first','특실 우선'],['special_only','특실']];
// 저장된 예매 조건이 없을 때 쓰는 기본값 — 첫 프리셋이 있으면 그걸 따른다
function _presetDefaults() {
  const pz = _presetLoad()[0];
  if (!pz) return {};
  const o = { dep: pz.dep || '', arr: pz.arr || '', adults: pz.adults || 1,
              interval: pz.interval || 10, seatClass: pz.seatClass || 'general_first',
              allowWaiting: !!pz.allowWaiting, startTime: pz.startTime, endTime: pz.endTime };
  if (typeof pz.weekday === 'number') o.date = _nextWeekdayDate(pz.weekday);
  return o;
}

function _presetLoad() { try { return JSON.parse(localStorage.getItem('kor_ktx_presets') || '[]'); } catch { return []; } }
function _presetSave(list) { try { localStorage.setItem('kor_ktx_presets', JSON.stringify(list)); } catch {} if (typeof syncPush === 'function') syncPush(); }
function _histSig(d) { return [d.dep, d.arr, d.weekday, d.startTime, d.endTime, d.adults, d.interval, d.seatClass, d.allowWaiting ? 1 : 0].join('|'); }
function _histLoad() { try { return JSON.parse(localStorage.getItem('kor_ktx_hist') || '{}'); } catch { return {}; } }
function _histRecord(d) {
  const h = _histLoad(), sig = _histSig(d);
  h[sig] = { count: ((h[sig] || {}).count || 0) + 1, data: d };
  try { localStorage.setItem('kor_ktx_hist', JSON.stringify(h)); } catch {}
}
function _seatLabel(v) { const o = _SEAT_OPTS.find(x => x[0] === v); return o ? o[1] : v; }
function _presetTitle(p) { return `${p.dep}→${p.arr} ${_presetWhen(p)} (${p.adults}명)`; }
// 요일이 없는 프리셋도 있다 — 기록의 요일이 흩어져 '많이 탄 구간'으로 잡힌 경우
function _presetWhen(p) { return (typeof p.weekday === 'number' ? `${_WD[p.weekday]}요일 ` : '요일 무관 ') + `${_presetHour(p)}시`; }
function _presetHour(p) { const h = (p.hour != null ? p.hour : parseInt(String(p.startTime || '05:00').slice(0, 2), 10)); return isNaN(h) ? 5 : h; }
function _presetLines(p) {
  return [`${p.dep}→${p.arr}`, _presetWhen(p)];
}
function _nextWeekdayDate(wd) {
  const r = new Date(); r.setHours(0, 0, 0, 0);
  while (r.getDay() !== wd) r.setDate(r.getDate() + 1);
  return `${r.getFullYear()}-${String(r.getMonth() + 1).padStart(2, '0')}-${String(r.getDate()).padStart(2, '0')}`;
}

// 프리셋 추가/수정 모달
// 요일 선택 칩(월~일). getValue()는 JS getDay() 기준 인덱스를 돌려준다.
// 마침표마다 줄을 나눈다(숫자 사이 마침표는 제외). CSS의 white-space: pre-line과 짝.
function _byPeriod(text) {
  return String(text || '').replace(/\.(?!\d)\s+/g, '.\n').trim();
}

function makeWeekdayChips(value, onChange) {
  const order = [1, 2, 3, 4, 5, 6, 0];   // 월 화 수 목 금 토 일
  let cur = (typeof value === 'number') ? value : new Date().getDay();
  const wrap = div('wd-chips'); const btns = [];
  const sync = () => btns.forEach(b => b.el.classList.toggle('on', b.v === cur));
  order.forEach(i => {
    const b = el('button', 'wd-chip' + (i === 0 ? ' sun' : (i === 6 ? ' sat' : ''))); b.type = 'button';
    b.textContent = _WD[i];
    b.addEventListener('click', () => { cur = i; sync(); if (onChange) onChange(cur); });
    wrap.appendChild(b); btns.push({ v: i, el: b });
  });
  sync();
  return { el: wrap, getValue: () => cur, setValue: v => { cur = v; sync(); } };
}

function openPresetEditor(preset, onSave) {
  const p = preset || { dep: '', arr: '', weekday: new Date().getDay(), startTime: '00:00', endTime: '23:50',
                        adults: 1, interval: 10, seatClass: 'general_first', allowWaiting: false, name: '' };
  const ov = div('picker-overlay'); const sheet = div('picker-sheet preset-sheet');
  sheet.appendChild(txt('div', 'wheel-title', preset && preset.id ? '프리셋 수정' : '프리셋 추가'));
  const depF = makeStationField(p.dep, '초성 입력', () => syncSave()); const arrF = makeStationField(p.arr, '초성 입력', () => syncSave());
  const wdF = makeWeekdayChips(p.weekday);
  const hrF = makeSheetPicker(Array.from({ length: 24 }, (_, i) => [i, String(i).padStart(2, '0') + ':00']), _presetHour(p));
  const adF = makeSheetPicker([1,2,3,4,5,6,7,8,9].map(n => [n, n + '명']), p.adults);
  const c = makeGridCard();
  depF.errEl.classList.remove('center'); arrF.errEl.classList.remove('center');
  appendGridFullErr(c, '출발역', depF.el, depF.errEl); appendGridFullErr(c, '도착역', arrF.el, arrF.errEl);
  appendGridFull(c, '요일', wdF.el); appendGridFull(c, '시간', hrF.el);
  appendGridFull(c, '인원', adF.el);
  const body = div('preset-body'); body.append(c); sheet.appendChild(body);
  const bar = div('preset-actions');
  const cancel = el('button', 'btn-primary'); cancel.textContent = '취소';
  cancel.classList.add('btn-ghost');
  cancel.addEventListener('click', () => document.body.removeChild(ov));
  const save = el('button', 'btn-primary blue'); save.textContent = '저장';
  function syncSave() { save.disabled = !(depF.isValid() && arrF.isValid()); }
  save.addEventListener('click', () => {
    if (!depF.isValid() || !arrF.isValid()) { depF.markTouched(); arrF.markTouched(); return; }
    const obj = { id: (preset && preset.id) || (Date.now() + '' + Math.floor(Math.random() * 999)),
      dep: depF.getValue(), arr: arrF.getValue(), weekday: wdF.getValue(),
      hour: hrF.getValue(), startTime: String(hrF.getValue()).padStart(2, '0') + ':00', endTime: '23:50',
      adults: adF.getValue(),
      interval: p.interval, seatClass: p.seatClass, allowWaiting: p.allowWaiting };
    onSave(obj); document.body.removeChild(ov);
  });
  bar.append(cancel, save); sheet.appendChild(bar);
  syncSave();
  ov.appendChild(sheet);
  ov.addEventListener('click', e => { if (e.target === ov) document.body.removeChild(ov); });
  document.body.appendChild(ov);
}

// 프리셋 관리 모달(순서변경 ↑↓ / 수정 / 삭제 / 자주쓴설정)
function openPresetManager(onChanged) {
  const ov = div('picker-overlay'); const sheet = div('picker-sheet preset-sheet');
  function render() {
    sheet.innerHTML = '';
    sheet.appendChild(txt('div', 'wheel-title', '프리셋 관리'));
    const list = _presetLoad();
    const lc = div('preset-list');
    list.forEach((p, i) => {
      const row = div('preset-row');
      row.appendChild(txt('div', 'preset-row-name', _presetTitle(p)));
      const ctrl = div('preset-row-ctrl');
      const up = el('button', 'pbtn'); up.textContent = '▲'; up.disabled = i === 0;
      const dn = el('button', 'pbtn'); dn.textContent = '▼'; dn.disabled = i === list.length - 1;
      const ed = el('button', 'pbtn'); ed.textContent = '수정';
      const dl = el('button', 'pbtn del'); dl.textContent = '삭제';
      up.addEventListener('click', () => { const l = _presetLoad(); [l[i-1], l[i]] = [l[i], l[i-1]]; _presetSave(l); render(); onChanged && onChanged(); });
      dn.addEventListener('click', () => { const l = _presetLoad(); [l[i+1], l[i]] = [l[i], l[i+1]]; _presetSave(l); render(); onChanged && onChanged(); });
      ed.addEventListener('click', () => openPresetEditor(p, obj => { const l = _presetLoad(); const k = l.findIndex(x => x.id === p.id); if (k >= 0) l[k] = obj; _presetSave(l); render(); onChanged && onChanged(); }));
      dl.addEventListener('click', async () => { if (await askConfirm('이 프리셋을 삭제할까요?', dl)) { _presetSave(_presetLoad().filter(x => x.id !== p.id)); render(); onChanged && onChanged(); } });
      ctrl.append(up, dn, ed, dl); row.appendChild(ctrl); lc.appendChild(row);
    });
    if (!list.length) lc.appendChild(txt('div', 'hint', '저장된 프리셋이 없습니다.'));
    sheet.appendChild(lc);
    // 두 버튼은 한 행에 — 시트가 길어지면 아래 '자주 사용한 설정'이 밀린다
    const actRow = div('preset-actions preset-actions-slim');
    const addBtn = el('button', 'btn-primary blue'); addBtn.textContent = '+ 새 프리셋';
    addBtn.addEventListener('click', () => openPresetEditor(null, obj => { const l = _presetLoad(); l.push(obj); _presetSave(l); render(); onChanged && onChanged(); }));
    const autoBtn = el('button', 'btn-primary'); autoBtn.classList.add('btn-ghost');
    autoBtn.textContent = '과거 내역으로 자동 등록';
    autoBtn.addEventListener('click', async () => {
      autoBtn.disabled = true; autoBtn.textContent = '최근 3개월 조회 중…';
      const res = await _histFetch();
      autoBtn.disabled = false; autoBtn.textContent = '과거 내역으로 자동 등록';
      if (!res || !res.ok) { notify(res && res.error ? res.error : '이용 내역을 불러오지 못했습니다'); return; }
      const cl = res.clusters || [];
      if (!cl.length) { notify('최근 3개월간 3회 이상 반복해서 탄 열차가 없습니다'); return; }
      const added = _clustersToPresets(cl);
      if (!added.length) { notify('이미 같은 조건의 프리셋이 등록되어 있습니다'); return; }
      notify(`프리셋 ${added.length}개를 등록했습니다`);
      render(); onChanged && onChanged();
    });
    actRow.append(addBtn, autoBtn);
    sheet.appendChild(actRow);
    const sigs = new Set(list.map(_histSig));
    const freq = Object.values(_histLoad()).filter(h => !sigs.has(_histSig(h.data))).sort((a, b) => b.count - a.count).slice(0, 5);
    if (freq.length) {
      sheet.appendChild(txt('div', 'preset-subhead', '자주 사용한 설정'));
      freq.forEach(h => {
        const d = h.data; const row = div('preset-row');
        row.appendChild(txt('div', 'preset-row-name', `${_presetTitle(d)} · ${h.count}회`));
        const add = el('button', 'pbtn add'); add.textContent = '프리셋 추가';
        add.addEventListener('click', () => {   // 수정 모달 없이 바로 추가
          const l = _presetLoad();
          l.push(Object.assign({ id: Date.now() + '' + Math.floor(Math.random() * 999) }, d));
          _presetSave(l); render(); onChanged && onChanged();
        });
        row.appendChild(add); sheet.appendChild(row);
      });
    }
    const close = el('button', 'btn-primary'); close.textContent = '닫기';
    close.classList.add('btn-ghost'); close.style.marginTop = '12px';
    close.addEventListener('click', () => document.body.removeChild(ov));
    sheet.appendChild(close);
  }
  render();
  ov.appendChild(sheet);
  ov.addEventListener('click', e => { if (e.target === ov) document.body.removeChild(ov); });
  document.body.appendChild(ov);
}

// ── KTX panel (전체 입력 폼) ───────────────────────────────────

// ── CatchTable panel ──────────────────────────────────────────

// ── Build pages ───────────────────────────────────────────────
// ════════ 예매 통합 패널 ════════
function _makeStatusCard() {
  const card = div('card'); card.classList.add('u-flat');
  const top = div(''); top.style.cssText = 'display:flex;align-items:center;gap:10px;padding:16px 18px 12px';
  const dot = span('status-dot idle'); const stTxt = txt('span', 'status-txt', '대기중');
  top.append(dot, stTxt); card.appendChild(top);
  const grid = div('stat3');
  const cell = (label) => { const c = div(''); const v = txt('div', 'stat3-v', '-'); c.append(v, txt('div', 'stat3-l', label)); grid.appendChild(c); return v; };
  const aV = cell('조회'), bV = cell('후보'), cV = cell('마지막 조회');
  card.appendChild(grid);
  function apply(d) {
    let s = { t: '대기중', cls: 'idle' };
    if (d.last_success) s = { t: '예약 성공', cls: 'ok' };
    else if (d.running) s = { t: '탐색중', cls: 'run' };
    else if (d.last_error) s = { t: '오류', cls: 'err' };
    dot.className = 'status-dot ' + s.cls; stTxt.textContent = s.t;
    aV.textContent = d.attempts > 0 ? d.attempts.toLocaleString() + '회' : '-';
    bV.textContent = d.attempts > 0 ? (d.last_candidates || 0) + '개' : '-';
    cV.textContent = d.last_check_at || '-';
  }
  return { el: card, apply };
}

// 계정/결제 요약 행 — KTX 계정 + 결제수단
function _makeAccountRow(onChange) {
  const card = makeGridCard();
  const rows = [];
  const refreshAll = () => { rows.forEach(r => r.refresh()); if (onChange) onChange(); };
  const mkRow = (label, getVal, onClick) => {
    const cell = div('grid-cell-full acct-row');
    const val = txt('div', 'acct-state', '');
    const right = div(''); right.classList.add('u-row8');
    right.append(val);
    cell.append(txt('div', 'grid-cell-label', label), right);
    cell.addEventListener('click', onClick);
    const refresh = () => { const v = getVal(); val.textContent = v || '정보 등록하기'; val.style.color = 'var(--blue)'; };
    const r = { cell, refresh }; refresh(); rows.push(r); return r;
  };
  const r1 = mkRow('KTX 계정', () => { const a = lsGetAccount('ktx'); return a.memberNo ? (a.nick || a.memberNo) : ''; }, () => _ktxAccountModal(refreshAll));
  const r2 = mkRow('결제 수단', () => ktxPayConfigured() ? _payMethodLabel() : '', () => _ktxPayModal(refreshAll));
  card.append(r1.cell, div('grid-divider-h'), r2.cell);
  // 코레일 로그아웃 — 저장된 계정을 지우고 첫 로그인 화면으로 돌아간다
  const outCell = div('grid-cell-full acct-row');
  outCell.append(txt('div', 'grid-cell-label', '코레일 로그아웃'));
  const outVal = txt('div', 'acct-state', '계정 정보 지우기'); outVal.style.color = 'var(--red)';
  outCell.appendChild(outVal);
  outCell.style.cursor = 'pointer';
  outCell.addEventListener('click', async () => {
    if (!lsGetAccount('ktx').memberNo) { notify('등록된 코레일 계정이 없습니다'); return; }
    if (!(await askConfirm('코레일 계정 정보를 지울까요?\n연결된 기기에서도 다시 로그인해야 합니다.', outCell))) return;
    ktxLogout();
  });
  card.append(div('grid-divider-h'), outCell);
  return { el: card, refresh: refreshAll };
}

function _makeCollapse(title, open) {
  const el = div('card'); el.classList.add('u-flat');
  const head = div('collapse-head'); head.append(txt('div', 'grid-cell-label', title));
  const caret = span('collapse-caret', '▾'); head.appendChild(caret);
  const body = div(''); body.style.cssText = 'border-top:1px solid var(--separator);display:none';
  el.append(head, body);
  let o = !!open;
  const set = (v) => { o = v; body.style.display = o ? '' : 'none'; caret.style.transform = o ? 'rotate(180deg)' : ''; };
  head.addEventListener('click', () => set(!o)); set(o);
  return { el, body, set };
}

function _accountModal(name) {
  const ov = div('picker-overlay'); const sheet = div('picker-sheet'); sheet.classList.add('u-sheet-center');
  const t = txt('div', '', name + ' 계정 설정이 필요합니다'); t.style.cssText = 'font-size:17px;font-weight:800;color:var(--text);margin-bottom:8px';
  const n = txt('div', '', '예약 화면에서 비밀번호를 입력하지 않습니다.\n설정에서 계정을 먼저 등록해주세요.'); n.style.cssText = 'font-size:13.5px;color:var(--text-muted);line-height:1.55;white-space:pre-line';
  const bar = div(''); bar.style.cssText = 'display:flex;gap:10px;margin-top:20px';
  const go = el('button', 'btn-primary blue'); go.textContent = '지금 설정'; go.style.flex = '1'; go.addEventListener('click', () => { document.body.removeChild(ov); switchTab('settings'); });
  const cancel = el('button', 'btn-primary'); cancel.textContent = '취소'; cancel.classList.add('btn-ghost'); cancel.style.flex = '1'; cancel.addEventListener('click', () => document.body.removeChild(ov));
  bar.append(go, cancel); sheet.append(t, n, bar);
  ov.appendChild(sheet); ov.addEventListener('click', e => { if (e.target === ov) document.body.removeChild(ov); }); document.body.appendChild(ov);
}

// ── 코레일 공지사항(웹 게시판 API) — 우상단 알림 시트에서 표시 ──
let _noticeData = null, _noticeP = null;
function _loadNotices(force) {
  if (_noticeData && !force) return Promise.resolve(_noticeData);
  if (_noticeP && !force) return _noticeP;
  _noticeP = api('GET', '/notices' + (force ? '?force=1' : '')).then(d => { _noticeData = d || { ok: false, items: [] }; return _noticeData; }).catch(() => (_noticeData = { ok: false, items: [], error: '공지 조회 실패' })).finally(() => { _noticeP = null; });
  return _noticeP;
}
function _noticeSeen() { try { return JSON.parse(localStorage.getItem('kor_ktx_notice_seen') || '[]'); } catch { return []; } }
function _noticeMarkSeen(id) { try { const s = _noticeSeen(); if (!s.includes(id)) { s.push(id); localStorage.setItem('kor_ktx_notice_seen', JSON.stringify(s.slice(-50))); } } catch {} }
function _openNotice(n) {
  const ov = div('picker-overlay'); const sheet = div('picker-sheet'); sheet.classList.add('u-sheet');
  sheet.appendChild(txt('div', 'wheel-title', n.title || '공지'));
  const meta = txt('div', 'hint', (n.pinned ? '📌 고정 · ' : '') + (n.date || '')); meta.classList.add('u-note'); sheet.appendChild(meta);
  const body = txt('div', 'notice-body', n.text || ''); sheet.appendChild(body);
  function renderBody(d) {
    body.innerHTML = '';
    const imgs = (d && d.images) || [];
    imgs.forEach(u => { const im = el('img', 'notice-img'); im.src = u; im.alt = ''; im.loading = 'lazy'; body.appendChild(im); });
    if (d && d.text) body.appendChild(txt('div', '', d.text));
    if (!imgs.length && !(d && d.text)) body.textContent = '(본문 없음 — 코레일에서 보기를 눌러 확인하세요)';
  }
  if ((!n.text && !(n.images && n.images.length)) && n.id != null) {   // 목록 API엔 본문이 없어 열 때 가져옴
    body.appendChild(loadingBox('본문 불러오는 중…'));
    api('GET', '/notices/' + n.id).then(d => { if (d && d.ok) { n.text = d.text; n.images = d.images; renderBody(d); } else body.textContent = '(본문을 불러오지 못했습니다)'; }).catch(() => { body.textContent = '(본문을 불러오지 못했습니다)'; });
  } else renderBody(n);
  const link = el('a', 'btn-primary blue res-btn'); link.textContent = '코레일에서 보기'; link.target = '_blank'; link.href = 'https://www.korail.com/ticket/guest/notice'; link.style.margin = '14px 0 8px'; sheet.appendChild(link);
  const bar = div('preset-actions'); const close = el('button', 'btn-primary'); close.textContent = '닫기'; close.classList.add('btn-ghost'); close.addEventListener('click', () => document.body.removeChild(ov)); bar.appendChild(close); sheet.appendChild(bar);
  ov.appendChild(sheet); ov.addEventListener('click', e => { if (e.target === ov) document.body.removeChild(ov); }); document.body.appendChild(ov);
  if (n.id != null) _noticeMarkSeen(n.id);
}
// 예매 탭 상단 배너: 긴급공지가 있으면 빨강, 아니면 아직 안 본 최신 공지 1건(닫으면 그 공지는 다시 안 뜸)
function _makeNoticeBanner() {
  const wrap = div(''); wrap.style.display = 'none';
  const card = div('card notice-banner'); const t = txt('div', 'notice-banner-t', ''); const x = el('button', 'notice-banner-x'); x.type = 'button'; x.textContent = '×';
  card.append(t, x); wrap.append(card, div('sp10'));
  let cur = null;
  x.addEventListener('click', e => { e.stopPropagation(); if (cur && cur.id != null) _noticeMarkSeen(cur.id); wrap.style.display = 'none'; });
  card.addEventListener('click', () => { if (cur) _openNotice(cur); wrap.style.display = 'none'; });
  function apply(d) {
    if (!d || !d.ok) return;
    const em = (d.emergency || [])[0];
    if (em && em.title) { cur = { title: em.title, text: em.text, date: '긴급' }; card.classList.add('emer'); t.textContent = '🚨 ' + em.title; wrap.style.display = ''; return; }
    const seen = _noticeSeen(); const n = (d.items || []).find(i => !seen.includes(i.id));
    if (n) { cur = n; card.classList.remove('emer'); t.textContent = '📢 ' + n.title + (n.date ? ` · ${n.date}` : ''); wrap.style.display = ''; }
  }
  _loadNotices().then(apply);
  return { el: wrap };
}

// ── 감시 현황 / 결과 카드용 포맷터 ──
function _fmtDur(sec) { sec = Math.max(0, Math.floor(sec)); const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60; return h ? `${h}시간 ${m}분` : (m ? `${m}분 ${s}초` : `${s}초`); }
function _fmtCountdown(sec) { sec = Math.max(0, Math.floor(sec)); const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60; const hh = String(h).padStart(2, '0'), mm = String(m).padStart(2, '0'), ss = String(s).padStart(2, '0'); return d ? `D-${d} ${hh}:${mm}` : (h ? `${hh}:${mm}:${ss}` : `${mm}:${ss}`); }
function _fmtDateK(yyyymmdd) { if (!/^\d{8}/.test(yyyymmdd || '')) return yyyymmdd || ''; const y = +yyyymmdd.slice(0, 4), m = +yyyymmdd.slice(4, 6), d = +yyyymmdd.slice(6, 8); return `${m}/${d}(${_WD[new Date(y, m - 1, d).getDay()]})`; }
function _fmtHHMM(hhmmss) { return hhmmss && hhmmss.length >= 4 ? hhmmss.slice(0, 2) + ':' + hhmmss.slice(2, 4) : (hhmmss || ''); }
const _SLOW_RATIO = 1.3;   // 최소 소요시간 대비 30% 초과면 '느린 열차'
function _durMin(dep, arr) { if (!dep || !arr) return 0; const d = +dep.slice(0,2)*60 + +dep.slice(2,4), a = +arr.slice(0,2)*60 + +arr.slice(2,4); return a >= d ? a - d : a + 1440 - d; }
function _durTxt(m) { const h = (m / 60) | 0, mm = m % 60; return h ? (mm ? `${h}시간 ${mm}분` : `${h}시간`) : `${mm}분`; }
function _seatBadge(v) { return v === 'ok' ? '가능' : v === 'wait' ? '예약대기' : v === 'pre' ? '예매 전' : '매진'; }
function _trainLabel(t) {
  let ty = String(t.type || '').trim();
  const m = String(t.group || '').match(/\|([A-Z])$/);   // 그룹 id 끝 문자 = 편성(A/B/C)
  if (m && ty.indexOf('산천') >= 0) ty = ty + m[1];
  if (!t.no) return ty; return ty ? `${ty} (${t.no})` : String(t.no);
}
// 선호 좌석 목록 제목 — 편성 그룹 이름(열차번호 없이). 예: 'KTX-산천A (상행)'
function _groupTitleFor(gid, ty, ud) {
  const g = (_groupsById || {})[gid];
  if (g) return _groupLabel(g);
  const dir = ud === 'up' ? ' (상행)' : ud === 'down' ? ' (하행)' : '';
  return (ty || '열차') + dir;
}
function _fmtWon(n) { return (n == null || isNaN(n)) ? '' : Number(n).toLocaleString() + '원'; }
function _fmtWhen(dt) { return dt ? `${dt.getMonth() + 1}/${dt.getDate()}(${_WD[dt.getDay()]}) ${String(dt.getHours()).padStart(2, '0')}:${String(dt.getMinutes()).padStart(2, '0')}` : ''; }
function _parseLocal(iso) { const m = (iso || '').match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?/); return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0)) : null; }
function _parseBuyLimit(s) { const m = (s || '').match(/^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})?/); return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0)) : null; }

// ── 감시 현황 카드: 예약됨 / 감시 중 / 명절 대기 / 오류. 유휴 시 숨김. 1초마다 경과·카운트다운 갱신.
function _makeMonitorCard() {
  const card = div('card mon-card'); card.style.display = 'none';
  const head = div('mon-head'); const dot = span('status-dot idle'); const title = txt('div', 'mon-title', ''); const sub = txt('div', 'mon-sub', '');
  const tcol = div('mon-tcol'); tcol.append(title, sub); head.append(dot, tcol); card.appendChild(head);
  const grid = div('stat3 mon-grid');
  const cell = (label) => { const c = div(''); const v = txt('div', 'stat3-v', '-'); c.append(v, txt('div', 'stat3-l', label)); grid.appendChild(c); return v; };
  const aV = cell('조회'), bV = cell('마지막 조회'), cV = cell('다음 조회'), dV = cell('후보');
  card.appendChild(grid);
  const cov = txt('div', 'mon-cov', ''); card.appendChild(cov);
  const err = txt('div', 'mon-err', ''); err.style.display = 'none'; card.appendChild(err);
  let last = null, tick = null;
  function render() {
    const d = last; if (!d) return;
    const holiday = !!d.holiday_block;
    const show = d.phase === 'scheduled' || d.phase === 'running' || !!d.running || (!!d.last_error && d.phase !== 'done');
    card.style.display = show ? '' : 'none'; if (!show) return;
    let cls = 'run', t = '', sub2 = '';
    if (d.phase === 'scheduled') {
      const at = _parseLocal(d.scheduled_at); cls = 'sched';
      t = '예약됨 · ' + (at ? `${_fmtWhen(at)} 시작` : '시작 대기');
      sub2 = at ? `시작까지 ${_fmtCountdown((at.getTime() - Date.now()) / 1000)} · 45초 전에 자동 로그인` : '';
    } else if (holiday) {
      const open = _parseLocal(d.holiday_open_at); cls = 'wait';
      t = '명절 대기 — 잔여석 판매 개시까지 자동 대기';
      sub2 = open ? `${_fmtWhen(open)} 개시 예정 · ${_fmtCountdown((open.getTime() - Date.now()) / 1000)} 남음` : '';
    } else if (d.running) {
      t = '감시 중' + (d.started_ts ? ` · ${_fmtDur(Date.now() / 1000 - d.started_ts)}째` : '');
      sub2 = (d.status_text && d.status_text !== '감시 중') ? d.status_text : '';
    } else { cls = 'err'; t = d.status_text || '오류'; }
    dot.className = 'status-dot ' + (cls === 'err' ? 'err' : (cls === 'run' ? 'run' : 'idle'));
    card.className = 'card mon-card ' + cls;
    title.textContent = t; sub.textContent = sub2; sub.style.display = sub2 ? '' : 'none';
    aV.textContent = d.attempts > 0 ? d.attempts.toLocaleString() + '회' : '-';
    bV.textContent = d.last_check_at || '-';
    cV.textContent = (d.running && d.phase === 'running' && d.next_delay_sec != null) ? '약 ' + d.next_delay_sec + '초' : '-';
    dV.textContent = d.attempts > 0 ? (d.last_candidates || 0) + '개' : '-';
    grid.style.display = d.phase === 'scheduled' ? 'none' : '';
    cov.textContent = d.coverage ? `조회 범위 ${d.coverage}` : ''; cov.style.display = (d.coverage && d.phase !== 'scheduled') ? '' : 'none';
    const showErr = !!d.last_error && !holiday;
    err.style.display = showErr ? '' : 'none'; err.textContent = showErr ? d.last_error : '';
  }
  function apply(d) { last = d; render(); if (!tick) tick = setInterval(render, 1000); }
  return { el: card, apply };
}

// ── 결과 카드: 예약(좌석/예약대기/입석) + 결제 상태 2단. 미결제면 구입기한 카운트다운, 실패면 빨간 경고.
function _makeResultCard() {
  const card = div('card result-card'); card.style.display = 'none';
  const head = div('res-head'); const title = txt('div', 'res-title', ''); head.appendChild(title); card.appendChild(head);
  const body = div('res-body');
  const l1 = txt('div', 'res-route', ''), l2 = txt('div', 'res-train', ''), l3 = txt('div', 'res-price', '');
  body.append(l1, l2, l3); card.appendChild(body);
  const raw = txt('div', 'res-raw', ''); raw.style.display = 'none'; card.appendChild(raw);
  const pay = div('res-pay'); const pdot = span('res-pay-dot'); const pcol = div('res-pay-col');
  const ptitle = txt('div', 'res-pay-title', ''), pmsg = txt('div', 'res-pay-msg', '');
  pcol.append(ptitle, pmsg); pay.append(pdot, pcol); card.appendChild(pay);
  const btn = el('a', 'btn-primary blue res-btn'); btn.target = '_blank'; btn.href = 'https://www.korail.com/ticket/reservation/list'; card.appendChild(btn);
  const KIND = { seat: ['🎫', '좌석 확보'], waiting: ['⌛', '예약대기 등록'], standing: ['🚏', '입석 예약'] };
  let last = null, tick = null, shownKey = null, first = true;
  function render() {
    const d = last; if (!d) return;
    const r = d.result; const show = !!(r || d.last_success);
    card.style.display = show ? '' : 'none'; if (!show) return;
    const k = KIND[(r && r.kind) || 'seat'] || KIND.seat;
    title.textContent = `${k[0]} ${k[1]}`;
    if (r && r.dep) {
      l1.textContent = `${_fmtDateK(r.date)} ${r.dep} → ${r.arr}`;
      l2.textContent = [`${_fmtHHMM(r.dep_time)} → ${_fmtHHMM(r.arr_time)}`, [r.train_type, r.train_no].filter(Boolean).join(' '), [r.seat_label, r.seats ? r.seats + '석' : ''].filter(Boolean).join(' '), r.seat_pick || ''].filter(Boolean).join(' · ');
      l3.textContent = _fmtWon(r.price) + (r.ncard ? '  · N카드 할인 적용' : ''); body.style.display = ''; raw.style.display = 'none';
    } else { body.style.display = 'none'; raw.textContent = (r && r.raw) || d.last_success || ''; raw.style.display = ''; }
    const ps = d.pay_state || 'none';
    let cls = 'warn', pt = '', pm = d.pay_message || '', bt = '코레일에서 결제하기', showBtn = true;
    const limit = r ? _parseBuyLimit(r.buy_limit) : null; const left = limit ? (limit.getTime() - Date.now()) / 1000 : null;
    if (r && r.kind === 'waiting') bt = '코레일에서 예약대기 확인';
    if (ps === 'paid') { cls = 'ok'; pt = '결제 완료'; bt = '승차권 확인'; }
    else if (ps === 'paying' || ps === 'waiting') { cls = 'run'; pt = ps === 'paying' ? '자동 결제 중…' : '결제 순번 대기'; showBtn = false; }
    else if (ps === 'failed' || ps === 'error') { cls = 'err'; pt = '결제 실패 — 직접 결제 필요'; }
    else if (ps === 'skipped') { cls = 'dim'; pt = (r && r.kind === 'waiting') ? '결제 없음 (예약대기)' : '자동결제 미적용'; }
    else if (ps === 'dry') { pt = '미결제 (DRY 검증만)'; }
    else { pt = '미결제 — 직접 결제 필요'; }
    if (ps !== 'paid' && ps !== 'paying' && ps !== 'waiting' && limit) {
      const lim = `구입기한 ${_fmtWhen(limit)}`;
      if (left <= 0) { cls = 'err'; pt = '구입기한 만료 — 예약이 취소됐을 수 있습니다'; pm = lim; }
      else { pm = `${lim} · 남은 시간 ${_fmtCountdown(left)}` + (pm ? '\n' + pm : ''); if (left < 300) cls = 'err'; }
    }
    pay.className = 'res-pay ' + cls; ptitle.textContent = pt; pmsg.textContent = pm; pmsg.style.display = pm ? '' : 'none';
    btn.textContent = bt; btn.style.display = showBtn ? '' : 'none';
    card.classList.toggle('alert', cls === 'err');
  }
  function apply(d) {
    last = d; render();
    const key = d.result ? (d.result.rsv_id || d.result.raw) : d.last_success;
    if (key && key !== shownKey) {   // 이 화면에서 새로 생긴 결과면 카드로 스크롤(첫 로드 때는 안 움직임)
      const scroll = !first; shownKey = key;
      if (scroll) setTimeout(() => { try { card.scrollIntoView({ behavior: 'smooth', block: 'center' }); } catch {} }, 150);
    }
    first = false;
    if (!tick) tick = setInterval(render, 1000);
  }
  function hide() { last = null; shownKey = null; card.style.display = 'none'; }
  return { el: card, apply, hide };
}

// ════════ 가이드 템플릿(모든 기능) → 탭별 커스터마이징. cfg.mode: urgent(긴급) | normal(보통) | special(특수) ════════
// 탭별 구성:
//  · 긴급예매: 날짜(오늘)·시간범위·대상 열차 복수 선택·인원·등급·최대소요·예약대기·입석·감시주기 → 아무 열차든 자리 나면 예약
//  · 보통예매: 날짜·시간범위·열차 1개·호차·좌석맵 직접 선택(또는 자동 배정)·인원·등급 → 1회 시도(폴링 없음)
//  · 특수예매: [오픈 대기 | 지정 좌석 감시 | 선호 조건 감시] — 오픈 대기는 출발 1개월 전 07:00 자동 스케줄 + 오픈 직후 집중 감시
const _MODE_LABEL = { urgent: '긴급', normal: '보통', special: '특수' };
function _ymd(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; }
// 코레일 일반 예매 개시 = 출발일 1개월 전 07:00. 예외(코레일 공지 2026-08-31 '승차권 예매 시작 시간 변경 안내'):
// 2026-10-07~10-11 출발 열차는 1개월 전 10:00부터. 새 공지가 나오면 여기에 추가.
// 코레일이 공지로만 알리는 '예매 시작 시각 변경'을 서버가 공지에서 자동으로 읽어 채운다(하드코딩 제거)
let _OPEN_TIME_EXCEPTIONS = [];
function _openTimeFor(dateStr) { const ex = _OPEN_TIME_EXCEPTIONS.find(e => dateStr >= e.from && dateStr <= e.to); return ex ? { h: ex.hour, m: ex.minute, note: ex.note } : { h: 7, m: 0, note: '' }; }
let _DAYS = null;   // {'20260923': {status:'holiday'|'closed', msg}} — 코레일 응답 기반(명절 하드코딩 없음)
function _loadDayStatus() {
  return api('GET', '/daystatus?days=45').then(r => {
    _DAYS = (r && r.ok) ? (r.days || {}) : {};
    if (r && r.ok && Array.isArray(r.open_time_exceptions)) _OPEN_TIME_EXCEPTIONS = r.open_time_exceptions;
    return _DAYS;
  }).catch(() => (_DAYS = {}));
}
function _dayInfo(ds) { const k = (ds || '').replace(/-/g, ''); return (_DAYS && _DAYS[k]) || null; }
function _isOpenNow(dateStr) { const oa = _openAtFor(dateStr); return !oa || oa <= new Date(); }
function _maxOpenDate() {
  const now = new Date();
  for (let i = 45; i >= 0; i--) { const d = new Date(); d.setDate(d.getDate() + i); const ds = _ymd(d); const oa = _openAtFor(ds); if (oa && oa <= now) return ds; }
  return _ymd(new Date());
}
function _openAtFor(dateStr) {
  const m = (dateStr || '').match(/^(\d{4})-(\d{2})-(\d{2})$/); if (!m) return null;
  const y = +m[1], mo = +m[2] - 1, d = +m[3];
  const last = new Date(y, mo, 0).getDate();   // 전달 마지막 날
  const t = _openTimeFor(dateStr);
  return new Date(y, mo - 1, Math.min(d, last), t.h, t.m, 0);
}
function _buildModePanel(panel, cfg) {
  const mode = cfg.mode, kind = 'ktx';
  const RKEY = 'kor_ktx_resv_' + mode;
  const lsResvSet = (o) => { try { localStorage.setItem(RKEY, JSON.stringify(o)); } catch {} if (typeof syncPush === 'function') syncPush(); };
  let saved = {}; try { saved = JSON.parse(localStorage.getItem(RKEY) || 'null') || {}; } catch {}
  // 처음 들어온 화면이면 서울→부산 같은 고정 기본값 대신 첫 프리셋을 채운다
  if (!saved.dep && !saved.arr) Object.assign(saved, _presetDefaults());
  // 통합 예매 탭: 모드는 탭이 아니라 '고른 날짜와 좌석 옵션'에서 파생된다.
  //  · 아직 예매가 안 열린 날짜 → 오픈런(자동)
  //  · 좌석 옵션 '직접 선택' → 열차 1편 + 좌석맵, 그 외 → 여러 열차 감시
  const seatMode = () => (seatModeF ? seatModeF.getValue() : 'auto');   // auto(긴급) | cand(후보 등록) | manual(직접 선택)
  const isManual = () => seatMode() === 'manual';
  // 오픈런(예약) = 아직 예매가 열리지 않은 날. 판단 근거:
  //   ① 코레일이 'closed'(판매 준비 중)로 표시 → 확실히 미개시
  //   ② 개시 시각(_openAtFor: 1개월 전, 명절/공지 예외 반영)이 아직 안 지남
  // 명절('holiday')이라도 개시 시각이 지났으면 지금 감시 가능하므로 오픈런으로 강제하지 않는다
  // (이전 버그: _dayInfo가 있기만 하면 오픈런 → 이미 열린 명절도 오픈런으로 잘못 떴다. 2026-09-09)
  const isOpenRun = () => {
    if (typeof dateF === 'undefined' || !dateF) return false;
    const ds = dateF.getValue();
    const info = _dayInfo(ds);
    if (info && info.status === 'closed') return true;
    return !_isOpenNow(ds);
  };
  const defDate = _todayStr();
  const nowHH = () => { const n = new Date(); return String(n.getHours()).padStart(2, '0') + ':' + String(Math.floor(n.getMinutes() / 10) * 10).padStart(2, '0'); };
  const plusH = (hh, h) => { const [a, b] = hh.split(':').map(Number); const m = Math.min(1430, a * 60 + b + h * 60); return String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0'); };

  // 계정 미설정 시에만 안내 배너(계정은 설정 탭 / 우상단 버튼)
  const setupBanner = div('card'); setupBanner.style.cssText = 'padding:13px 16px;display:flex;align-items:center;gap:10px;cursor:pointer;background:rgba(49,130,246,0.07);border:1px solid rgba(49,130,246,0.18)';
  const sbBody = div(''); sbBody.style.flex = '1';
  sbBody.append(Object.assign(txt('div', '', 'KTX 계정 설정이 필요합니다'), { style: 'font-size:14px;font-weight:700;color:var(--text)' }), Object.assign(txt('div', '', '계정 정보를 등록해야 예매를 시작할 수 있어요'), { style: 'font-size:12px;color:var(--text-muted);margin-top:2px' }));
  const sbGo = txt('div', '', '설정'); sbGo.style.cssText = 'font-size:13.5px;font-weight:700;color:var(--blue);white-space:nowrap';
  setupBanner.append(sbBody, sbGo);
  setupBanner.addEventListener('click', () => _accountSheet(syncSetup));
  const setupWrap = div(''); setupWrap.append(setupBanner, div('sp10')); panel.appendChild(setupWrap);
  function syncSetup() { setupWrap.style.display = ktxAccountReady() ? 'none' : ''; }
  syncSetup();

  // 오픈런 안내(아직 예매가 열리지 않은 날짜를 고르면 자동 전환)
  let seatModeF = null;
  const openBanner = div(''); const openWrap = div('');   // 안내는 달력에서 '확인'으로 받는다(패널 배너 없음)
  const sub = () => (isOpenRun() ? 'open' : (seatMode() === 'auto' ? 'pref' : ''));

  // 프리셋(모든 탭) — 제목 오른쪽에 추가/수정, 비어 있으면 카드 가운데에 자동 설정
  const presetHead = div('group-header gh-row');
  presetHead.appendChild(txt('span', '', '프리셋'));
  const presetMng = el('button', 'gh-action'); presetMng.type = 'button'; presetMng.textContent = '추가/수정';
  presetHead.appendChild(presetMng);
  panel.appendChild(presetHead);
  const presetCard = div('card preset-card'); presetCard.classList.add('u-card-pad'); panel.appendChild(presetCard); panel.appendChild(div('sp10'));
  let renderPresetBar = () => {};

  // 역
  let _ensT = null;
  function _invalidateTrains() { _flashList(); _syncNcardRoute(); _clearFixed(); selTrains = []; carList = null; _trainFetchKey = null; _trainErr = ''; _showN = 10; if (typeof candPv !== 'undefined' && candPv) candPv.refresh();
    if ((dateF.getValue() || '') === _todayStr() && startHour < new Date().getHours()) { startHour = _nearestHour(); hourSpan = 1; }
    renderHourChips(); renderTrainSel(); clearTimeout(_ensT); _ensT = setTimeout(() => { timeRange.refreshLabel(); renderTrainSel(); }, 700);
    // 날짜가 바뀌면 오픈런 여부를 다시 판단해 버튼을 갱신한다(이전 버그: 오픈런→일반 날짜로 바꿔도 버튼이 그대로 남음).
    const _or = isOpenRun();
    if (_or) autoSchedule();
    else if (!schedManual) { startMode = 'now'; startAtDate = ''; startAtTime = ''; }
    schedBtn.classList.toggle('auto', _or);
    renderSched();
  }
  // 저장된 값이 서로 같으면(옛 버전에서 만들어진 상태) 도착역을 비워 다시 고르게 한다
  const _sDep = saved.dep || cfg.defaultDep;
  let _sArr = saved.arr || cfg.defaultArr;
  if (_sArr && _sArr === _sDep) _sArr = '';
  const depF = makeStationField(_sDep, '초성 입력', () => { _invalidateTrains(); save(); }, STATIONS,
    { exclude: () => (typeof arrF !== 'undefined' && arrF ? arrF.getValue() : '') });
  const arrF = makeStationField(_sArr, '초성 입력', () => { _invalidateTrains(); save(); }, STATIONS,
    { exclude: () => depF.getValue() });
  [depF, arrF].forEach(f => { f.el.classList.add('u-stn'); });
  const stationCol = (label, f) => { const col = div(''); col.classList.add('u-col'); const box = div('station-box'); box.appendChild(f.el); col.append(txt('div', 'sub-label', label), box, f.errEl); return col; };
  const swapBtn = el('button', 'swap-btn'); swapBtn.type = 'button'; swapBtn.textContent = '⇄'; swapBtn.classList.add('u-swap');
  swapBtn.addEventListener('click', () => { const t = depF.el.value; depF.el.value = arrF.el.value; arrF.el.value = t; depF.refresh(); arrF.refresh(); _invalidateTrains(); save(); });
  const stationRow = div(''); stationRow.classList.add('u-rowtop');
  stationRow.append(stationCol('출발역', depF), swapBtn, stationCol('도착역', arrF));
  const stationCard = div('card'); stationCard.style.padding = '16px 16px 12px'; stationCard.append(stationRow);
  // N카드는 구간이 맞으면 자동 적용 — 별도 토글 없이 여기 한 줄로 알린다
  const ncardLine = txt('div', 'ncard-line note-line', ''); ncardLine.style.display = 'none';
  function _ncardActive() {
    const nc = lsGetPay().ncard;
    if (!nc || !nc.no || !nc.dep) return null;
    const d = depF.getValue(), a = arrF.getValue();
    if (!((d === nc.dep && a === nc.arr) || (d === nc.arr && a === nc.dep))) return null;
    const today = _ymd(new Date()).replace(/-/g, '');
    if (nc.valid_to && today > nc.valid_to) return null;
    if ((parseInt(adultF.getValue()) || 1) !== 1) return null;
    return nc;
  }
  function renderNcardLine() {
    const nc = _ncardActive();
    if (!nc) { ncardLine.style.display = 'none'; return; }
    const used = (nc.used != null ? nc.used : 0), remain = (nc.remaining != null ? nc.remaining : 0);
    const total = used + remain;
    const days = (() => { const v = String(nc.valid_to || ''); if (!/^\d{8}$/.test(v)) return null;
      const t = new Date(); const e = new Date(+v.slice(0,4), +v.slice(4,6)-1, +v.slice(6,8));
      return Math.max(0, Math.round((e - new Date(t.getFullYear(), t.getMonth(), t.getDate())) / 86400000)); })();
    ncardLine.textContent = `N카드 적용 구간입니다 (잔여 ${remain}회` + (days != null ? `, ${days}일)` : ')');
    ncardLine.style.display = '';
  }

  // 조건 카드
  const cfgCard = makeGridCard();
  // 오픈런만 아직 안 열린 날짜를 고를 수 있다(나머지 탭은 코레일에서 예매 자체가 안 됨)
  const dateF = makeCalendarField(saved.date ? (_resolveDate(saved.date) || defDate) : defDate, () => { _resetHourForDate(); _invalidateTrains(); save(); },
    () => null);   // 통합 탭: 미오픈·명절 날짜도 고를 수 있고, 고르면 오픈런으로 자동 전환된다
  // 오픈런만 예외 — 명절·미오픈 날짜는 일반 예매로는 코레일이 아예 안 받는다
  function _dateBlockReason(ds) {
    const i = _dayInfo(ds);
    // 코레일이 준 명절 안내문(대상열차·예매일자·결제기간)을 그대로 보여준다 — 언제 잡을 수 있는지가 여기 다 있다
    if (i && i.status === 'holiday') return (i.msg ? i.msg.replace(/\s*\(ERR\d+\)\s*$/, '').trim() + '\n\n' : '명절 특별수송기간입니다.\n') + '잔여석은 특수예매 › 오픈런으로 노릴 수 있습니다.';
    if ((i && i.status === 'closed') || !_isOpenNow(ds)) return '아직 예매가 열리지 않은 날짜입니다. 미리 걸어두려면 특수예매 › 오픈런을 쓰세요.';
    return '';
  }
  seatModeF = makeSegmentedField([['auto','긴급'],['cand','선호 좌석'],['manual','직접 선택']],
    ['auto','cand','manual'].includes(saved.seatMode) ? saved.seatMode : 'auto', () => { _carryPick(); syncSections(); save(); });
  seatModeF.el.classList.add('seg-tight'); seatModeF.el.style.width = '100%';

  function _trainKey() { return `${depF.getValue()}|${arrF.getValue()}|${(dateF.getValue() || '').replace(/-/g, '')}`; }
  // 고른 열차의 출발시각 — 서버가 이 구간만 조회하도록(넓은 시간대를 매 사이클 페이징하지 않게)
  // 결과 탭 '조회중인 열차'에 그대로 쓸 열차 정보
  function _rowsOf(nos) {
    const key = _trainKey();
    return (nos || []).map(no => {
      const tr = _tcFind(key, no) || ((fixed && fixed.train && fixed.train.no === no) ? fixed.train : null);
      return tr ? { no: tr.no, type: tr.type || '', dep_time: tr.dep_time || '', arr_time: tr.arr_time || '' } : null;
    }).filter(Boolean);
  }
  function _depTimesOf(nos) {
    const key = _trainKey();
    return (nos || []).map(no => {
      const tr = _tcFind(key, no) || ((fixed && fixed.train && fixed.train.no === no) ? fixed.train : null);
      return tr && /^\d{4,6}$/.test(String(tr.dep_time || '')) ? String(tr.dep_time) : null;
    }).filter(Boolean);
  }
  const _inflight = {};
  let _winInflight = {}, _winAt = {};
  // 조회 실패(서버 다운·ok:false)면 그 시각을 10초간 fresh 취급 — renderTrainSel이 실패 직후 곧바로 다시 부르는
  // 무한 루프를 끊는다(QA 실측 2026-09-10: 서버 재시작 4초 동안 /api/trains 1,800회). '다시 시도'는 이 냉각을 지운다.
  function _winCool(key, a) { _winAt[key + '|' + parseInt(a, 10)] = Date.now() - 50000; }
  function _ensureWindow(key, a, b) {
    if (!depF.isValid() || !arrF.isValid()) return Promise.resolve(false);
    const [dep, arr, d] = key.split('|');
    const wkey = key + '|' + a + '|' + b;
    if (_winInflight[wkey]) return _winInflight[wkey];
    const qs = `/trains?dep=${encodeURIComponent(dep)}&arr=${encodeURIComponent(arr)}&date=${d}`
             + `&start=${a.replace(':','')}` + (b ? `&end=${b.replace(':','')}` : '');
    _winInflight[wkey] = api('GET', qs)
      .then(r => {
        if (r && r.ok && r.trains) {
          _tcMergeWindow(key, r.trains);
          if (r.window) _winAt[key + '|' + parseInt(a, 10)] = Date.now();   // 시간대 조회 → 그 시각만 fresh
          else for (let h = 0; h < 24; h++) _winAt[key + '|' + h] = Date.now();   // 미리보기(하루 전체) → 모든 시각 fresh(예매 전 날 재조회 방지)
        } else _winCool(key, a);
        return r;
      })
      .catch(() => { _winCool(key, a); return false; })
      .finally(() => { delete _winInflight[wkey]; });
    return _winInflight[wkey];
  }
  function _ensureTrains(key) {
    if (!depF.isValid() || !arrF.isValid()) return Promise.resolve({ ok: false, error: '출발역/도착역을 먼저 선택하세요' });
    if (_tcFresh(key)) return Promise.resolve({ ok: true, cached: true });
    if (_inflight[key]) return _inflight[key];
    const [dep, arr, d] = key.split('|');
    _inflight[key] = api('GET', `/trains?dep=${encodeURIComponent(dep)}&arr=${encodeURIComponent(arr)}&date=${d}`)
      .then(r => { if (r && r.ok) _tcSetDay(key, r.trains || [], r.preview); return r || { ok: false, error: '조회 실패' }; })
      .catch(() => ({ ok: false, error: '열차 목록을 불러오지 못했습니다' }))
      .finally(() => { delete _inflight[key]; });
    return _inflight[key];
  }
  function _minTimeForDate() { const d = (dateF.getValue() || ''); if (d !== _todayStr()) return null; const now = new Date(); const m = Math.ceil((now.getHours() * 60 + now.getMinutes() + 1) / 10) * 10; return String(Math.min(1430, m) / 60 | 0).padStart(2, '0') + ':' + String(Math.min(1430, m) % 60).padStart(2, '0'); }
  const defStart = nowHH(), defEnd = plusH(nowHH(), 3);
  // 출발시간은 '칩'으로 고르고(가로 스와이프), 목록은 그 시각부터 1시간씩 보여준다
  let startHour = (() => { const h = parseInt((saved.startTime || '').slice(0, 2), 10); return isNaN(h) ? 5 : h; })();
  let hourSpan = 1, _showN = 10;   // _showN: 더보기로 늘어나는 표시 편수
  function _nearestHour() { const d = new Date(); return Math.min(23, d.getHours()); }
  const timeRange = {
    getStart: () => String(startHour).padStart(2, '0') + ':00',
    getEnd: () => (startHour + hourSpan >= 24) ? '23:59' : String(startHour + hourSpan).padStart(2, '0') + ':00',
    setRange: (a) => { const h = parseInt(String(a).slice(0, 2), 10); if (!isNaN(h)) { startHour = h; hourSpan = 1; } },
    refreshLabel: () => {},
  };
  const hourF = makeSheetPicker(Array.from({ length: 24 }, (_, h) => [h, String(h).padStart(2, '0') + ':00']), startHour,
    v => { startHour = +v; hourSpan = 1; _showN = 10; selTrains = []; _flashList(); renderTrainSel(); save(); });
           // 시간대 조회는 renderTrainSel이 시작시각별로 1회만 한다(중복 _ensureWindow 제거 — 2콜 원인, 2026-09-10)
  function renderHourChips() {
    // 오늘이면 이미 지난 시각은 고를 수 없게 현재 시각 이후로 올린다
    if ((dateF.getValue() || '') === _todayStr() && startHour < new Date().getHours()) {
      startHour = _nearestHour(); hourSpan = 1; hourF.setValue(startHour);
    }
  }
  function _resetHourForDate() {
    startHour = (dateF.getValue() || '') === _todayStr() ? _nearestHour() : 5;
    hourSpan = 1; hourF.setValue(startHour);
  }
  const adultF = makeSheetPicker([[1,'1명'],[2,'2명'],[3,'3명'],[4,'4명'],[5,'5명'],[6,'6명'],[7,'7명'],[8,'8명'],[9,'9명']], saved.adults || 1, () => { renderFixed(); save(); });
  const _pref0 = lsGetSeatPref();   // 설정 탭에서 정한 기본값
  const seatF = makeSheetPicker(_SEAT_OPTS, saved.seatClass || _pref0.seatClass, () => save());
  const ivlF = makeSheetPicker([[5,'5초'],[10,'10초'],[30,'30초'],[60,'60초']], saved.interval || 10, () => save());   // 긴급 기본 10초
  const _waitMsg = '예약대기로 좌석을 확보하면 자동 결제가 지원되지 않습니다.\n좌석이 확보되면 코레일 앱에서 직접 결제해주세요.';
  const waitCb = makeCheckbox(saved.allowWaiting === undefined ? _pref0.allowWaiting : !!saved.allowWaiting); waitCb.addEventListener('change', () => { save(); _syncEtcNotes(); });
  const _standMsg = '좌석이 매진이고 입석만 남은 열차도 자동예매합니다.\n⚠️ 실험적 기능: 좌석이 보장되지 않고 자동결제도 미적용입니다.';
  const standingCb = makeCheckbox(saved.allowStanding === undefined ? _pref0.allowStanding : !!saved.allowStanding); standingCb.addEventListener('change', () => { save(); _syncEtcNotes(); });
  // 체크된 옵션의 설명은 그 행 바로 아래에 남긴다(토스트 대신)
  const _etcNotes = {};
  function _syncEtcNotes() {
    if (_etcNotes.wait) { _etcNotes.wait.style.display = waitCb.checked ? '' : 'none'; }
    if (_etcNotes.stand) { _etcNotes.stand.style.display = standingCb.checked ? '' : 'none'; }
  }
  // 행 추가 헬퍼(섹션 토글용으로 요소를 돌려줌)
  const rowEls = {};
  function addRow(card, id, label, ctl, info) {
    if (card.children.length) card.appendChild(div('grid-divider-h'));
    const dv = card.lastElementChild && card.lastElementChild.classList.contains('grid-divider-h') ? card.lastElementChild : null;
    const cell = div('grid-cell-full'); const lab = div('grid-cell-label'); lab.classList.add('u-row');
    lab.appendChild(document.createTextNode(label)); if (info) lab.appendChild(makeInfoIcon(info));
    const act = div('grid-cell-action'); act.appendChild(ctl); cell.append(lab, act); card.appendChild(cell);
    rowEls[id] = [dv, cell].filter(Boolean); return cell;
  }
  // 열차 선택(긴급: 복수 / 보통·특수감시: 단일)
  let selTrains = Array.isArray(saved.trainNos) ? saved.trainNos.slice() : [];
  let fixed = saved.fixed || null;   // {train:{no,type,dep_time,arr_time}, car_no, seats:[{no,label}]}
  const isMulti = () => !isManual();   // '직접 선택'만 열차 1편, 나머지는 여러 열차 대상
  let _manualWhy = '';                // '직접 선택'이 막힌 이유(눌렀을 때만 보여준다)
  let carList = null;                 // 선택한 열차의 호차 목록(칩)
  let _carErr = '';                   // 호차 조회 실패 사유(칩 자리에 표시)
  let _seatBoxH = 0;                  // 마지막 좌석맵 높이 — 로딩 중에도 이만큼 자리를 잡아둔다
  let _listFlashUntil = 0, _listFlashT = null;   // 캐시가 있어도 최소한 이만큼은 로딩을 보여준다
  const _flashList = () => { _listFlashUntil = Date.now() + 320; };
  let _trainFetchKey = null, _trainErr = '', _trainWaitT = null, _trainWaitFrom = 0, _autoPickKey = (Array.isArray(saved.trainNos) && saved.trainNos.length) ? 'init' : null;
  // ── 기차 선택: 출발/도착 + 날짜 + 시간 범위 + 열차 목록(체크박스) ──
  addRow(cfgCard, 'date', '날짜', dateF.el);
  addRow(cfgCard, 'time', '시간', hourF.el);
  addRow(cfgCard, 'adults', '인원', adultF.el);
  const trainBox = div('train-list inline');
  { cfgCard.appendChild(div('grid-divider-h')); const dv = cfgCard.lastElementChild; const cell = div('grid-cell-stack'); cell.appendChild(trainBox); cfgCard.appendChild(cell); rowEls.trains = [dv, cell]; }
  panel.appendChild(txt('div', 'group-header', '기차 선택'));
  {   // 역 선택을 같은 카드 맨 위에 얹어 '기차 선택'을 박스 하나로 묶는다
    const cell = div('grid-cell-stack'); cell.append(stationRow, ncardLine);
    cfgCard.insertBefore(div('grid-divider-h'), cfgCard.firstChild);
    cfgCard.insertBefore(cell, cfgCard.firstChild);
  }
  panel.appendChild(cfgCard); panel.appendChild(div('sp12'));

  // 열차 한 편을 고르면(단일 모드) 호차 칩까지 이어서 불러온다
  function _toggleTrain(tr) {
    const on = selTrains.includes(tr.no);
    if (isManual()) selTrains = on ? [] : [tr.no];   // 직접 선택은 항상 1편(새로 누르면 갈아탄다)
    else selTrains = on ? selTrains.filter(x => x !== tr.no) : [...selTrains, tr.no];
    _syncFixed();
    save(); _refreshTrainMarks(); _syncSeatModeAvail();
  }
  // 좌석맵 상태(fixed)를 선택(selTrains)에 맞춘다. 선택을 바꾸는 곳은 전부 이걸 부른다.
  function _syncFixed() {
    if (!isManual()) return;                          // 긴급·후보 등록은 좌석맵을 쓰지 않는다
    const no = selTrains[0] || null;
    if (!no) { if (fixed) { fixed = null; carList = null; renderFixed(); } return; }
    if (fixed && fixed.train && fixed.train.no === no) return;   // 이미 맞음
    const key = _trainKey();
    const tr = (_tcList(key, timeRange.getStart(), timeRange.getEnd()) || []).find(t => t.no === no) || _tcFind(key, no);
    if (!tr) { fixed = null; carList = null; renderFixed(); return; }
    fixed = { train: { no: tr.no, type: tr.type, dep_time: tr.dep_time, arr_time: tr.arr_time }, car_no: null, seats: [] };
    carList = null; renderFixed(); loadCars();
  }
  // 기본값은 전체 선택 — 단, 가장 빠른 열차보다 30% 넘게 걸리는 편은 빼둔다
  // 선택 표시만 갱신(목록 재생성 없음 → 스크롤 위치 유지)
  // 선택한 열차는 목록을 스크롤해도 위/아래 가장자리에 붙어 계속 보인다
  function _pinSelected(body) {
    if (!body) return;
    const rows = [...body.querySelectorAll('.train-row.pick')];
    const on = rows.filter(r => r.classList.contains('on')).slice(0, 3);
    const H = rows.length ? Math.round(rows[0].getBoundingClientRect().height) || 26 : 26;
    rows.forEach(r => { r.classList.remove('pinned'); r.style.top = ''; r.style.bottom = ''; });
    on.forEach((r, i) => {
      r.classList.add('pinned');
      r.style.top = (i * H) + 'px';                     // 위로 지나가면 상단에 쌓이고
      r.style.bottom = ((on.length - 1 - i) * H) + 'px'; // 아래로 지나가면 하단에 쌓인다
    });
  }
  function _refreshTrainMarks() {
    const body = trainBox.querySelector('.train-list-body');
    setTimeout(() => _pinSelected(body), 0);
    if (!body) { renderTrainSel(); return; }
    [...body.children].forEach(r => {
      const no = r.dataset && r.dataset.no; if (!no) return;
      const on = selTrains.includes(no);
      r.classList.toggle('on', on);
      const cb = r.querySelector('.train-cb'); if (cb) cb.classList.toggle('on', on);
    });
    const n = trainBox.querySelector('.train-list-n');
    if (n) n.textContent = `${selTrains.length}편 선택`;
    _syncSelAll();
    if (typeof candPv !== 'undefined' && candPv) candPv.refresh();   // 선택 열차 바뀌면 선호 좌석 목록도
  }
  function renderTrainSel() {
    try { _renderTrainSel(); }
    catch (e) {
      trainBox.innerHTML = '';
      trainBox.appendChild(txt('div', 'train-list-msg', '열차 목록을 그리지 못했습니다: ' + (e && e.message ? e.message : e)));
      const again = el('button', 'train-more'); again.type = 'button'; again.textContent = '다시 시도';
      again.addEventListener('click', () => { delete _winAt[_winKey]; try { localStorage.removeItem('kor_ktx_traincache'); } catch {} _trainFetchKey = null; _trainErr = ''; renderTrainSel(); });
      trainBox.appendChild(again);
    }
  }
  // 머리 체크박스: 전부 선택=on, 일부=some(줄표), 없음=빈칸
  function _syncSelAll() {
    const cb = trainBox.querySelector('.train-list-head .train-cb'); if (!cb) return;
    const rows = [...trainBox.querySelectorAll('.train-list-body .train-row.pick')];
    const on = rows.filter(r => r.classList.contains('on')).length;
    cb.classList.toggle('on', rows.length > 0 && on === rows.length);
    cb.classList.toggle('some', on > 0 && on < rows.length);
  }
  function _renderTrainSel() {
    timeRange.refreshLabel();
    const single = isManual();
    if (!isMulti() && !single) { trainBox.innerHTML = ''; return; }
    const key = _trainKey(), a = timeRange.getStart(), b = timeRange.getEnd();
    let rows = _tcFrom(key, a, _showN);   // 시작시각부터 _showN편(더보기로 증가)
    const prevTop = (() => { const b = trainBox.querySelector('.train-list-body'); return b ? b.scrollTop : 0; })();
    trainBox.innerHTML = '';
    const flashLeft = _listFlashUntil - Date.now();
    if (flashLeft > 0) {
      const bd = div('train-list-body'); const ld = div('train-list-loading');
      ld.append(div('spinner'), txt('span', '', '열차 불러오는 중…')); bd.appendChild(ld);
      trainBox.append(div('train-list-head'), bd);
      clearTimeout(_listFlashT); _listFlashT = setTimeout(renderTrainSel, flashLeft + 20);
      return;
    }
    const head = div('train-list-head');
    const pv = rows && rows.length && rows.every(t => t.seat === 'pre');
    if (isMulti() && rows && rows.length) {
      const wrap = div('sel-all');
      wrap.append(span('train-cb'), txt('span', 'sel-all-lab', '전체'));
      wrap.addEventListener('click', () => {
        const allOn = rows.every(t => selTrains.includes(t.no));
        selTrains = allOn ? selTrains.filter(no => !rows.some(t => t.no === no))
                          : [...new Set([...selTrains, ...rows.map(t => t.no)])];
        _syncFixed(); save(); renderTrainSel(); _syncSeatModeAvail();
      });
      head.appendChild(wrap);
    } else head.appendChild(span(''));
    head.appendChild(txt('span', 'train-list-n', rows ? `${selTrains.length}편 선택` : ''));
    const body = div('train-list-body'); trainBox.append(head, body);
    // 시작시각부터 10편만 조회한다(끝 구간 무관, 1콜). 시작시각별 60초 fresh.
    const _winKey = key + '|' + parseInt(a, 10);
    const _winFresh = _winAt[_winKey] && (Date.now() - _winAt[_winKey] < 60000);
    if (!_winFresh && _trainFetchKey !== _winKey) {
      _trainFetchKey = _winKey; _trainErr = '';
      clearTimeout(_trainWaitT);
      _trainWaitT = setTimeout(() => {
        if (!_tcFrom(key, a, 10) && !_trainErr) { _trainErr = '열차 목록을 불러오지 못했습니다 (응답 지연).'; renderTrainSel(); }
      }, 15000);
      _ensureWindow(key, a, '')
        .then(r => { if (!r || r.ok === false) _trainErr = (r && r.error) || '열차 목록을 불러오지 못했습니다 (연결 실패). 잠시 후 다시 시도하세요.'; })
        .catch(() => {})
        .finally(() => { clearTimeout(_trainWaitT); _trainFetchKey = null; renderTrainSel(); });
    }
    if (!rows || !rows.length) {
      if (_trainErr) {
        body.appendChild(txt('div', 'train-list-msg', _trainErr));
        const again = el('button', 'train-more'); again.type = 'button'; again.textContent = '다시 시도';
        again.addEventListener('click', () => { delete _winAt[_winKey]; _trainFetchKey = null; _trainErr = ''; renderTrainSel(); });
        body.appendChild(again);
      } else {
        body.appendChild(loadingBox('열차 불러오는 중…'));
      }
      return;
    }
    {   // 새로고침·시간 변경으로 선택한 열차가 목록 밖에 있으면 함께 보여준다
      const shown = new Set(rows.map(t => t.no));
      const want = selTrains;
      const extra = want.filter(no => !shown.has(no))
        .map(no => _tcFind(key, no) || ((fixed && fixed.train && fixed.train.no === no) ? fixed.train : null))
        .filter(Boolean);
      if (extra.length) rows = rows.concat(extra).sort((a, b) => (a.dep_time < b.dep_time ? -1 : 1));
    }
    if (!rows.length) { body.appendChild(txt('div', 'train-list-msg', '이 시간대에 출발하는 열차가 없습니다')); return; }
    const minDur = Math.min(...rows.map(t => _durMin(t.dep_time, t.arr_time) || 9999));
    rows.forEach(tr => {
      const on = selTrains.includes(tr.no);
      const dm = _durMin(tr.dep_time, tr.arr_time), slow = minDur > 0 && dm > minDur * _SLOW_RATIO;
      const r = div('train-row pick' + (on ? ' on' : ''));
      r.append(span('train-cb' + (on ? ' on' : '')),
               txt('span', 'train-time', `${_fmtHHMM(tr.dep_time)}→${_fmtHHMM(tr.arr_time)}`),
               txt('span', 'train-dur' + (slow ? ' slow' : ''), _durTxt(dm)),
               txt('span', 'train-name', _trainLabel(tr)),
               txt('span', 'train-seat ' + tr.seat, _seatBadge(tr.seat)));
      r.dataset.no = tr.no;
      r.addEventListener('click', () => _toggleTrain(tr));
      body.appendChild(r);

    });
    _pinSelected(body);
    _syncSelAll();
    body.scrollTop = prevTop;
    // 7편을 넘으면 아래가 살짝 흐려져 스크롤이 있다는 걸 알린다
    body.classList.toggle('no-fade', rows.length <= 4);
    { // '열차 더보기' — 마지막 조회된 열차 시각 이후로 10편 더 불러온다
      const last = rows.length ? rows[rows.length - 1] : null;
      const lastHH = last ? last.dep_time.slice(0, 2) : '';
      if (last && lastHH < '23') {
        const more = el('button', 'train-more'); more.type = 'button'; more.textContent = '열차 더보기';
        more.addEventListener('click', () => {
          const from = last.dep_time.slice(0, 2) + ':' + last.dep_time.slice(2, 4);   // 마지막 열차 시각부터
          _showN += 10;
          _ensureWindow(key, from, '').finally(() => renderTrainSel());
        });
        body.appendChild(more);
      }
    }
    if (typeof candPv !== 'undefined' && candPv) candPv.refresh();   // 목록·캐시가 갱신된 뒤 선호좌석 다시 매칭
  }

  // ── 좌석 옵션: 보통=좌석맵(정확히 인원수) / 특수-감시=좌석맵(인원수 이상) / 특수-선호=선호 조건 ──
  const seatCard = makeGridCard();
  const sideF = makeSheetPicker(_SIDE_OPTS, _SIDE_OPTS.some(o => o[0] === saved.seatSide) ? saved.seatSide : _pref0.seatSide, () => { renderEtc(); save(); });
  const dirF = makeSheetPicker(_DIR_OPTS, _DIR_OPTS.some(o => o[0] === saved.seatDir) ? saved.seatDir : _pref0.seatDir, () => { renderEtc(); save(); });
  const seatGrid = div('seat-grid-wrap');
  const seatHint = txt('div', 'hint', ''); seatHint.classList.add('u-pad-hint'); seatHint.style.display = 'none';
  const seatModeDesc = txt('div', 'seat-mode-desc note-line', '');
  const seatWarn = txt('div', 'seat-mode-desc warn-line', ''); seatWarn.style.display = 'none';
  const _SEATMODE_DESC = {
    auto: '조건에 맞는 자리를 좌석 상관 없이 예매합니다.',
    cand: '미리 등록한 후보 좌석을 1순위부터 시도합니다.',
    manual: '원하는 자리를 직접 선택합니다.',
  };
  {
    const cell = div('seat-tabs-cell');
    seatModeF.el.classList.add('seg-tabs');
    cell.append(seatModeF.el, seatModeDesc, seatWarn, seatHint);
    seatCard.appendChild(cell); rowEls.pick = [cell];
  }
  // 후보 좌석 — 고르는 게 아니라 선택한 열차마다 자동으로 붙는다(2026-09-09 지시).
  // 좌석 배치 그룹(편성×호차대×방향)으로 매칭하므로 열차마다 다른 후보가 들어갈 수 있다.
  let candOverride = '';   // 더 이상 쓰지 않음(수동 변경 없음)
  const candList = div('cand-auto');
  { // 제목을 셀 상단에, 목록은 그 아래 full-width로(좌우 분할 X — 좁아서)
    if (seatCard.children.length) seatCard.appendChild(div('grid-divider-h'));
    const _dv = seatCard.lastElementChild && seatCard.lastElementChild.classList.contains('grid-divider-h') ? seatCard.lastElementChild : null;
    const cell = div('grid-cell-stack cand-sec');
    const lab = div('grid-cell-label u-row'); lab.appendChild(document.createTextNode('선호 좌석 목록'));
    lab.appendChild(makeInfoIcon('설정 › 선호 좌석 설정에서 등록한 우선순위대로 시도합니다. 열차 편성에 맞는 후보가 자동으로 붙습니다.'));
    cell.append(lab, candList); seatCard.appendChild(cell);
    rowEls['cand'] = [_dv, cell].filter(Boolean);
  }

  function _candTemplateFor(trainNo) {
    if (!trainNo) return null;
    const tr = _tcFind(_trainKey(), trainNo);   // 표시된 10편 밖이어도 캐시 전체에서
    const ty = tr ? String(tr.type || '').trim() : '';
    const gid = (tr && tr.group) || _groupIdOf(ty, trainNo, tr);
    if (!gid) return null;
    return _candLoad().find(x => x.groupId === gid) || null;
  }

  document.addEventListener('ktx-groups-loaded', () => { if (candPv) candPv.refresh(); });
  const candPv = { el: candList, refresh: () => {
    candList.innerHTML = '';
    // 선택 대상: 직접 선택은 fixed.train, 그 외(긴급·후보)는 selTrains. 모드에 안 맞는 stale 값은 쓰지 않는다.
    const nos = isManual() ? (fixed && fixed.train ? [fixed.train.no] : []) : selTrains.slice();
    if (!nos.length) { candList.appendChild(txt('div', 'cand-auto-empty', '열차를 먼저 선택하세요')); return; }
    const seen = new Set();   // 좌석은 편성(그룹) 단위 — 열차번호는 무관하므로 그룹당 1행만
    const cand = _candLoad();
    let missing = 0; const blocks = [];
    nos.forEach(no => {
      const tr = _tcFind(_trainKey(), no);   // 표시된 10편 밖이어도 캐시 전체에서 찾는다
      if (!tr) { missing++; return; }   // 캐시에도 없으면(아직 미조회) '설정 안 됨'으로 오판하지 않는다
      const ty = String(tr.type || '').trim();
      const gid = tr.group || _groupIdOf(ty, no, tr);   // 그룹은 열차 행의 group(clsf 기반). 번호 매칭 아님
      const key = gid || ty || no;
      if (seen.has(key)) return; seen.add(key);
      const ud = gid ? gid.split('|')[1] : (parseInt(no, 10) % 2 ? 'down' : 'up');
      const t = gid ? cand.find(x => x.groupId === gid) : null;
      const title = _groupTitleFor(gid, ty, ud);
      const seats = (t && t.seats) || [];
      const sanchon = ty.indexOf('산천') >= 0;   // KTX-산천은 중련 대비 2줄(원 호차 / +10 호차)
      const block = div('cand-auto-block' + (sanchon && gid && seats.length ? ' dual' : ''));
      block.appendChild(txt('span', 'cand-auto-train', title));
      if (!gid) block.appendChild(txt('span', 'cand-auto-na', '선호 좌석을 이용할 수 없는 열차입니다'));
      else if (!seats.length) block.appendChild(txt('span', 'cand-auto-none', '선호좌석이 설정되지 않았습니다.'));
      else if (sanchon) {
        // 각 좌석을 한 열로: 윗줄 원 호차, 아랫줄 +10 호차. 그리드로 열 시작 위치를 맞춘다. 최대 4개 + ...
        const shown = seats.slice(0, 4), more = seats.length > 4;
        const grid = div('cand-dual'); grid.style.gridTemplateColumns = `repeat(${shown.length + (more ? 1 : 0)}, auto)`;
        const lab = x => x.label || x.seat_no;
        const cell = (car, x, i) => txt('span', 'cand-dual-cell', `${String(car).padStart(2, ' ')}호${lab(x)}${(i < shown.length - 1 || more) ? ',' : ''}`);
        shown.forEach((x, i) => grid.appendChild(cell(parseInt(x.car_no, 10), x, i)));        // 원 호차
        if (more) grid.appendChild(txt('span', 'cand-dual-cell', '…'));
        shown.forEach((x, i) => grid.appendChild(cell(parseInt(x.car_no, 10) + 10, x, i)));    // 중련
        if (more) grid.appendChild(txt('span', 'cand-dual-cell', '…'));
        block.appendChild(grid);
      } else {
        const _c2 = n => String(n).padStart(2, ' ');
        const shown = seats.slice(0, 4).map(x => `${_c2(parseInt(x.car_no, 10))}호${x.label || x.seat_no}`).join(', ');
        block.appendChild(txt('span', 'cand-auto-seatlist', shown + (seats.length > 4 ? ', …' : '')));
      }
      blocks.push({ title, block });
    });
    blocks.sort((p, q) => p.title.localeCompare(q.title, 'ko'));   // 가나다순
    blocks.forEach(b => candList.appendChild(b.block));
    if (!blocks.length) candList.appendChild(txt('div', 'cand-auto-empty', missing ? '열차 정보를 불러오는 중…' : '열차를 먼저 선택하세요'));
  } };

  { seatCard.appendChild(div('grid-divider-h')); const dv = seatCard.lastElementChild; const cell = div('grid-cell-stack'); cell.appendChild(seatGrid); seatCard.appendChild(cell); rowEls.grid = [dv, cell]; }
  // 선호 옵션: 좌석등급·창측/내측·진행 방향·예약대기·입석을 한 줄 칩으로 요약, 끝의 톱니를 눌러 수정
  const etcVal = div('etc-chips');
  const gear = el('button', 'etc-gear'); gear.type = 'button'; gear.title = '선호 옵션 수정';
  gear.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>';
  const etcWrap = div('etc-wrap'); etcWrap.append(etcVal, gear);
  // 선호 좌석 모드에서만 뜻이 있는 설명이라, 문구 대신 라벨 옆 물음표(마우스오버)로 뺐다(2026-09-10)
  const etcCell = addRow(seatCard, 'etc', '선호 옵션', etcWrap,
                         '선호 좌석 예매 실패 시, 이 조건으로 예매합니다');
  const etcInfo = etcCell.querySelector('.info-icon');
  etcCell.style.cursor = 'pointer';
  etcCell.addEventListener('click', () => openEtcSheet());
  function renderEtc() {
    etcVal.innerHTML = '';
    const sb = sub(), chips = [];
    chips.push(_seatLabel(seatF.getValue()));
    if (seatMode() !== 'manual') {   // 긴급·후보 등록 공통(팝업과 동일)
      chips.push(_optLabel(_SIDE_OPTS, sideF.getValue()));
      chips.push(_optLabel(_DIR_OPTS, dirF.getValue()));
    }
    if (seatMode() === 'auto') {   // 예약대기·입석은 긴급에서만
      if (waitCb.checked) chips.push('예약대기 허용');
      if (standingCb.checked) chips.push('입석 허용');
    }
    if (!chips.length) { etcVal.appendChild(txt('span', 'etc-none', '설정 안 함')); return; }
    
    chips.forEach(c => etcVal.appendChild(txt('span', 'etc-chip', c)));
  }
  function openEtcSheet() {
    const sb = sub();
    const ov = div('picker-overlay'), sheet = div('picker-sheet');
    sheet.appendChild(txt('div', 'wheel-title', '선호 옵션'));
    const card = div('etc-list');
    const row = (label, ctl, note, key) => {   // 토글(체크박스)용 — 라벨 옆
      if (card.children.length) card.appendChild(div('grid-divider-h'));
      const cell = div('grid-cell-full'), lab = div('grid-cell-label'); lab.classList.add('u-row');
      lab.appendChild(document.createTextNode(label));
      const act = div('grid-cell-action'); act.appendChild(ctl); cell.append(lab, act); card.appendChild(cell);
      if (note) {
        const n = txt('div', 'etc-note', note);
        card.appendChild(n);
        if (key) _etcNotes[key] = n;
      }
    };
    const rowChips = (label, chip) => {   // 칩 그룹을 제목과 같은 행에, 우측정렬로
      if (card.children.length) card.appendChild(div('grid-divider-h'));
      const cell = div('grid-cell-full'); cell.classList.add('chip-row');
      const act = div('grid-cell-action'); act.appendChild(chip.el);
      cell.append(txt('div', 'grid-cell-label', label), act);
      card.appendChild(cell);
    };
    // 좌석등급·창측/내측·진행 방향 = 칩. 긴급·후보 등록 공통(직접 선택 제외).
    rowChips('좌석등급', makeChipGroup(_SEAT_OPTS, () => seatF.getValue(), v => { seatF.setValue(v); save(); }));
    if (seatMode() !== 'manual') {
      rowChips('창측 / 내측', makeChipGroup(_SIDE_OPTS, () => sideF.getValue(), v => { sideF.setValue(v); renderEtc(); save(); }));
      rowChips('진행 방향', makeChipGroup(_DIR_OPTS, () => dirF.getValue(), v => { dirF.setValue(v); renderEtc(); save(); }));
    }
    // 예약대기·입석은 긴급에서만(후보 등록에는 필요 없음 — 2026-09-09 지시)
    if (seatMode() === 'auto') {
      row('예약대기 허용', waitCb, _waitMsg, 'wait');
      row('입석 허용', standingCb, _standMsg, 'stand');
    }
    _syncEtcNotes();
    sheet.appendChild(card);
    const close = () => { if (ov.parentNode) document.body.removeChild(ov); renderEtc(); save(); };
    const bar = div('picker-sheet-cancel'); const cb = div('picker-sheet-cancel-btn'); cb.textContent = '확인'; cb.style.color = 'var(--blue)';
    cb.addEventListener('click', close); bar.appendChild(cb);
    ov.append(sheet, bar); ov.addEventListener('click', e => { if (e.target === ov) close(); });
    document.body.appendChild(ov);
  }
  panel.appendChild(txt('div', 'group-header', '좌석 옵션'));
  panel.appendChild(seatCard); panel.appendChild(div('sp12'));
  const multiSeat = () => false;   // 좌석은 인원수만큼만 고른다
  function _seatClass() { return /^special/.test(seatF.getValue()) ? '2' : '1'; }
  function _seatQS(cls) { return `dep=${encodeURIComponent(depF.getValue())}&arr=${encodeURIComponent(arrF.getValue())}&date=${(dateF.getValue() || '').replace(/-/g, '')}&train_no=${fixed.train.no}&dep_time=${fixed.train.dep_time}&seat_class=${cls || _seatClass()}&count=${parseInt(adultF.getValue()) || 1}`; }
  function _clearFixed() { if (fixed) { fixed = null; renderFixed(); } }
  function renderFixed() {
    const prevLeft = (() => { const c = seatGrid.querySelector('.car-chips'); return c ? c.scrollLeft : 0; })();
    seatGrid.innerHTML = '';
    if (!fixed || !fixed.train) { seatGrid.appendChild(txt('div', 'train-list-msg', '열차를 먼저 선택하세요')); return; }
    // 호차 선택 칩(좌석맵 바로 위)
    const chips = div('car-chips');
    if (!carList) chips.appendChild(txt('span', 'car-chip dim', '호차 불러오는 중…'));
    else if (!carList.length) {
      const msg = div('car-fail');
      msg.appendChild(txt('span', '', _carErr || '호차 정보를 받지 못했습니다'));
      const again = el('button', 'tk-btn car-retry'); again.type = 'button'; again.textContent = '다시 시도';
      again.addEventListener('click', () => { carList = null; _carErr = ''; renderFixed(); loadCars(); });
      msg.appendChild(again); chips.appendChild(msg);
    }
    else (isManual() ? carList.filter(c => c.rest > 0) : carList).forEach(c => {
      const spec = c.cls === '2';
      const ch = makeCarChip(c, fixed.car_no === c.car_no, null);
      ch.addEventListener('click', () => {
        if (fixed.car_no === c.car_no) return;
        fixed.car_no = c.car_no; fixed.car_cls = c.cls || '1'; fixed.seats = []; fixed.map = null; fixed.skeleton = null;
        _loadSkeleton(true) || _loadSkeleton();   // 캐시가 있으면 즉시, 없으면 받아온 뒤(그 사이 로딩 표시)
        // 고른 호차의 등급과 좌석등급 설정을 맞춰 예약이 어긋나지 않게 한다
        if (spec && !/^special/.test(seatF.getValue())) seatF.setValue('special_first');
        if (!spec && /^special/.test(seatF.getValue())) seatF.setValue('general_first');
        save(); renderFixed(); loadSeatMap();
      });
      chips.appendChild(ch);
    });
    seatGrid.appendChild(wrapHScroll(chips));
    chips.scrollLeft = prevLeft;
    if (carList && carList.length && isManual() && !carList.some(c => c.rest > 0)) { seatGrid.appendChild(txt('div', 'train-list-msg', '이 열차는 지금 매진입니다 (목록은 최근 조회 기준이라 다를 수 있어요)')); return; }
    if (!fixed.car_no) { seatGrid.appendChild(txt('div', 'train-list-msg', '호차를 선택하면 좌석맵이 표시됩니다')); return; }
    if (!fixed.map && !fixed.skeleton) {
      const ld = div('train-list-loading seat-hold');
      if (_seatBoxH) ld.style.minHeight = _seatBoxH + 'px';
      ld.append(div('spinner'), txt('span', '', '좌석맵 불러오는 중…'));
      seatGrid.appendChild(ld); return;
    }
    const pending = !fixed.map;                       // 배치만 있고 잔여 상태는 아직
    const mapData = fixed.map || fixed.skeleton;
    const need = parseInt(adultF.getValue()) || 1; const sel = new Set((fixed.seats || []).map(x => x.no)); const multi = multiSeat();
    const head = div('seat-head');
    const headL = div('seat-head-l');
    headL.appendChild(txt('span', '', `${parseInt(fixed.car_no, 10)}호차`));
    if (pending) { headL.appendChild(div('spinner sm')); headL.appendChild(txt('span', 'seat-loading', '자리 확인 중')); }
    head.appendChild(headL);
    seatGrid.appendChild(head);
    // 좌석맵은 공용 렌더러(renderSeatTable)로 — 후보 편집기와 같은 그림을 쓴다
    const ud = (parseInt(fixed.train.no, 10) % 2) ? '하행' : '상행';
    const { hw } = (renderSeatTable(seatGrid, mapData.seats, {
      pending,
      dirNote: `열차 진행 방향 →  ${ud}`,
      isSel: x => sel.has(x.no),
      isDisabled: x => multi ? false : x.avail === false,
      onPick: x => {
        const cur = fixed.seats || [];
        if (sel.has(x.no)) fixed.seats = cur.filter(y => y.no !== x.no);
        else {
          const keep = (!multi && cur.length >= need) ? cur.slice(cur.length - need + 1) : cur;
          fixed.seats = [...keep, { no: x.no, label: x.label }];
        }
        save();
        const y = window.scrollY, sc = _panelScroll();   // 다시 그려도 화면이 튀지 않게
        renderFixed();
        if (sc) sc.el.scrollTop = sc.top;
        window.scrollTo(0, y);
      },
      legend: '<span class="seat-cell avail lg"></span>빈자리 <span class="seat-cell na lg"></span>판매됨 '
            + '<span class="seat-cell sel lg"></span>선택'
            + (mapData.seats.some(x => x.dir) ? ' <span class="seat-cell avail fwd lg"></span>순방향 <span class="seat-cell avail rev lg"></span>역방향' : '')
            + _attLegend(mapData.seats),
    }) || {});
    if (hw && _seatBoxH) hw.style.minHeight = _seatBoxH + 'px';
    if (hw) requestAnimationFrame(() => { const h = hw.offsetHeight; if (h > 40) { _seatBoxH = h; hw.style.minHeight = ''; } });
    if (pending && hw) {   // 캐시 배치를 보여주는 동안 실제 좌석현황을 부르는 중 — 오버레이로 명확히
      hw.style.position = 'relative';
      const ov = div('seat-loading-ov'); ov.append(div('spinner'), txt('span', '', '실제 좌석 확인 중…'));
      hw.appendChild(ov);
    }
    // 고른 좌석 요약은 좌석맵 아래에 — '13호차 8A, 8B'
    const picks = (fixed.seats || []).map(x => x.label).filter(Boolean);
    seatGrid.appendChild(txt('div', 'seat-pick-sum',
      picks.length ? `${parseInt(fixed.car_no, 10)}호차 ${picks.join(', ')}` : ''));
  }
  // 다시 그릴 때 스크롤 위치를 지키기 위한 현재 스크롤 컨테이너
  function _panelScroll() {
    let e = seatGrid.parentElement;
    while (e && e !== document.body) {
      if (e.scrollHeight > e.clientHeight + 4 && /auto|scroll/.test(getComputedStyle(e).overflowY)) return { el: e, top: e.scrollTop };
      e = e.parentElement;
    }
    return null;
  }
  // 목록은 24시간 캐시라 좌석 상태가 오래됐을 수 있다 — 실시간 조회로 매진이면 그 행을 고쳐 표시
  function _markTrainSoldOut(no) {
    const body = trainBox.querySelector('.train-list-body'); if (!body) return;
    [...body.children].forEach(r => {
      if (!r.dataset || r.dataset.no !== no) return;
      const b = r.querySelector('.train-seat'); if (b) { b.className = 'train-seat none'; b.textContent = '매진'; }
    });
  }
  function loadCars() {
    if (!fixed || !fixed.train) return;
    const f = fixed;
    const needFull = 0;   // 잔여 있는 호차만 보면 된다(웹과 동일하게 등급당 1콜)
    api('GET', `/cars?${_seatQS()}&full=${needFull}`).then(r => {
      if (fixed !== f) return;
      carList = (r && r.cars) || [];
      _carErr = (r && !r.ok) ? (r.error || '') : '';
      if (isManual() && carList.length && !carList.some(c => c.rest > 0)) _markTrainSoldOut(f.train.no);
      if (isManual() && carList.length) _prefetchLayouts(carList);
      if (isManual() && !fixed.car_no) {
        const open = carList.filter(c => c.rest > 0);
        const first = open.find(c => parseInt(c.car_no, 10) === 1) || open[0];
        if (first) {
          fixed.car_no = first.car_no; fixed.car_cls = first.cls || '1'; fixed.seats = []; fixed.map = null;
          _loadSkeleton(true);
          if (first.cls === '2' && !/^special/.test(seatF.getValue())) seatF.setValue('special_first');
          if (first.cls !== '2' && /^special/.test(seatF.getValue())) seatF.setValue('general_first');
          renderFixed(); loadSeatMap(); save(); return;
        }
      }
      renderFixed();
    })
      .catch(() => { if (fixed === f) { carList = []; _carErr = '조회 실패 — 네트워크를 확인하세요'; renderFixed(); } });
  }
  const _trainMaps = {};   // '날짜|열차번호|호차' → 실제 좌석 배치(그 편성 그대로)
  function _tmKey(f, car) { return `${(dateF.getValue() || '').replace(/-/g, '')}|${f.train.no}|${parseInt(car, 10)}`; }
  // 캐시된 좌석 배치를 먼저 깔고(동기), 없으면 받아온 뒤 깐다
  function _loadSkeleton(sync) {
    if (!fixed || !fixed.train || !fixed.car_no || fixed.map) return false;
    const f = fixed;
    const ty = String(f.train.type || '').trim();
    if (!ty) return false;
    const ud = _udOf(f.train.no), car = parseInt(f.car_no, 10);
    const mine = _trainMaps[_tmKey(f, car)];
    if (mine) { f.skeleton = { seats: mine }; return true; }   // 이 열차에서 실제로 봤던 배치 — 정확하다
    const gid = _groupIdOf(ty, f.train && f.train.no, f.train);
    const hit = _layoutCache[(gid || ty + '|' + ud) + '|' + car];
    if (hit && hit.ok) { f.skeleton = { seats: hit.seats || [] }; return true; }   // 같은 배치 그룹의 표본
    if (sync) return false;
    if (!gid) return false;   // 그룹 미해석 → 서버가 라이브 샘플(20~30초). 스켈레톤은 생략하고 실제 좌석맵을 기다린다
    _layoutFetch(ty, car, ud, gid).then(r => {
      if (fixed !== f || f.map || !r || !r.ok) return;
      f.skeleton = { seats: r.seats || [] }; renderFixed();
    }).catch(() => {});
    return false;
  }
  // 호차 목록을 받으면 그 편성의 좌석 배치를 미리 받아둔다(다음 클릭부터 즉시 표시)
  function _prefetchLayouts(cars) {
    if (!fixed || !fixed.train) return;
    const ty = String(fixed.train.type || '').trim();
    if (!ty) return;
    const ud = _udOf(fixed.train.no);
    const gid = _groupIdOf(ty, fixed.train.no, fixed.train);   // f는 이 스코프에 없었다(조용히 죽던 버그)
    if (!gid) return;   // 그룹을 못 구하면 미리 받지 않는다 — 라이브 샘플 폭주 방지
    (async () => {
      for (const c of cars) {
        const no = parseInt(c.car_no, 10);
        if (_layoutCache[gid + '|' + no]) continue;
        try { await _layoutFetch(ty, no, ud, gid); } catch {}
        await new Promise(r => setTimeout(r, 200));
      }
    })();
  }
  function loadSeatMap(silent) {   // silent: 화면 복원용 선조회 — 실패해도 사용자를 부르지 않는다
    if (!fixed || !fixed.car_no) return;
    const f = fixed;
    _loadSkeleton();
    api('GET', `/seats?${_seatQS(f.car_cls)}&car_no=${f.car_no}`).then(r => { if (fixed !== f) return; f.skeleton = null; if (r.ok) { f.map = { seats: r.seats }; _trainMaps[_tmKey(f, f.car_no)] = r.seats; if (!multiSeat()) { const ok = new Set(r.seats.filter(x => x.avail).map(x => x.no)); f.seats = (f.seats || []).filter(x => ok.has(x.no)); } } else { f.map = { seats: [] }; if (!silent) notify('좌석맵 조회 실패: ' + (r.error || '')); } renderFixed(); save(); })
      .catch(() => { if (fixed === f) { f.map = { seats: [] }; renderFixed(); } });
  }

  // 예매 방식
  const runCard = makeGridCard();
  const runF = makeSegmentedField([['reserve','예매만'],['pay','예매+결제']], saved.runMode || 'reserve',
    () => { save(); _syncPayWarn(); });
  runF.el.classList.add('seg-tight'); runF.el.style.width = '180px';
  { const rcell = div('grid-cell-full'); const ract = div('grid-cell-action'); ract.appendChild(runF.el); rcell.append(txt('div', 'grid-cell-label', '예매 방식'), ract); runCard.appendChild(rcell); }
  // 예매+결제일 때만 결제 수단을 고르는 행이 붙는다(설정의 결제 정보와 같은 값을 쓴다)
  const payDv = div('grid-divider-h');
  const payRow = div('grid-cell-full');
  const payMF = makeSheetPicker(_PAY_METHODS, _payMethodOf(lsGetPay()), () => {
    lsSetPay({ method: payMF.getValue() });
    _syncPayWarn(); save();
  });
  { const act = div('grid-cell-action'); act.appendChild(payMF.el);
    payRow.append(txt('div', 'grid-cell-label', '결제 수단'), act); }
  runCard.append(payDv, payRow);

  const payWarn = txt('div', 'warn-line pay-warn', '결제 정보를 먼저 입력하세요');
  // 경고문구는 셀째로 숨긴다 — 문구만 숨기면 빈 셀의 위아래 패딩이 카드 밑에 남는다
  const payWarnCell = div('grid-cell-stack'); payWarnCell.style.display = 'none';
  payWarnCell.appendChild(payWarn); runCard.appendChild(payWarnCell);
  function _syncPayWarn() {
    const pay = runF.getValue() === 'pay';
    [payDv, payRow].forEach(e => e.style.display = pay ? '' : 'none');
    if (pay) payMF.setValue(_payMethodOf(lsGetPay()));
    const need = pay && !payReady();
    payWarnCell.style.display = need ? '' : 'none';
    payWarn.textContent = (need && _payMethodOf(lsGetPay()) === 'card')
      ? '신용카드 정보를 먼저 입력하세요' : '결제 정보를 먼저 입력하세요';
    markSettingsAttention(need);
    _refreshStart();
  }
  document.addEventListener('ktx-acct-sync', _syncPayWarn);
  // N카드는 구간·유효기간·1명 조건이 맞으면 자동 적용된다(별도 토글 없음)
  function _syncNcardRoute() { renderNcardLine(); }
  document.addEventListener('ktx-acct-sync', () => { syncSetup(); renderNcardLine(); });
  panel.appendChild(runCard); panel.appendChild(div('sp12'));

  // 감시 현황 + 버튼 + 결과 + 로그
  const monitor = _makeMonitorCard();   // 화면에 붙이지 않는다(감시 현황은 '결과' 탭)
  const btnStart = makePrimaryButton('', 'blue', onStart); btnStart.classList.add('btn-run');
  let startMode = saved.startMode || 'now', startAtDate = saved.startAtDate || '', startAtTime = saved.startAtTime || '', schedManual = !!saved.schedManual;
  const schedBtn = el('button', 'btn-sched'); schedBtn.type = 'button'; schedBtn.innerHTML = _ICON_CLOCK; schedBtn.title = '예약 시작';
  function _schedAt() { return (startMode === 'at' && startAtDate && startAtTime) ? _parseLocal(startAtDate + 'T' + startAtTime) : null; }
  const baseLabel = () => isOpenRun() ? '오픈런 예약' : (isManual() ? '바로 예매' : '감시 시작');
  function _schedLabel() { const at = _schedAt(); return at ? `${at.getMonth() + 1}/${at.getDate()}(${_WD[at.getDay()]}) ${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')} 시작` : baseLabel(); }
  function renderSched() { schedBtn.classList.toggle('active', !!_schedAt()); if (!_running) btnStart.innerHTML = _ICON_PLAY + '<span>' + _schedLabel() + '</span>'; }
  // 특수-오픈 대기: 출발일이 아직 안 열렸으면(1개월 이상 남음) 개시 시각(1개월 전 07:00)을 자동 세팅
  function autoSchedule() {
    if (!isOpenRun() || schedManual) return;
    const at = _openAtFor(dateF.getValue());
    if (at && at.getTime() > Date.now()) { startMode = 'at'; startAtDate = _ymd(at); startAtTime = `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`; }
    else { startMode = 'now'; }
    renderSched();
  }
  function openSchedSheet() {
    let modeF, dInp, tInp;
    const tmr = new Date(); tmr.setDate(tmr.getDate() + 1);
    _editModal('예약 시작', (body) => {
      const card = makeGridCard();
      modeF = makeSegmentedField([['now','지금 시작'],['at','지정 시각']], startMode, () => syncMode()); modeF.el.classList.add('seg-tight'); modeF.el.style.width = '190px';
      appendGridFull(card, '시작', modeF.el);
      dInp = makeWheelDateField(startAtDate || _ymd(tmr)); dInp.min = _todayStr();
      tInp = makeWheelTimeField(startAtTime || '07:00');
      const aCell = div('grid-cell-full'); const aAct = div('grid-cell-action'); aAct.style.gap = '10px'; aAct.append(dInp.el, tInp.el);
      aCell.append(txt('div', 'grid-cell-label', '시작 일시'), aAct); const dv = div('grid-divider-h'); card.append(dv, aCell);
      body.appendChild(card);
      const quick = div(''); quick.classList.add('u-chips');
      const oa = _openAtFor(dateF.getValue());
      if (oa && oa.getTime() > Date.now()) { const ch = el('button', 'preset-chip'); ch.type = 'button'; ch.textContent = `예매 개시(출발 1개월 전) ${_fmtWhen(oa)}`; ch.style.cssText = 'font-size:12.5px;padding:8px 12px'; ch.addEventListener('click', () => { modeF.setValue('at'); dInp.value = _ymd(oa); tInp.value = `${String(oa.getHours()).padStart(2, '0')}:${String(oa.getMinutes()).padStart(2, '0')}`; syncMode(); }); quick.appendChild(ch); }
      body.appendChild(quick);
      const ex = _openTimeFor(dateF.getValue() || '');
      const hintText = '지정 시각 45초 전에 자동 로그인해 세션을 준비하고, 정각에 감시를 시작합니다. 코레일 일반 예매는 출발일 1개월 전 07:00에 열립니다.'
        + (ex.note ? ` 이 날짜는 ${String(ex.h).padStart(2, '0')}:${String(ex.m).padStart(2, '0')} 개시 — ${ex.note}` : '');
      const hint = txt('div', 'note-line sched-note', _byPeriod(hintText)); body.appendChild(hint);
      function syncMode() { const on = modeF.getValue() === 'at'; dv.style.display = on ? '' : 'none'; aCell.style.display = on ? '' : 'none'; }
      syncMode();
    }, () => {
      if (modeF.getValue() === 'at') {
        const at = _parseLocal(dInp.value + 'T' + tInp.value);
        if (!at) { notify('시작 일시를 입력하세요'); return false; }
        if (at.getTime() - Date.now() < 10000) { notify('이미 지난 시각입니다'); return false; }
        startMode = 'at'; startAtDate = dInp.value; startAtTime = tInp.value;
      } else { startMode = 'now'; }
      schedManual = true; save(); renderSched();
    });
  }
  schedBtn.addEventListener('click', openSchedSheet);
  // 감시주기는 실행 직결 설정이라 시작 버튼 옆에 둔다
  const ivlChip = div('ivl-chip'); const ivlCol = div('');
  ivlCol.append(txt('span', 'ivl-chip-label', '감시주기'), ivlF.el); ivlChip.appendChild(ivlCol);
  ivlChip.title = '설정 주기마다 새로고침하며 좌석을 감시합니다. 매크로 패턴으로 보이지 않게 ±25% 무작위 오차를 둡니다.';
  schedBtn.classList.add('boxed');   // 예약 시작은 감시주기와 별개 박스
  const btnRow = div('btn-row-primary'); btnRow.append(btnStart, ivlChip, schedBtn); panel.appendChild(btnRow);
  const result = _makeResultCard();   // 화면에는 붙이지 않는다 — 결과는 '결과' 탭에서 본다

  // 실시간 로그도 '결과' 탭으로 이동
  const bottomSpacer = div(''); bottomSpacer.classList.add('u-safe-b'); panel.appendChild(bottomSpacer);

  // 섹션 표시 규칙(가이드 템플릿 → 탭별)
  function show(map, id, on) { (map[id] || []).forEach(e => e.style.display = on ? '' : 'none'); }
  // '직접 선택' 가능 여부는 고른 열차에 따라 바뀌므로 목록을 건드릴 때마다 즉시 다시 계산한다
  function _carryPick() {
    // 직접 선택으로 바꾸면 1편만 남긴다. 반대 방향은 선택을 그대로 이어간다.
    if (isManual() && selTrains.length > 1) selTrains = selTrains.slice(0, 1);
    _syncFixed();
    _refreshTrainMarks();
  }
  // '직접 선택'은 언제나 고를 수 있다. 조건이 안 맞으면 막는 대신 경고 문구로 알린다.
  function _manualWarning() {
    if (!isManual()) return '';
    if (!selTrains.length) return '열차를 선택해주세요';
    const trains = _tcList(_trainKey(), timeRange.getStart(), timeRange.getEnd()) || [];
    const tr = trains.find(t => t.no === selTrains[0]) || _tcFind(_trainKey(), selTrains[0]);
    if (tr && tr.seat !== 'ok') return '좌석 지정이 불가능한 열차입니다. 다시 선택하세요';
    if (isOpenRun()) return '예매 전 날짜라 좌석 배치만 볼 수 있습니다';
    return '';
  }
  function _syncSeatModeAvail() {
    _setSegDisabled(seatModeF, 'manual', false);
    _manualWhy = _manualWarning();
    seatWarn.textContent = _manualWhy;
    seatWarn.style.display = _manualWhy ? '' : 'none';
    seatHint.textContent = ''; seatHint.style.display = 'none';
    seatModeDesc.textContent = _SEATMODE_DESC[seatMode()] || '';
    seatModeDesc.style.display = _manualWhy ? 'none' : '';   // 경고가 있으면 설명 대신 경고만
    show(rowEls, 'grid', isManual() && !_manualWhy);         // 고를 수 없는 상태면 좌석맵도 감춘다
    _refreshStart();
  }

  // 감시를 시작할 수 없는 이유(없으면 빈 문자열). 시작 버튼 활성 여부가 이걸 따른다.
  function _startBlockReason() {
    if (depF.getValue() && depF.getValue() === arrF.getValue()) return '출발역과 도착역이 같습니다';
    if (!depF.isValid() || !arrF.isValid()) return '출발역/도착역을 확인하세요';
    if (!selTrains.length) return '열차를 선택해주세요';
    if (isManual()) {
      if (_manualWhy) return _manualWhy;
      const need = parseInt(adultF.getValue()) || 1;
      const cnt = (fixed && fixed.seats || []).length;
      if (cnt && cnt !== need) return `인원(${need}명)만큼 좌석을 고르세요`;
    }
    if (seatMode() === 'cand') {
      const anyTpl = selTrains.some(no => { const t = _candTemplateFor(no); return t && (t.seats || []).length; });
      if (!anyTpl) return '선호 좌석이 등록된 열차를 선택하세요';
    }
    if (runF.getValue() === 'pay' && !payReady()) return '결제 정보를 먼저 입력하세요';
    return '';
  }
  function syncSections() {
    const openRun = isOpenRun();
    _syncSeatModeAvail();

    show(rowEls, 'trains', true);
    show(rowEls, 'pick', true);
    show(rowEls, 'grid', isManual() && !_manualWhy);
    show(rowEls, 'etc', !isManual());
    ivlChip.classList.toggle('off', isManual());   // 한 번에 예매 — 주기 감시 안 함
    ivlF.el.style.pointerEvents = isManual() ? 'none' : '';
    show(rowEls, 'cand', seatMode() === 'cand');
    if (etcInfo) etcInfo.style.display = (seatMode() === 'cand') ? '' : 'none';   // 선호 좌석 모드에서만
    show(rowEls, 'sideRow', seatMode() === 'auto');
    show(rowEls, 'dirRow', seatMode() === 'auto');
    schedBtn.classList.toggle('auto', openRun);   // 오픈런은 시작 시각이 자동 설정된다
    if (!openRun) startMode = 'now'; else autoSchedule();
    renderSched(); renderFixed(); renderEtc();
    if (candPv) candPv.refresh();   // 선호 좌석 목록(#3) — 모드·열차에 맞춰 갱신
  }

  function applyPreset(pz) {
    depF.setValue(pz.dep || ''); arrF.setValue(pz.arr || '');
    if (typeof pz.weekday === 'number') {
      // 지금 고른 날짜가 이미 그 요일이면 그대로 둔다(다음 주를 노리는 중일 수 있다)
      const cur = _parseLocal((dateF.getValue() || '') + 'T00:00');
      if (!cur || cur.getDay() !== pz.weekday) dateF.setValue(_nextWeekdayDate(pz.weekday));
    }
    const ph = _presetHour(pz); startHour = ph; hourSpan = 1; hourF.setValue(ph);
    adultF.setValue(pz.adults || 1); ivlF.setValue(pz.interval || 10);
    seatF.setValue(pz.seatClass || 'general_first'); waitCb.checked = !!pz.allowWaiting; _invalidateTrains(); save();
  }
  const _openPresetMng = () => {
    const list = _presetLoad();
    if (list.length) openPresetManager(renderPresetBar);
    else openPresetEditor(null, obj => { const l = _presetLoad(); l.push(obj); _presetSave(l); renderPresetBar(); });
  };
  presetMng.addEventListener('click', _openPresetMng);

  renderPresetBar = function () {
    presetCard.innerHTML = '';
    const list = _presetLoad();
    presetMng.textContent = list.length ? '추가/수정' : '직접 추가';
    if (!list.length) {
      // 등록된 게 없으면 과거 이용내역으로 한 번에 만들도록 안내한다
      const box = div('preset-empty');
      const b = el('button', 'btn-auto'); b.type = 'button'; b.textContent = '프리셋 자동 설정하기';
      b.addEventListener('click', () => {
        if (typeof showSetup !== 'function') { _openPresetMng(); return; }
        showSetup(() => { renderPresetBar(); refreshHeaderPill && refreshHeaderPill(); });
      });
      box.appendChild(b);
      box.appendChild(txt('div', 'pay-note note-line muted', '최근 3개월 동안 자주 탄 구간과 시간대를 찾아 등록합니다'));
      presetCard.appendChild(box);
      return;
    }
    const bar = div('preset-bar');
    list.forEach(pz => { const b = el('button', 'preset-chip'); b.type = 'button'; _presetLines(pz).forEach(line => b.appendChild(txt('div', 'preset-chip-line', line))); b.addEventListener('click', () => applyPreset(pz)); bar.appendChild(b); });
    presetCard.appendChild(wrapHScroll(bar));
  };
  renderPresetBar();

  function collect() { return { sub: sub(), dep: depF.getValue(), arr: arrF.getValue(), date: dateF.getValue(), startTime: timeRange.getStart(), endTime: timeRange.getEnd(), adults: adultF.getValue(), interval: ivlF.getValue(), seatClass: seatF.getValue(), seatMode: seatMode(), candTemplate: candOverride, allowWaiting: waitCb.checked, allowStanding: standingCb.checked, runMode: runF.getValue(), startMode, startAtDate, startAtTime, schedManual, trainNos: selTrains, seatSide: sideF.getValue(), seatDir: dirF.getValue(), startTime: String(startHour).padStart(2,'0') + ':00', fixed: fixed ? { train: fixed.train, car_no: fixed.car_no, car_cls: fixed.car_cls, seats: fixed.seats } : null, useNcard: !!_ncardActive() }; }
  let _saveT = null, _running = false;
  function _refreshStart() {
    const why = _startBlockReason();
    btnStart.disabled = _running || !!why;
    btnStart.title = why || '';
    const m = presetCard.querySelector('.preset-chip.add'); if (m) m.disabled = !(depF.isValid() && arrF.isValid());
  }
  function save() { clearTimeout(_saveT); _saveT = setTimeout(() => lsResvSet(collect()), 400); _refreshStart(); }
  if (isManual()) {   // 저장된 상태가 어긋나 있으면(옛 버전) 시작할 때 맞춘다
    if (fixed && fixed.train) selTrains = [fixed.train.no];
    else if (selTrains.length > 1) selTrains = selTrains.slice(0, 1);
  }
  document.addEventListener('ktx-seatpref', () => {   // 설정 탭에서 기본값을 바꾸면 여기에도 바로 적용
    const p = lsGetSeatPref();
    seatF.setValue(p.seatClass); sideF.setValue(p.seatSide); dirF.setValue(p.seatDir);
    waitCb.checked = p.allowWaiting; standingCb.checked = p.allowStanding;
    renderEtc(); _syncEtcNotes(); save();
  });
  renderHourChips(); syncSections(); renderTrainSel(); _syncPayWarn();
  if (fixed && fixed.train && !carList && seatMode() === 'manual') loadCars();   // 호차 목록도 그 모드에서만(등급당 1콜)
  // 저장된 '직접 선택' 좌석은 모드와 무관하게 복원되지만, 좌석맵을 미리 뜨는 건 그 모드가 켜져
  // 있을 때만이다. 긴급·후보 모드에서 옛 열차·호차로 조회하면 등급 불일치(ERI411092) 토스트만 뜬다.
  if (fixed && fixed.car_no && !fixed.map && seatMode() === 'manual') loadSeatMap(true);

  // 예매 탭은 '등록 화면'이다. 진행 상황·중단은 결과 탭에서만 다루고,
  // 여기서는 입력을 잠그지 않는다(감시를 여러 개 등록할 수 있어야 하므로).
  let _lastJob = null;

  function getStartBody() {
    if (!depF.isValid() || !arrF.isValid()) { depF.markTouched(); arrF.markTouched(); notify('출발역/도착역을 확인하세요'); return null; }
    if (!ktxAccountReady()) { _accountSheet(syncSetup); return null; }
    lsResvSet(collect());
    const acct = lsGetAccount(kind);
    const need = parseInt(adultF.getValue()) || 1;
    const base = {
      date: dateInputToYYMMDD(dateF.getValue()), dep: depF.getValue(), arr: arrF.getValue(),
      start_hhmm: timeInputToHHMM(timeRange.getStart()), end_hhmm: timeInputToHHMM(timeRange.getEnd()),
      adults: need, interval_sec: parseInt(ivlF.getValue()) || 10,
      seat_class: seatF.getValue(), max_duration_min: 0,
      seat_mode: 'none', seat_side: sideF.getValue(), seat_dir: dirF.getValue(), fixed_fallback: true,
      train_nos: [], train_times: [], train_rows: [], one_shot: false, watch_seats: [], open_burst: false,
    };
    const sb = sub();
    // 미오픈·명절 날짜는 오픈런으로 자동 전환되므로 여기서 막지 않는다(열리는 순간 잡는다)
    if (isMulti()) {
      if (!selTrains.length) { notify('대상 열차를 하나 이상 선택하세요'); return null; }
      base.train_nos = selTrains.slice();
      base.train_times = _depTimesOf(selTrains);
      base.train_rows = _rowsOf(selTrains);
      // 목록은 시작시각부터 10편을 보여줘 시간대 밖 열차도 고를 수 있다.
      // 고른 열차가 범위 밖이면 감시 범위를 넓혀 표시·기록이 실제와 어긋나지 않게 한다(2026-09-10).
      const _hh = _depTimesOf(selTrains).map(t => String(t).slice(0, 4)).filter(Boolean).sort();
      if (_hh.length) {
        if (_hh[0] < base.start_hhmm) base.start_hhmm = _hh[0];
        if (_hh[_hh.length - 1] > base.end_hhmm) base.end_hhmm = _hh[_hh.length - 1];
      }
    }
    if (isManual()) {
      // 직접 선택: 열차 1편 + 좌석맵에서 고른 자리
      if (!fixed || !fixed.train) { notify('열차를 선택하세요'); return null; }
      base.fixed_train_no = fixed.train.no;
      base.train_times = _depTimesOf([fixed.train.no]);
      base.train_rows = _rowsOf([fixed.train.no]);
      const chosen = (fixed.seats || []).map(x => ({ car_no: fixed.car_no, seat_no: x.no, label: x.label }));
      if (chosen.length && chosen.length !== need) { notify(`인원(${need}명)만큼 좌석을 고르거나, 좌석 선택을 모두 해제하세요 (현재 ${chosen.length}석)`); return null; }
      if (chosen.length === need) { base.seat_mode = 'fixed'; base.fixed_seats = chosen; }
    } else if (seatMode() === 'cand') {
      // 선호 좌석: 선택한 열차들의 그룹별 템플릿을 모아 보낸다(서버가 열차 편성에 맞는 걸 쓴다).
      // '직접 선택'에서 남은 fixed.train을 쓰면 안 된다 — 시작 버튼은 selTrains로 판정하는데 여기서 다른 열차를
      // 보내 시작이 조용히 실패하거나 엉뚱한 열차로 감시가 시작됐다(QA 하네스 2026-09-10 발견).
      const nos = selTrains;
      const tmap = {}; let first = null;
      nos.forEach(no => { const t = _candTemplateFor(no);
        if (t && (t.seats || []).length && t.groupId) { tmap[t.groupId] = t.seats.slice(0, 10); if (!first) first = t; } });
      if (!first) { notify('선호 좌석이 등록된 열차가 없습니다. 설정 › 선호 좌석 설정에서 먼저 등록하세요'); return null; }
      base.seat_mode = 'cand';
      base.cand_seats = first.seats.slice(0, 10);   // 호환용(단일)
      base.cand_templates = tmap;                    // 그룹별
      base.cand_fallback = 'auto';   // 실패 시 선호 옵션 조건으로 예매('후보가 전부 없을 때' 섹션 제거, 2026-09-10)
      base.cand_label = first.name || '';
    } else {
      base.seat_mode = 'pref';   // 긴급(아무 좌석) = 조건에 맞는 자리 자동 배정
    }
    if (isOpenRun()) {
      if (startMode === 'at') {
        const at = _schedAt();
        if (!at) { notify('예약 시작 일시를 설정하세요 (시계 버튼)'); return null; }
        if (at.getTime() - Date.now() < 10000) { notify('예약 시작 시각이 이미 지났습니다. 시계 버튼에서 수정하세요'); return null; }
        base.start_at = startAtDate + 'T' + startAtTime + ':00'; base.open_burst = true;
      }
    }
    const pay = lsGetPay();
    const autoPay = runF.getValue() === 'pay';
    const method = _payMethodOf(pay);
    // N카드는 조건(구간·유효기간·1명)이 맞을 때 자동으로 붙는다
    const ncOk = _ncardActive();
    base.use_ncard = !!ncOk; base.ncard_no = ncOk ? (ncOk.no || pay.ncardNo || '') : '';
    if (autoPay && !ktxPayConfigured()) { notify('결제 정보를 먼저 등록해주세요'); _ktxPayModal(syncSetup); return null; }
    const pin = (pay.payPin || '').trim();
    if (autoPay && method === 'card' && !_cardReady(pay.card)) { notify('신용카드 정보를 결제 정보에서 먼저 입력하세요'); _ktxPayModal(syncSetup); return null; }
    const useBiz = pay.receiptType === 'business' && pay.bizIdKind === 'biz';
    const numVal = (useBiz ? pay.bizNo : pay.phone) || '';
    const pc = (method === 'card' ? (pay.card || {}) : {});
    return Object.assign(base, {
      allow_waiting: waitCb.checked,
      allow_standing: standingCb.checked,
      member_no: acct.memberNo || '', password: acct.password || '', pay_pin: pin,
      auto_pay: autoPay, pay_dryrun: false, pay_method: method,
      cash_receipt: !!pay.cashReceipt, receipt_type: pay.receiptType || 'personal',
      phone: numVal.replace(/\D/g, ''), smart_ticket: pay.smartTicket !== false,
      use_mileage: false,   // 간편현금결제 전용 옵션 — 화면에서 뺐다
      card_no: pc.cardNo || '', card_exp: pc.cardExp || '', card_pw: pc.cardPw || '',
      card_auth_kind: pc.cardAuthKind || 'J', card_auth_val: pc.cardAuthVal || '',
      card_installment: Number(pc.cardInstallment || 0),
    });
  }
  // 감시는 여러 개를 동시에 돌릴 수 있다 — 시작할 때마다 새 작업(job)으로 등록한다
  async function onStart() {
    const body = getStartBody(); if (!body) return;
    const job = 'j' + Date.now().toString(36);
    body.label = `${depF.getValue()}→${arrF.getValue()} ${_fmtDateK((dateF.getValue() || '').replace(/-/g, ''))}`;
    btnStart.disabled = true;
    try {
      const res = await api('POST', `/start?mode=${job}`, body);
      if (!res.ok) { notify('시작 실패: ' + res.error); btnStart.disabled = false; return; }
      _lastJob = job; _jobBodies[job] = body; window.__startedAt = Date.now();
      _running = false; _refreshStart(); btnStart.disabled = false;
      if (typeof _loadResults === 'function') _loadResults();
      switchTab('results');
    } catch (e) { notify('시작 실패: ' + e.message); btnStart.disabled = false; }
  }
  _refreshStart();
  if (window.__DEMO__) {   // 데모 전용 테스트 훅(QA 하네스가 옵션→요청 매핑을 검증). 운영 화면엔 노출되지 않는다.
    window.__T = {
      getStartBody, blockReason: _startBlockReason, isOpenRun, seatMode, isManual, isMulti,
      get selTrains() { return selTrains.slice(); }, set selTrains(v) { selTrains = v.slice(); },
      get fixed() { return fixed; }, get startMode() { return startMode; }, get startAt() { return startAtDate + 'T' + startAtTime; },
      get schedManual() { return schedManual; },
      fields: { dateF, hourF, adultF, seatF, ivlF, sideF, dirF, runF, seatModeF, depF, arrF }, waitCb, standingCb,
      btnStart, schedBtn, timeRange, renderTrainSel, invalidateTrains: _invalidateTrains, syncSections, refreshStart: _refreshStart,
      candTemplateFor: _candTemplateFor, tcFind: _tcFind, trainKey: _trainKey, save, candPv,
    };
  }
}

function buildPage() {
  const pill = makeStatusPill();
  _headerPill = pill;
  const headerWrap = div('app-header');
  headerWrap.style.cssText = 'position:absolute;top:0;left:0;right:0;height:44px;display:flex;align-items:center;z-index:10;background:var(--bg)';
  const inner = div('app-header-inner');
  // 좌우 여백 0 — 헤더 줄의 바깥 상자가 이미 카드와 같은 위치라, 여기서 더 주면
  // 종 버튼이 카드보다 14px 안쪽으로 들어가 오른쪽이 비어 보인다(2026-09-09 실측).
  inner.style.cssText = 'display:flex;align-items:center;justify-content:space-between;width:100%;padding:0';
  const titleEl = txt('div', 'app-header-title', _TAB_TITLES[_currentTab]);
  _headerTitle = titleEl;
  const right = div(''); right.classList.add('u-row8');
  const bellBtn = el('button', 'hdr-acct'); bellBtn.type = 'button'; bellBtn.title = '알림';
  // width/height를 안 주면 모바일 사파리에서 크기가 잡히지 않아 아이콘이 사라진다
  bellBtn.innerHTML = '<svg width="19" height="19" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">'
    + '<path d="M12 2.6a1.15 1.15 0 0 1 1.15 1.15v.7a5.9 5.9 0 0 1 4.75 5.79v3.1l1.3 2.26a1 1 0 0 1-.87 1.5H5.67a1 1 0 0 1-.87-1.5l1.3-2.26v-3.1A5.9 5.9 0 0 1 10.85 4.45v-.7A1.15 1.15 0 0 1 12 2.6Z"/>'
    + '<path d="M9.9 19.1h4.2a2.1 2.1 0 0 1-4.2 0Z"/></svg>';
  const bellDot = span('hdr-dot'); bellDot.style.display = 'none'; bellBtn.appendChild(bellDot);
  bellBtn.addEventListener('click', () => _alertsSheet(() => _refreshAlertBadge()));
  _alertBadge = bellDot;
  right.append(pill.el, bellBtn);
  inner.append(titleEl, right);
  headerWrap.appendChild(inner);
  document.getElementById('header-mount').appendChild(headerWrap);

  _buildModePanel(document.getElementById('book-panel'), {
    mode: 'book', defaultDep: '서울', defaultArr: '부산', tabId: 'book',
    apiStart: '/start', apiStop: '/stop', apiStatus: '/status',
  });
}

// 우상단 계정 버튼 → 계정/결제 시트(어느 탭에서든 조회·수정)
function _accountSheet(onChange) {
  const ov = div('picker-overlay'); const sheet = div('picker-sheet'); sheet.classList.add('u-sheet-pad');
  sheet.appendChild(txt('div', 'wheel-title', '계정 · 결제'));
  const row = _makeAccountRow(() => { onChange && onChange(); });
  sheet.appendChild(row.el);
  const bar = div('preset-actions'); bar.style.marginTop = '12px';
  const close = el('button', 'btn-primary'); close.textContent = '닫기'; close.classList.add('btn-ghost'); close.addEventListener('click', () => document.body.removeChild(ov));
  bar.appendChild(close); sheet.appendChild(bar);
  ov.appendChild(sheet); ov.addEventListener('click', e => { if (e.target === ov) document.body.removeChild(ov); }); document.body.appendChild(ov);
}


// ── Ddoc panel ───────────────────────────────────────────────


function _usageRow(card, label, desc, warn) {
  if (card.children.length > 0) card.appendChild(div('grid-divider-h'));
  const cell = div('grid-cell-stack');
  cell.appendChild(txt('div', 'grid-cell-label', label));
  cell.appendChild(txt('div', 'usage-desc', desc));
  if (warn) cell.appendChild(txt('div', 'usage-warn', warn));   // 보안 경고: 줄바꿈 + 붉은색
  card.appendChild(cell);
}
function buildUsagePage() {
  const panel = document.getElementById('usage-panel');
  panel.appendChild(div('sp10'));
  panel.appendChild(txt('div', 'group-header', '기차놀이 사용법'));
  const c1 = makeGridCard();
  _usageRow(c1, '회원번호', '코레일 회원번호·휴대폰번호·이메일 중 하나를 입력하세요. 휴대폰번호는 하이픈 없이 적어도 됩니다.');
  _usageRow(c1, '비밀번호', '코레일 비밀번호를 입력하세요.', '⚠ 보안을 위해 다른 곳에서 쓰지 않는 비밀번호로 변경 후 사용하세요.');
  _usageRow(c1, '간편현금결제 PIN', '코레일 마이페이지 → 결제관리 → 간편현금결제 설정에서 계좌 연결 후 PIN 6자리를 설정하세요.', '⚠ 보안을 위해 다른 곳에서 쓰지 않는 PIN 번호를 입력해주세요.');
  panel.appendChild(c1);
  panel.appendChild(div('sp140'));
}
// ── 계정/결제 저장 모델 (서비스당 1벌, 설정 탭에서 관리) ──
// ── KTX 계정: 단일 계정(로그인 + 결제정보 pay 한 세트). kor_ktx_account = {nick,memberNo,password,pay}.
function _acctLabel(a) { return (a && a.memberNo) ? (a.nick ? `${a.nick} · ${a.memberNo}` : a.memberNo) : ''; }
function lsGetAccount(svc) { try { return JSON.parse(localStorage.getItem('kor_' + svc + '_account') || 'null') || {}; } catch { return {}; } }
function lsSetAccount(svc, o) { const cur = lsGetAccount(svc); try { localStorage.setItem('kor_' + svc + '_account', JSON.stringify(Object.assign({}, cur, o))); } catch {} if (typeof syncPush === 'function') syncPush(); }
// 결제정보는 계정 레코드의 pay 한 세트(계정마다 PIN·영수증이 다르므로). 계정이 없을 때만 레거시 kor_ktx_pay.
function _lsLegacyPay() { try { return JSON.parse(localStorage.getItem('kor_ktx_pay') || 'null') || {}; } catch { return {}; } }
function lsGetPay() { const a = lsGetAccount('ktx'); return a.pay || _lsLegacyPay(); }
// 좌석 선호 옵션의 기본값 — 설정 탭에서 정하고, 예매 탭이 이 값으로 시작한다(계정 단위로 동기화)
const _SEATPREF_DEF = { seatClass: 'general_first', seatSide: 'window', seatDir: 'fwd', allowWaiting: false, allowStanding: false };
function lsGetSeatPref() {
  let o = {};
  try { o = JSON.parse(localStorage.getItem('kor_ktx_seatpref') || '{}') || {}; } catch {}
  const v = Object.assign({}, _SEATPREF_DEF, o);
  if (!_SEAT_OPTS.some(x => x[0] === v.seatClass)) v.seatClass = _SEATPREF_DEF.seatClass;
  if (!_SIDE_OPTS.some(x => x[0] === v.seatSide)) v.seatSide = _SEATPREF_DEF.seatSide;
  if (!_DIR_OPTS.some(x => x[0] === v.seatDir)) v.seatDir = _SEATPREF_DEF.seatDir;
  v.allowWaiting = !!v.allowWaiting; v.allowStanding = !!v.allowStanding;
  return v;
}
function lsSetSeatPref(o) {
  const v = Object.assign(lsGetSeatPref(), o || {});
  try { localStorage.setItem('kor_ktx_seatpref', JSON.stringify(v)); } catch {}
  if (typeof syncPush === 'function') syncPush();
  document.dispatchEvent(new Event('ktx-seatpref'));   // 열려 있는 예매 탭에 바로 반영
  return v;
}
function payReady() {
  const p = lsGetPay() || {};
  if (_payMethodOf(p) === 'card') return _cardReady(p.card);  // 신용카드는 카드 정보가 다 있어야 한다
  return true;                                                // 토스는 코레일에 등록된 키를 그대로 쓴다
}
// 설정 탭에 붉은 점을 깜빡여 '볼 게 있다'를 알린다.
// 사유가 둘(결제 미입력·업데이트 준비)이라 사유별로 기록하고 하나라도 켜지면 표시한다.
const _attnReasons = new Set();
function _setAttn(reason, on) {
  if (on) _attnReasons.add(reason); else _attnReasons.delete(reason);
  const t = document.getElementById('tab-settings');
  if (t) t.classList.toggle('needs-attn', _attnReasons.size > 0);
}
function markSettingsAttention(on) { _setAttn('pay', on); }
function lsSetPay(o) { lsSetAccount('ktx', { pay: Object.assign({}, lsGetAccount('ktx').pay, o) }); document.dispatchEvent(new Event('ktx-acct-sync')); }
function lsGetReceipt() { try { return JSON.parse(localStorage.getItem('kor_receipt') || 'null') || {}; } catch { return {}; } }   // 마이그레이션용
// 코레일 계정 지우기 → 첫 로그인 화면. 빈 값을 서버에도 올려야 다른 기기에서 되살아나지 않는다.
// 로그인 ID 형식(서버 login_id_kind 와 같은 규칙) — 형식이 아니면 코레일에 시도조차 하지 않는다.
// 헛시도는 계정 잠금 위험만 키운다(2026-09-10: 2회 실패로 10분 차단됨).
function _loginIdKind(v) {
  v = String(v || '').trim();
  if (!v) return '';
  if (v.indexOf('@') >= 0) return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v) ? 'email' : '';
  if (/[^\d\s-]/.test(v)) return '';
  const d = v.replace(/\D/g, '');
  if (/^01/.test(d) && (d.length === 10 || d.length === 11)) return 'phone';
  if (d.length >= 8 && d.length <= 11) return 'member';
  return '';
}

function ktxLogout() {
  try {
    localStorage.setItem('kor_ktx_account', '{}');
    localStorage.removeItem('kor_ktx_pay');
    localStorage.removeItem('kor_setup_done');
    const ts = _syncTsLoad(); ts['kor_ktx_account'] = Date.now(); _syncTsSave(ts);
    const hs = _syncHashLoad(); delete hs['kor_ktx_account']; _syncHashSave(hs);
  } catch {}
  if (typeof syncPush === 'function') syncPush();
  document.querySelectorAll('.picker-overlay').forEach(e => e.remove());
  showOnboarding(() => { showSetup(() => location.reload()); });
}

function ktxAccountReady() { const a = lsGetAccount('ktx'); return !!(a.memberNo && String(a.memberNo).trim() && a.password); }
function ktxPayConfigured() { const p = lsGetPay(); return !!(p && p.method); }
// 간편현금결제는 화면에서 뺀다(2026-09-09 지시) — 크롬 자동화(pay.py)가 필요해 단독 실행본에선
// 동작하지 않고, 딸린 설정(PIN·스마트티켓·마일리지·현금영수증)도 그 결제 단계에서만 쓰인다.
const _PAY_METHODS = [['toss','토스페이'],['card','신용카드']];
// 예전에 간편현금으로 저장해 둔 계정은 토스페이로 읽는다
function _payMethodOf(p) { return (p && p.method) === 'card' ? 'card' : 'toss'; }
function _payMethodLabel() {
  const p = lsGetPay(); const m = _payMethodOf(p);
  const hit = _PAY_METHODS.find(x => x[0] === m);
  const base = hit ? hit[1] : '토스페이';
  if (m !== 'card') return base;
  const no = String(((p.card || {}).cardNo) || '').replace(/\D/g, '');   // 어느 카드인지 끝 4자리로 알아본다
  return no.length >= 4 ? `${base}(${no.slice(-4)})` : base;
}

// 입력 모달 셸 (하단 취소/저장 — 저장 시 즉시 DB 반영)
function _editModal(title, buildBody, onSave, opts) {
  opts = opts || {};
  const ov = div('picker-overlay'); const sheet = div('picker-sheet'); sheet.classList.add('u-sheet');
  sheet.appendChild(txt('div', 'wheel-title', title));
  const body = div(''); buildBody(body); sheet.appendChild(body);
  const bar = div('preset-actions'); bar.style.marginTop = '16px';
  const cancel = el('button', 'btn-primary'); cancel.textContent = '취소'; cancel.classList.add('btn-ghost');
  cancel.addEventListener('click', () => document.body.removeChild(ov));
  const save = el('button', 'btn-primary blue'); save.textContent = '저장';
  save.addEventListener('click', () => { if (onSave() === false) return; document.body.removeChild(ov); });
  bar.append(cancel, save); sheet.appendChild(bar);
  ov.appendChild(sheet);
  ov.addEventListener('click', async e => {   // 바깥 클릭: confirmDiscard가 있으면 되묻는다(선호좌석 팝업)
    if (e.target !== ov) return;
    if (opts.confirmDiscard) {
      const msg = typeof opts.confirmDiscard === 'function' ? opts.confirmDiscard() : opts.confirmDiscard;
      if (msg && !(await askConfirm(msg, sheet))) return;   // 실제 변경이 없으면 msg가 빈값 → 바로 닫힘
    }
    if (ov.parentNode) document.body.removeChild(ov);
  });
  document.body.appendChild(ov);
}

// 결제 정보 입력 블록(결제 수단·PIN·발권/할인·현금영수증) — 계정 수정 모달 안에서 사용
function _payFields(p) {
  const wrap = div('');
  const card = makeGridCard();
  const methodF = makeSegmentedField(_PAY_METHODS, _payMethodOf(p), () => syncMethod());
  methodF.el.classList.add('seg-tight'); methodF.el.style.width = '190px';
  appendGridFull(card, '결제 수단', methodF.el);
  const pinInp = makePwInput(p.payPin || '', '숫자 6자리', true, 6);
  pinInp.addEventListener('input', () => { pinInp.value = pinInp.value.replace(/\D/g, '').slice(0, 6); });
  const pinRow = div('grid-cell-full'); const pinAct = div('grid-cell-action'); pinAct.appendChild(pinInp); pinRow.append(txt('div','grid-cell-label','간편결제 PIN'), pinAct);
  wrap.appendChild(card);
  // 안내 문구는 결제 수단 바로 아래, PIN 은 그 아래
  const mNote = _payMethodNote(() => methodF.getValue(), () => /^\d{6}$/.test(pinInp.value.trim()));
  wrap.appendChild(mNote.el);
  // 구역마다 컨테이너 — 숨길 때 뒤따르는 sp10 여백까지 같이 사라져야 빈 공간이 안 남는다
  const pinSec = div(''); const pinCard = makeGridCard(); pinCard.appendChild(pinRow);
  pinSec.append(pinCard, div('sp10')); wrap.appendChild(pinSec);
  pinInp.addEventListener('input', () => mNote.paint());
  const cardSec = div(''); wrap.appendChild(cardSec);
  const cardLabel = txt('div', 'sub-label', '카드 정보'); cardLabel.classList.add('u-pb4'); cardSec.appendChild(cardLabel);
  const cardBlk = _cardSubcard(p); cardSec.append(cardBlk.el, div('sp10'));
  const optSec = div(''); wrap.appendChild(optSec);
  const optLabel = txt('div', 'sub-label', '발권 · 할인'); optLabel.classList.add('u-pb4'); optSec.appendChild(optLabel);
  const optCard = makeGridCard();
  const smartCb = makeCheckbox(p.smartTicket !== false);
  const smCell = div('grid-cell-full'); const smAct = div('grid-cell-action'); smAct.appendChild(smartCb); smCell.append(txt('div','grid-cell-label','스마트티켓 발권'), smAct); optCard.appendChild(smCell);
  const milCb = makeCheckbox(p.useMileage === true);
  optCard.appendChild(div('grid-divider-h'));
  const milRowEl = div('grid-cell-full'); const milAct = div('grid-cell-action'); milAct.appendChild(milCb); milRowEl.append(txt('div','grid-cell-label','마일리지 전액 사용'), milAct); optCard.appendChild(milRowEl);
  optSec.append(optCard, div('sp10'));
  const rcptSec = div(''); wrap.appendChild(rcptSec);
  const rcptLabel = txt('div', 'sub-label', '현금영수증'); rcptLabel.classList.add('u-pb4'); rcptSec.appendChild(rcptLabel);
  const receipt = _receiptSubcard(p); rcptSec.appendChild(receipt.el);
  // 할인 — N카드 번호(있으면 각 탭의 'N카드 할인 적용' 토글이 켜짐). 1인용 개인 N카드 기준.
  wrap.appendChild(div('sp10'));
  const dcLabel = txt('div', 'sub-label', '할인'); dcLabel.classList.add('u-pb4'); wrap.appendChild(dcLabel);
  const dcCard = makeGridCard();
  const ncardInp = makeTextInput(p.ncardNo || '', 180, 'N카드 번호(숫자만)'); ncardInp.inputMode = 'numeric';
  ncardInp.addEventListener('input', () => { ncardInp.value = ncardInp.value.replace(/\D/g, '').slice(0, 20); });
  appendGridFull(dcCard, 'N카드 번호', ncardInp); wrap.appendChild(dcCard);
  const dcHint = txt('div', 'hint', '코레일 앱 마이페이지 → 할인카드에서 확인. 1명 예매에만 적용되며, 구간·시간대가 카드 조건에 맞아야 코레일이 할인 예약을 받아줍니다.'); dcHint.classList.add('u-pad-hint'); wrap.appendChild(dcHint);
  function syncMethod() {
    const m = methodF.getValue();
    [pinSec, optSec, rcptSec].forEach(e => e.style.display = 'none');
    cardSec.style.display = m === 'card' ? '' : 'none';
    mNote.paint();
    // 안내 문구 아래에 남는 구역이 없으면 아래 여백을 없앤다(섹션 끝에서 과하게 벌어지는 문제)
    mNote.el.classList.toggle('is-last', m !== 'card');
  }
  syncMethod();
  return { el: wrap, collect: () => { const r = receipt.collect(); return { method: methodF.getValue(), payPin: pinInp.value.trim(), smartTicket: smartCb.checked, useMileage: milCb.checked, cashReceipt: r.cashReceipt, receiptType: r.receiptType, bizIdKind: r.bizIdKind, phone: r.phone, bizNo: r.bizNo, ncardNo: ncardInp.value.trim(), card: cardBlk.collect() }; } };
}

// 계정 모달 — 로그인 정보(닉네임/회원번호/비밀번호). 결제 정보는 '결제 수단' 행의 _ktxPayModal에서(같은 계정 레코드의 pay에 저장).
function _ktxAccountModal(onSaved) {
  const a = lsGetAccount('ktx'); let memInp, pwInp;
  _editModal('KTX 계정', (body) => {
    const card = makeGridCard();
    memInp = makeTextInput(a.memberNo || '', 150, '회원번호 또는 휴대폰번호');

    pwInp = makePwInput(a.password || '', '비밀번호');
    appendGridFull(card, '회원번호/휴대폰', memInp); appendGridFull(card, '비밀번호', pwInp);
    body.appendChild(card);
  }, () => {
    const memberNo = memInp.value.trim();
    if (!memberNo) { notify('회원번호 또는 휴대폰번호를 입력하세요'); return false; }
    if (!_loginIdKind(memberNo)) { notify('회원번호 또는 휴대폰번호 형식이 아닙니다'); return false; }
    lsSetAccount('ktx', { memberNo, password: pwInp.value });
    onSaved && onSaved();
  });
}

// 현금영수증 서브카드 (결제 모달 안)
// 신용카드 직접결제 입력 — 필드는 코레일 웹 카드결제가 보내는 값 그대로다(2026-09-09 확인).
//   카드번호 hidStlCrCrdNo1 · 유효기간 hidCrdVlidTrm1(YYMM) · 비밀번호 앞2자리 hidVanPwd1
//   인증구분 hidAthnDvCd1 J=개인(생년월일 6자리) / S=법인(사업자번호 10자리) · 인증값 hidAthnVal1
//   할부 hidIsmtMnthNum1 (0=일시불, 5만원 미만은 할부 불가)
const _CARD_INSTALLMENTS = [[0,'일시불'],[2,'2개월'],[3,'3개월'],[4,'4개월'],[5,'5개월'],[6,'6개월'],[10,'10개월'],[12,'12개월']];

// 결제 수단 안내 — 토스페이는 코레일에 등록된 키를 쓰므로 등록 여부를 조회해 알려준다
const _PAY_NOTE_CASH_HTML =
  '계좌 1회 등록으로 현금 결제를 할 수 있는 서비스입니다'
  + '<span class="pay-note-sub">(<a href="https://www.korail.com" target="_blank" rel="noopener">코레일 사이트</a>'
  + ' - 마이페이지 - 결제관리 - 간편현금결제 설정)</span>';
const _PAY_NOTE_TOSS_OK = '토스페이가 등록되어있습니다';
const _PAY_NOTE_TOSS_NONE =
  '최초 1회 등록이 필요합니다.<br>토스페이로 1회 결제 해주세요.'
  + '<span class="pay-note-sub">(코레일 어플 - 승차권 결제 - 간편결제 - 토스페이)</span>';

// 이 서버가 간편현금결제 자동결제를 할 수 있는지(단독 실행본에는 크롬 자동화가 없어 불가)
let _capCache = null;
function _caps() {
  if (_capCache) return _capCache;
  _capCache = fetch('/api/me').then(r => r.json()).catch(() => ({}));
  return _capCache;
}

let _tossKeyCache = null;   // null=미조회, true/false=조회됨
function _tossKeyState(force) {
  if (_tossKeyCache !== null && !force) return Promise.resolve(_tossKeyCache);
  const a = lsGetAccount('ktx');
  if (!a.memberNo || !a.password) return Promise.resolve(null);
  return api('POST', '/paykeys', { member_no: a.memberNo, password: a.password })
    .then(r => { _tossKeyCache = (r && r.ok) ? !!r.toss : null; return _tossKeyCache; })
    .catch(() => null);
}

// 결제 수단 행 아래에 붙는 안내 한 덩어리. getValue()로 현재 수단을 읽어 갱신한다.
// hasPin(): 간편결제 PIN이 이미 저장돼 있으면 간편현금 안내는 띄우지 않는다
function _payMethodNote(getMethod, hasPin) {
  const el0 = div('pay-note note-line');
  function set(cls, html) { el0.className = 'pay-note ' + cls; el0.innerHTML = html; el0.style.display = ''; }
  function paint() {
    const m = getMethod();
    if (m !== 'toss') { el0.style.display = 'none'; return; }
    set('note-line muted', '토스페이 등록 여부 확인 중…');
    _tossKeyState().then(ok => {
      if (getMethod() !== 'toss') return;          // 그 사이 수단을 바꿨으면 버린다
      if (ok === null) { set('warn-line', '토스페이 등록 여부를 확인하지 못했습니다'); return; }
      set(ok ? 'note-line' : 'warn-line', ok ? _PAY_NOTE_TOSS_OK : _PAY_NOTE_TOSS_NONE);
    });
  }
  return { el: el0, paint };
}

function _cardSubcard(p) {
  const c = p.card || {};
  const wrap = div('');
  const card = makeGridCard();

  // 0000-0000-0000-0000 형식. 네 자리마다 '-'가 저절로 붙고, 지울 때는 숫자가 지워진다
  // (뒤에 하이픈을 남기지 않아 백스페이스가 하이픈에 걸리지 않는다)
  const _fmtCard = v => String(v || '').replace(/\D/g, '').slice(0, 16).replace(/(\d{4})(?=\d)/g, '$1-');
  const noInp = makeTextInput(_fmtCard(c.cardNo), 180, '0000-0000-0000-0000'); noInp.inputMode = 'numeric';
  noInp.addEventListener('input', () => {
    const before = noInp.value.slice(0, noInp.selectionStart || 0).replace(/\D/g, '').length;   // 커서 앞 숫자 개수
    noInp.value = _fmtCard(noInp.value);
    let pos = 0, seen = 0;                       // 그만큼 지난 자리로 커서를 되돌린다
    while (pos < noInp.value.length && seen < before) { if (/\d/.test(noInp.value[pos])) seen++; pos++; }
    try { noInp.setSelectionRange(pos, pos); } catch {}
  });
  appendGridFull(card, '카드번호', noInp);

  // 저장은 코레일이 받는 YYMM, 표시는 익숙한 MM/YY
  const expInp = makeTextInput(_expToView(c.cardExp || ''), 90, 'MM/YY'); expInp.inputMode = 'numeric';
  expInp.addEventListener('input', () => {
    const d = expInp.value.replace(/\D/g, '').slice(0, 4);
    expInp.value = d.length > 2 ? d.slice(0, 2) + '/' + d.slice(2) : d;
  });
  const dvE = div('grid-divider-h'); const expCell = div('grid-cell-full'); const expAct = div('grid-cell-action');
  expAct.appendChild(expInp); expCell.append(txt('div','grid-cell-label','유효기간'), expAct);

  const pwInp = makePwInput(c.cardPw || '', '앞 2자리', true, 2);
  pwInp.addEventListener('input', () => { pwInp.value = pwInp.value.replace(/\D/g, '').slice(0, 2); });
  const dvP = div('grid-divider-h'); const pwCell = div('grid-cell-full'); const pwAct = div('grid-cell-action');
  pwAct.appendChild(pwInp); pwCell.append(txt('div','grid-cell-label','카드 비밀번호'), pwAct);

  const kindF = makeSegmentedField([['J','개인'],['S','법인']], c.cardAuthKind || 'J', () => syncKind());
  kindF.el.classList.add('seg-tight'); kindF.el.style.width = '140px';
  const dvK = div('grid-divider-h'); const kindCell = div('grid-cell-full'); const kindAct = div('grid-cell-action');
  kindAct.appendChild(kindF.el); kindCell.append(txt('div','grid-cell-label','카드 종류'), kindAct);

  const authInp = makePwInput(c.cardAuthVal || '', '숫자 6자리', true, 10);
  authInp.addEventListener('input', () => {
    const max = kindF.getValue() === 'S' ? 10 : 6;
    authInp.value = authInp.value.replace(/\D/g, '').slice(0, max);
  });
  const dvA = div('grid-divider-h'); const authCell = div('grid-cell-full'); const authAct = div('grid-cell-action');
  const authLabel = txt('div','grid-cell-label','생년월일');
  authAct.appendChild(authInp); authCell.append(authLabel, authAct);

  const ismtF = makeSheetPicker(_CARD_INSTALLMENTS, Number(c.cardInstallment || 0), () => {});
  const dvI = div('grid-divider-h'); const ismtCell = div('grid-cell-full'); const ismtAct = div('grid-cell-action');
  ismtAct.appendChild(ismtF.el); ismtCell.append(txt('div','grid-cell-label','할부'), ismtAct);

  card.append(dvE, expCell, dvP, pwCell, dvK, kindCell, dvA, authCell, dvI, ismtCell);
  wrap.appendChild(card);
  const hint = txt('div', 'hint', '카드번호·유효기간·비밀번호·생년월일은 이 기기에만 저장되고 다른 기기로 동기화되지 않습니다. 할부는 5만원 이상부터 선택할 수 있습니다.');
  hint.classList.add('u-pad-hint'); wrap.appendChild(hint);

  function syncKind() {
    const biz = kindF.getValue() === 'S';
    authLabel.textContent = biz ? '사업자번호' : '생년월일';
    authInp.placeholder = biz ? '숫자 10자리' : '숫자 6자리';
    authInp.value = authInp.value.replace(/\D/g, '').slice(0, biz ? 10 : 6);
  }
  syncKind();

  return { el: wrap, collect: () => ({
    cardNo: _fmtCard(noInp.value),   // 0000-0000-0000-0000 로 저장(전송할 때 숫자만 남긴다)
    cardExp: _expToStore(expInp.value),
    cardPw: pwInp.value.replace(/\D/g, ''),
    cardAuthKind: kindF.getValue(),
    cardAuthVal: authInp.value.replace(/\D/g, ''),
    cardInstallment: Number(ismtF.getValue() || 0),
  }) };
}

function _expToView(yymm) { const d = String(yymm || '').replace(/\D/g, ''); return d.length === 4 ? d.slice(2) + '/' + d.slice(0, 2) : ''; }
function _expToStore(mmyy) { const d = String(mmyy || '').replace(/\D/g, ''); return d.length === 4 ? d.slice(2) + d.slice(0, 2) : ''; }

// 카드 입력이 결제 가능한 상태인지 — 서버 card_check와 같은 규칙
function _cardReady(c) {
  if (!c) return false;
  const no = String(c.cardNo || '').replace(/\D/g, '');
  const exp = String(c.cardExp || '').replace(/\D/g, '');
  const pw = String(c.cardPw || '').replace(/\D/g, '');
  const val = String(c.cardAuthVal || '').replace(/\D/g, '');
  const need = c.cardAuthKind === 'S' ? 10 : 6;
  return no.length >= 12 && exp.length === 4 && +exp.slice(2) >= 1 && +exp.slice(2) <= 12
      && pw.length === 2 && val.length === need;
}

function _receiptSubcard(p) {
  const wrap = div('');
  const card = makeGridCard();
  const phoneInp = makeTextInput(p.phone || '', 150, '숫자만 입력');
  phoneInp.addEventListener('input', () => { const d = phoneInp.value.replace(/\D/g, '').slice(0, 11); phoneInp.value = d.length > 7 ? d.slice(0,3)+'-'+d.slice(3,7)+'-'+d.slice(7) : d.length > 3 ? d.slice(0,3)+'-'+d.slice(3) : d; });
  const bizInp = makeTextInput(p.bizNo || '', 150, '사업자등록번호');
  bizInp.addEventListener('input', () => { const d = bizInp.value.replace(/\D/g, '').slice(0, 10); bizInp.value = d.length > 5 ? d.slice(0,3)+'-'+d.slice(3,5)+'-'+d.slice(5) : d.length > 3 ? d.slice(0,3)+'-'+d.slice(3) : d; });
  function useBiz() { return rtypeF.getValue() === 'business' && idF.getValue() === 'biz'; }
  function syncNum() { const on = cashF.getValue() === 'yes', biz = rtypeF.getValue() === 'business', showId = on && biz; dvId.style.display = showId ? '' : 'none'; idCell.style.display = showId ? '' : 'none'; const ub = useBiz(); numLabel.textContent = ub ? '사업자등록번호' : '휴대폰번호'; numAction.innerHTML = ''; numAction.appendChild(ub ? bizInp : phoneInp); }
  const rtypeF = makeSegmentedField([['personal','개인'],['business','사업자']], p.receiptType || 'personal', () => syncNum()); rtypeF.el.classList.add('seg-tight'); rtypeF.el.style.width = '140px';
  const idF = makeSegmentedField([['biz','사업자번호'],['phone','휴대폰']], p.bizIdKind || 'biz', () => syncNum()); idF.el.classList.add('seg-tight'); idF.el.style.width = '150px';
  const cashF = makeSegmentedField([['yes','신청'],['no','미신청']], p.cashReceipt ? 'yes' : 'no', () => syncCash()); cashF.el.classList.add('seg-tight'); cashF.el.style.width = '140px';
  appendGridFull(card, '현금영수증 신청', cashF.el);
  const dv1 = div('grid-divider-h'); const rtCell = div('grid-cell-full'); const rtAct = div('grid-cell-action'); rtAct.appendChild(rtypeF.el); rtCell.append(txt('div','grid-cell-label','발급 구분'), rtAct);
  const dvId = div('grid-divider-h'); const idCell = div('grid-cell-full'); const idAct = div('grid-cell-action'); idAct.appendChild(idF.el); idCell.append(txt('div','grid-cell-label','식별 수단'), idAct);
  const dv2 = div('grid-divider-h'); const numCell = div('grid-cell-full'); const numLabel = txt('div','grid-cell-label','휴대폰번호'); const numAction = div('grid-cell-action'); numCell.append(numLabel, numAction);
  card.append(dv1, rtCell, dvId, idCell, dv2, numCell);
  const baseRows = [dv1, rtCell, dv2, numCell];
  function syncCash() { const sh = cashF.getValue() === 'yes'; baseRows.forEach(e => e.style.display = sh ? '' : 'none'); syncNum(); }
  syncCash(); wrap.appendChild(card);
  return { el: wrap, collect: () => ({ cashReceipt: cashF.getValue() === 'yes', receiptType: rtypeF.getValue(), bizIdKind: idF.getValue(), phone: phoneInp.value, bizNo: bizInp.value }) };
}

function _ktxPayModal(onSaved) {   // 결제 정보 모달 — 저장은 계정 레코드의 pay로(lsSetPay)
  const p = lsGetPay();
  _tossKeyCache = null;   // 코레일 앱에서 방금 등록하고 왔을 수 있으니 열 때마다 다시 본다
  let methodF, pinInp, smartCb, milCb, receipt, pinCard, pinRow, optLabel, optCard, rcptLabel, cardLabel, cardBlk, mNote,
      pinSec, cardSec, optSec, rcptSec;
  _editModal('결제 정보', (body) => {
    // ① 결제 — 결제 수단 + (현금결제 시) PIN
    const card = makeGridCard();
    methodF = makeSegmentedField(_PAY_METHODS, _payMethodOf(p), () => syncMethod());
    methodF.el.classList.add('seg-tight'); methodF.el.style.width = '190px';
    appendGridFull(card, '결제 수단', methodF.el);
    pinInp = makePwInput(p.payPin || '', '숫자 6자리', true, 6);
    pinInp.addEventListener('input', () => { pinInp.value = pinInp.value.replace(/\D/g, '').slice(0, 6); });
    pinRow = div('grid-cell-full'); const pinAct = div('grid-cell-action'); pinAct.appendChild(pinInp); pinRow.append(txt('div','grid-cell-label','간편결제 PIN'), pinAct);
    body.appendChild(card);
    mNote = _payMethodNote(() => methodF.getValue(), () => /^\d{6}$/.test(pinInp.value.trim()));
    body.appendChild(mNote.el);
    pinSec = div(''); pinCard = makeGridCard(); pinCard.appendChild(pinRow);
    pinSec.append(pinCard, div('sp10')); body.appendChild(pinSec);
    pinInp.addEventListener('input', () => mNote.paint());
    // ①-b 카드 정보 (신용카드 전용)
    cardSec = div(''); cardSec.appendChild(div('sp10')); body.appendChild(cardSec);
    cardLabel = txt('div', 'sub-label', '카드 정보'); cardLabel.classList.add('u-pb4'); cardSec.appendChild(cardLabel);
    cardBlk = _cardSubcard(p); cardSec.append(cardBlk.el, div('sp10'));
    // ② 발권 · 할인 (간편현금결제 전용)
    optSec = div(''); optSec.appendChild(div('sp10')); body.appendChild(optSec);
    optLabel = txt('div', 'sub-label', '발권 · 할인'); optLabel.classList.add('u-pb4'); optSec.appendChild(optLabel);
    optCard = makeGridCard();
    smartCb = makeCheckbox(p.smartTicket !== false);
    const smCell = div('grid-cell-full'); const smAct = div('grid-cell-action'); smAct.appendChild(smartCb); smCell.append(txt('div','grid-cell-label','스마트티켓 발권'), smAct); optCard.appendChild(smCell);
    milCb = makeCheckbox(p.useMileage === true);
    optCard.appendChild(div('grid-divider-h'));
    const milRowEl = div('grid-cell-full'); const milAct = div('grid-cell-action'); milAct.appendChild(milCb); milRowEl.append(txt('div','grid-cell-label','마일리지 전액 사용'), milAct); optCard.appendChild(milRowEl);
    optSec.append(optCard, div('sp10'));
    // ③ 현금영수증 (간편현금결제 전용)
    rcptSec = div(''); rcptSec.appendChild(div('sp10')); body.appendChild(rcptSec);
    rcptLabel = txt('div', 'sub-label', '현금영수증'); rcptLabel.classList.add('u-pb4'); rcptSec.appendChild(rcptLabel);
    receipt = _receiptSubcard(p); rcptSec.appendChild(receipt.el);
    function syncMethod() {
      const m = methodF.getValue();
      [pinSec, optSec, rcptSec].forEach(e => e.style.display = 'none');
      cardSec.style.display = m === 'card' ? '' : 'none';
      mNote.paint();
      mNote.el.classList.toggle('is-last', m !== 'card');
    }
    syncMethod();
  }, () => {
    const r = receipt.collect();
    lsSetPay({ method: methodF.getValue(), payPin: pinInp.value.trim(), smartTicket: smartCb.checked, useMileage: milCb.checked, cashReceipt: r.cashReceipt, receiptType: r.receiptType, bizIdKind: r.bizIdKind, phone: r.phone, bizNo: r.bizNo, card: cardBlk.collect() });
    onSaved && onSaved();
  });
}

function _buildKtxAccountCard() {
  const a = lsGetAccount('ktx');
  const card = makeGridCard();
  const memInp = makeTextInput(a.memberNo || '', 150, '회원번호 또는 휴대폰번호');
  const pwInp = makePwInput(a.password || '', '비밀번호');
  const pinInp = makePwInput(a.payPin || '', '(선택) 숫자 6자리', true, 6);
  const smartCb = makeCheckbox(a.smartTicket !== false);
  function save() { lsSetAccount('ktx', { memberNo: memInp.value.trim(), password: pwInp.value, payPin: pinInp.value.trim(), smartTicket: smartCb.checked }); }
  memInp.addEventListener('input', () => { memInp.value = memInp.value.replace(/\D/g, ''); save(); });
  pwInp.addEventListener('input', save);
  pinInp.addEventListener('input', () => { pinInp.value = pinInp.value.replace(/\D/g, '').slice(0, 6); save(); });
  smartCb.addEventListener('change', save);
  appendGridFull(card, '회원번호/휴대폰', memInp);
  appendGridFull(card, '비밀번호', pwInp);
  appendGridFull(card, '간편결제 PIN', pinInp);
  appendGridFull(card, '스마트티켓 발권', smartCb);
  return card;
}

function _buildReceiptCard() {
  const a = lsGetReceipt();
  const card = makeGridCard();
  const phoneInp = makeTextInput(a.phone || '', 150, '숫자만 입력');
  const bizInp = makeTextInput(a.bizNo || '', 150, '사업자등록번호');
  function save() { lsSetReceipt({ cashReceipt: cashF.getValue() === 'yes', receiptType: rtypeF.getValue(), bizIdKind: idF.getValue(), phone: phoneInp.value, bizNo: bizInp.value }); }
  phoneInp.addEventListener('input', () => { const d = phoneInp.value.replace(/\D/g, '').slice(0, 11); phoneInp.value = d.length > 7 ? d.slice(0,3)+'-'+d.slice(3,7)+'-'+d.slice(7) : d.length > 3 ? d.slice(0,3)+'-'+d.slice(3) : d; save(); });
  bizInp.addEventListener('input', () => { const d = bizInp.value.replace(/\D/g, '').slice(0, 10); bizInp.value = d.length > 5 ? d.slice(0,3)+'-'+d.slice(3,5)+'-'+d.slice(5) : d.length > 3 ? d.slice(0,3)+'-'+d.slice(3) : d; save(); });
  function _useBiz() { return rtypeF.getValue() === 'business' && idF.getValue() === 'biz'; }
  function syncNum() {
    const on = cashF.getValue() === 'yes', biz = rtypeF.getValue() === 'business', showId = on && biz;
    dvId.style.display = showId ? '' : 'none'; idCell.style.display = showId ? '' : 'none';
    const ub = _useBiz(); numLabel.textContent = ub ? '사업자등록번호' : '휴대폰번호';
    numAction.innerHTML = ''; numAction.appendChild(ub ? bizInp : phoneInp);
  }
  const rtypeF = makeSegmentedField([['personal','개인'],['business','사업자']], a.receiptType || 'personal', () => { syncNum(); save(); });
  rtypeF.el.classList.add('seg-tight'); rtypeF.el.style.width = '140px';
  const idF = makeSegmentedField([['biz','사업자번호'],['phone','휴대폰']], a.bizIdKind || 'biz', () => { syncNum(); save(); });
  idF.el.classList.add('seg-tight'); idF.el.style.width = '150px';
  const cashF = makeSegmentedField([['yes','신청'],['no','미신청']], a.cashReceipt ? 'yes' : 'no', () => { syncCash(); save(); });
  cashF.el.classList.add('seg-tight'); cashF.el.style.width = '140px';
  appendGridFull(card, '현금영수증 신청', cashF.el);
  const dv1 = div('grid-divider-h');
  const rtCell = div('grid-cell-full'); const rtAct = div('grid-cell-action'); rtAct.appendChild(rtypeF.el);
  rtCell.append(txt('div', 'grid-cell-label', '발급 구분'), rtAct);
  const dvId = div('grid-divider-h');
  const idCell = div('grid-cell-full'); const idAct = div('grid-cell-action'); idAct.appendChild(idF.el);
  idCell.append(txt('div', 'grid-cell-label', '식별 수단'), idAct);
  const dv2 = div('grid-divider-h');
  const numCell = div('grid-cell-full'); const numLabel = txt('div', 'grid-cell-label', '휴대폰번호'); const numAction = div('grid-cell-action');
  numCell.append(numLabel, numAction);
  card.append(dv1, rtCell, dvId, idCell, dv2, numCell);
  const _baseRows = [dv1, rtCell, dv2, numCell];
  function syncCash() { const sh = cashF.getValue() === 'yes'; _baseRows.forEach(e => e.style.display = sh ? '' : 'none'); syncNum(); }
  syncCash();
  return card;
}

// ── 내 티켓 탭 — 미결제 예약 + 발권 승차권 + N카드 (저장된 계정으로 모바일 API 조회) ─────
let _loadTickets = null;
function buildTicketsPage() {
  const panel = document.getElementById('tickets-panel');
  if (!panel) return;
  panel.innerHTML = '';
  const secR = div(''), secT = div(''), secN = div('');
  panel.append(secR, secN, secT);
  let loading = false, lastAt = 0, timer = null;
  function empty(sec, title, msg) { sec.innerHTML = ''; sec.appendChild(txt('div', 'group-header', title)); emptyBox(msg, sec); sec.appendChild(div('sp18')); }
  // 코레일 앱 '나의 티켓'과 같은 형태: 파란 날짜 헤더 + 흰 본문(구간·시간 크게) + 호차/좌석
  function _dotDate(ymd) { return /^\d{8}$/.test(ymd || '') ? `${ymd.slice(0,4)}.${ymd.slice(4,6)}.${ymd.slice(6,8)} (${_WD[new Date(+ymd.slice(0,4), +ymd.slice(4,6) - 1, +ymd.slice(6,8)).getDay()]})` : (ymd || ''); }
  function _daysTo(ymd) {
    if (!/^\d{8}$/.test(ymd || '')) return null;
    const t = new Date(), a = new Date(+ymd.slice(0,4), +ymd.slice(4,6) - 1, +ymd.slice(6,8));
    return Math.round((a - new Date(t.getFullYear(), t.getMonth(), t.getDate())) / 86400000);
  }
  // '결제기한 9/15(화) 23:59  (D-6)' / 하루 미만이면 '(23시간 50분 남음)'
  function _fmtWhen2(d) {
    return `${d.getMonth() + 1}/${d.getDate()}(${_WD[d.getDay()]}) `
         + `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  }
  // 결제 시작일이 따로 있는 예약(명절 사전 예약 등) — 아직 시작 전이면 범위로 알린다
  function _payPeriod(start, lim) {
    if (!start || !lim) return '';
    return `결제기간 ${_fmtWhen2(start)} ~ ${_fmtWhen2(lim)}`;
  }
  function _payDeadline(lim, left) {
    if (!lim) return '';
    if (left == null || left <= 0) return '결제기한 만료';
    const when = `${lim.getMonth() + 1}/${lim.getDate()}(${_WD[lim.getDay()]}) `
               + `${String(lim.getHours()).padStart(2, '0')}:${String(lim.getMinutes()).padStart(2, '0')}`;
    let tail;
    if (left >= 86400) {
      const t0 = new Date(); t0.setHours(0, 0, 0, 0);
      const d0 = new Date(lim.getFullYear(), lim.getMonth(), lim.getDate());
      tail = `(D-${Math.round((d0 - t0) / 86400000)})`;
    } else {
      const h = Math.floor(left / 3600), m = Math.floor((left % 3600) / 60);
      tail = `(${h ? h + '시간 ' : ''}${m}분 남음)`;
    }
    return `결제기한 ${when}  ${tail}`;
  }
  function _dayTag(ymd) { const d = _daysTo(ymd); return d == null ? '' : d === 0 ? '오늘' : d === 1 ? '내일' : d > 1 ? `${d}일 전` : ''; }
  const _SEAT_CACHE_KEY = 'kor_ktx_seatcache';
  function _seatCacheGet(k) {
    try { const o = JSON.parse(localStorage.getItem(_SEAT_CACHE_KEY) || '{}'); const e = o[k];
      return e && (Date.now() - e.ts < 30 * 86400000) ? e.v : ''; } catch { return ''; }
  }
  function _seatCacheSet(k, v) {
    try { const o = JSON.parse(localStorage.getItem(_SEAT_CACHE_KEY) || '{}'); o[k] = { v, ts: Date.now() };
      const keys = Object.keys(o); if (keys.length > 200) delete o[keys[0]];
      localStorage.setItem(_SEAT_CACHE_KEY, JSON.stringify(o)); } catch {}
  }
  function ticketCard(t, isRsv) {
    const c = div('tkc' + (isRsv ? ' rsv' : ''));
    const head = div('tkc-head');
    head.append(txt('span', 'tkc-hdate', _dotDate(t.date)), txt('span', 'tkc-when', isRsv ? (t.kind === 'waiting' ? '예약대기' : '미결제') : _dayTag(t.date)));
    c.appendChild(head);
    const b = div('tkc-body');
    const kind = div('tkc-kind');
    const trainName = [t.train_type, t.train_no].filter(Boolean).join(' ');
    kind.append(txt('span', '', trainName || (isRsv ? '기차 예약' : '기차 승차권')), txt('span', 'tkc-cnt', (t.seats || 1) + '매'));
    b.append(kind, div('tkc-line'));
    const side = (stn, tm) => { const w = div('tkc-side'); w.append(txt('div', 'tkc-stn', stn || '-'), txt('div', 'tkc-time', _fmtHHMM(tm) || '-')); return w; };
    const route = div('tkc-route'); route.append(side(t.dep, t.dep_time), txt('div', 'tkc-arrow', '→'), side(t.arr, t.arr_time));
    b.appendChild(route);
    const _clsTxt = t.seat_class ? `(${t.seat_class})` : '';
    const _seatKey = (t.d_wct || '') + (t.d_dt || '') + (t.d_sqno || '') + (t.d_pwd || '');
    const _cachedSeat = (!isRsv && _seatKey) ? _seatCacheGet(_seatKey) : '';
    const shownSeat = t.seat_pick || _cachedSeat || '';
    const _show = (seat) => [seat, _clsTxt].filter(Boolean).join(' ');
    if (shownSeat) {
      b.appendChild(txt('div', 'tkc-meta seat', _show(shownSeat)));   // 좌석 있으면 등급과 함께
    } else if (isRsv) {
      if (_clsTxt) b.appendChild(txt('div', 'tkc-meta seat', _clsTxt));   // 예약은 좌석이 서버에서 오지만 없으면 등급만
    } else if (t.d_wct || t.d_sqno) {
      // 발권 승차권은 좌석 상세를 탭할 때 조회(지연 로딩). '(일반실)' 등급 줄은 좌석 뜨기 전엔 숨긴다.
      const seatBtn = txt('div', 'tkc-seatbtn', '좌석 보기');
      seatBtn.addEventListener('click', () => {
        if (seatBtn.dataset.loading) return; seatBtn.dataset.loading = '1'; seatBtn.textContent = '불러오는 중…';
        const a = lsGetAccount('ktx');
        api('POST', '/ticketdetail', { member_no: a.memberNo, password: a.password, wct: t.d_wct, dt: t.d_dt, sqno: t.d_sqno, pwd: t.d_pwd })
          .then(d => {
            if (d && d.ok && d.seat_pick) {
              _seatCacheSet(_seatKey, d.seat_pick);   // 확인한 좌석은 브라우저에 저장 — 재호출 없음
              seatBtn.replaceWith(txt('div', 'tkc-meta seat', _show(d.seat_pick)));
            } else { seatBtn.textContent = '좌석 정보 없음'; }
          })
          .catch(() => { seatBtn.textContent = '좌석 보기'; delete seatBtn.dataset.loading; });
      });
      b.appendChild(seatBtn);
    }
    if (isRsv) {
      const lim = _parseBuyLimit(t.buy_limit), left = lim ? (lim.getTime() - Date.now()) / 1000 : null;
      // 결제 시작일(yyyymmdd)이 있으면 그날 00:00부터 결제할 수 있다
      const start = t.buy_start ? _parseBuyLimit(t.buy_start + '000000') : null;
      const notYet = !!(start && start.getTime() > Date.now()) || t.pay_open === false;
      const act = div('tkc-actions');
      const pay = el('a', 'btn-primary tk-btn tk-pay'); pay.target = '_blank';
      pay.href = 'https://www.korail.com/ticket/reservation/list';
      pay.appendChild(txt('span', 'tk-pay-t1', notYet ? '결제 시작 전' : '코레일에서 결제'));
      if (t.price != null) pay.appendChild(txt('span', 'tk-pay-t2', _fmtWon(t.price)));
      if (notYet) {   // 아직 결제할 수 없는 예약 — 눌러도 소용없으니 막는다
        pay.classList.add('tk-pay-off');
        pay.removeAttribute('href');
        pay.addEventListener('click', e => { e.preventDefault(); notify('아직 결제 기간이 아닙니다'); });
      }
      const cancel = el('button', 'tk-btn tk-cancel'); cancel.textContent = '예약 취소';
      cancel.addEventListener('click', async () => {
        if (!await askConfirm('예약을 취소할까요? 되돌릴 수 없습니다.', cancel)) return;
        const a = lsGetAccount('ktx'); cancel.disabled = true; cancel.textContent = '취소 중…';
        api('POST', '/reservations/cancel', { member_no: a.memberNo, password: a.password, pnr: t.rsv_id }).then(d => {
          if (!d || !d.ok) { notify('취소 실패: ' + (d && d.error || '')); cancel.disabled = false; cancel.textContent = '예약 취소'; return; }
          load(true);
        }).catch(() => { notify('취소 실패'); cancel.disabled = false; cancel.textContent = '예약 취소'; });
      });
      act.append(pay, cancel); b.appendChild(act);
      const limLine = notYet ? _payPeriod(start, lim) : _payDeadline(lim, left);
      const limEl = txt('div', 'tkc-limit ' + (notYet ? 'note-line' : 'warn-line'), limLine);
      if (limLine) b.appendChild(limEl);
    }
    c.appendChild(b);
    return c;
  }
  function ncardCard(n) {
    const seg = (n.segments || [])[0] || {}, c = div('tkc');
    const head = div('tkc-head');
    head.append(txt('span', 'tkc-hdate', `${_dotDate(n.valid_from)} ~ ${_dotDate(n.valid_to)}`), txt('span', 'tkc-when', `${seg.dep || ''}↔${seg.arr || ''}`));
    c.appendChild(head);
    const b = div('tkc-body');
    b.append(txt('div', 'nc-use', `${n.used}회 이용 · ${n.remaining}회 남음 (총 ${n.used + n.remaining}회)`));
    const d = _daysTo(n.valid_to);
    if (d != null) b.appendChild(txt('div', 'nc-left', d >= 0 ? `이용 종료까지 ${d}일` : '이용 기간 종료'));
    c.appendChild(b);
    return c;
  }
  function render(d) {
    if (!d || !d.ok) { empty(secR, '예약 (미결제)', d && d.error || '조회 실패'); secT.innerHTML = ''; secN.innerHTML = ''; return; }
    const rs = d.reservations || [], ts = (d.tickets || []).slice().sort((a, b) => (a.date + (a.dep_time || '')).localeCompare(b.date + (b.dep_time || ''))), ns = (d.ncards || []).filter(c => c.ok);
    if (!rs.length) empty(secR, '예약 (미결제)', d.reservations_error ? '조회 실패: ' + d.reservations_error : '미결제 예약이 없습니다');
    else { secR.innerHTML = ''; secR.appendChild(txt('div', 'group-header', `예약 (미결제) ${rs.length}건`)); rs.forEach(r => secR.appendChild(ticketCard(r, true))); secR.appendChild(div('sp18')); }
    if (!ts.length) empty(secT, '승차권', d.tickets_error ? '조회 실패: ' + d.tickets_error : '이용 예정 승차권이 없습니다');
    else { secT.innerHTML = ''; secT.appendChild(txt('div', 'group-header', `승차권 ${ts.length}장`)); ts.forEach(t => secT.appendChild(ticketCard(t, false))); secT.appendChild(div('sp18')); }
    secN.innerHTML = '';
    _saveNcardFrom(d.ncards);
    if (ns.length) {
      secN.appendChild(txt('div', 'group-header', 'N카드'));
      ns.forEach(c => secN.appendChild(ncardCard(c)));
      secN.appendChild(div('sp18'));
    }
  }
  function load(force) {
    if (window.__DEMO__) { empty(secR, '예약 (미결제)', '데모에서는 코레일을 조회하지 않습니다'); secT.innerHTML = ''; secN.innerHTML = ''; return; }
    const a = lsGetAccount('ktx');
    if (!a.memberNo || !a.password) { empty(secR, '예약 (미결제)', '설정 탭에서 코레일 계정을 먼저 등록하세요'); secT.innerHTML = ''; secN.innerHTML = ''; return; }
    if (loading) return; if (!force && Date.now() - lastAt < 120000) return;   // 탭 재방문 재조회 억제(2026-09-09)
    loading = true;
    if (!secR.children.length) secR.appendChild(loadingBox('코레일에서 불러오는 중…'));
    api('POST', '/mytickets', { member_no: a.memberNo, password: a.password, force: !!force }).then(d => { lastAt = Date.now(); render(d); }).catch(() => render({ ok: false, error: '조회 실패' })).finally(() => { loading = false; });
  }
  _loadTickets = load;
  // 결제기한 카운트다운은 1분마다 갱신(서버 재조회 없이 다시 그림)
  if (timer) clearInterval(timer);
  timer = setInterval(() => { if (_currentTab === 'tickets' && lastAt && !loading) load(false); }, 60000);
}

// ── 결과 탭: 감시 작업 한 줄씩 + 실시간 로그 ──────────────────────
let _loadResults = null;
let _loadMyInfo = null;
let _renderSettings = () => {};   // 설정 탭이 만들어질 때 실제 함수로 바뀐다
let _ktxInfoSheet = () => {};    // 'KTX 계정 정보 자세히보기' 시트
function buildResultsPage() {
  const panel = document.getElementById('results-panel');
  if (!panel) return;
  panel.innerHTML = '';
  const listWrap = div(''); panel.appendChild(listWrap);
  // 예약 → 실행 중(중지 포함) → 완료. 섹션마다 색을 달리해 한눈에 갈린다(2026-09-09 지시)
  const _SECS = [['sched', '예약'], ['running', '실행 중'], ['done', '완료']];
  const secs = {};
  _SECS.forEach(([k, label]) => {
    const sec = div('jobs-sec ' + k); const h = div('jobs-sec-h');
    const chip = txt('span', 'jobs-sec-chip', label); const cnt = txt('span', 'jobs-sec-cnt', '');
    h.append(chip, cnt); const body = div('jobs-sec-body'); sec.append(h, body); sec.style.display = 'none';
    listWrap.appendChild(sec); secs[k] = { el: sec, body, cnt };
  });
  const sp = div(''); sp.classList.add('u-safe-b'); panel.appendChild(sp);
  const cards = {};   // job → 카드 DOM(재사용)
  const _secOf = j => j.result ? 'done' : (j.scheduled ? 'sched' : 'running');
  // 접힘 버튼이 곧 상태 한 줄이다
  function _toggleLabel(j) {
    if (j.result) {
      if (!j.auto_pay) return '예매 성공';                       // 예매만
      if (j.pay_state === 'paid') return '예매 · 결제 모두 성공';
      if (j.pay_state === 'error') return '예매 성공 · 결제 실패';
      return '예매 성공';                                        // dry/진행 중 등
    }
    if (j.scheduled) return j.start_at ? `${_fmtStartAt(j.start_at)} 시작 예정` : '시작 대기 중';
    if (j.running) return `조회 중${j.last_check_at ? ` (마지막 조회 ${_agoText(j.last_check_at)})` : ''}`;
    return j.last_error ? _shortErr(j.last_error) : (j.status_text || '중지됨');
  }
  function _fmtStartAt(iso) {
    const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
    return m ? `${m[1]}년 ${+m[2]}월 ${+m[3]}일 ${m[4]}:${m[5]}` : String(iso);
  }
  function _agoText(hhmmss) {
    const m = String(hhmmss).match(/^(\d{2}):(\d{2}):(\d{2})$/); if (!m) return hhmmss;
    const now = new Date(); const t = new Date(now); t.setHours(+m[1], +m[2], +m[3], 0);
    let s = Math.round((now - t) / 1000); if (s < 0) s += 86400;
    return s < 60 ? `${s}초 전` : s < 3600 ? `${Math.floor(s / 60)}분 전` : `${Math.floor(s / 3600)}시간 전`;
  }
  function _shortErr(e) {   // repr(Exception) → 안의 문구만
    const s = String(e); const m = s.match(/^\w+\((['"])(.*)\1\)$/s); return (m ? m[2] : s).slice(0, 80);
  }

  // 감시 하나의 로그창 — 한 번 만들면 계속 쓴다(재렌더링해도 스크롤·내용 유지)
  function logBox(job) {
    if (_jobLogEls[job]) return _jobLogEls[job]._wrap;
    const termWrap = div('terminal-wrap'); termWrap.style.cssText = 'position:relative';
    const logEl = div('terminal-log'); logEl.style.height = '108px'; termWrap.appendChild(logEl);   // 154px의 70% (2026-09-09 지시)
    // 복사 — 박스 안 우상단. 화면에 보이는 그대로(하트비트 제외) 클립보드로
    const _ICON_COPY = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>';
    const copyBtn = el('button', 'log-copybtn'); copyBtn.type = 'button'; copyBtn.innerHTML = _ICON_COPY; copyBtn.title = '로그 복사';
    copyBtn.addEventListener('click', async () => {
      const text = logEl.innerText.trim();
      if (!text) { toast('복사할 로그가 없습니다'); return; }
      try { await navigator.clipboard.writeText(text); }
      catch {   // 클립보드 API가 막힌 환경(비보안 컨텍스트 등) 폴백
        const ta = document.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select(); try { document.execCommand('copy'); } catch {} document.body.removeChild(ta);
      }
      toast('로그를 복사했습니다');
    });
    const btns = div('log-btns'); btns.append(copyBtn); termWrap.appendChild(btns);
    const newBtn = el('button', 'log-newbtn'); newBtn.type = 'button'; newBtn.style.display = 'none';
    newBtn.addEventListener('click', () => { logEl.scrollTop = logEl.scrollHeight; }); termWrap.appendChild(newBtn);
    logEl._onPending = (n) => { newBtn.style.display = n > 0 ? '' : 'none'; newBtn.textContent = `↓ 새 로그 ${n}`; };
    logEl.addEventListener('scroll', () => { if (_logAtBottom(logEl)) { logEl._pending = 0; newBtn.style.display = 'none'; } });
    const wrap = div('job-log'); wrap.appendChild(termWrap);
    logEl._wrap = wrap; _jobLogEls[job] = logEl;
    const draw = lines => {
      logEl.innerHTML = '';
      _sortByTime(lines).filter(l => !_isHeartbeat(l)).forEach(l => appendLog(logEl, l));
      logEl.scrollTop = logEl.scrollHeight;      // 항상 최신 줄이 보이게
      logEl._pending = 0; if (logEl._onPending) logEl._onPending(0);
    };
    draw(_jobLogBuf[job] || []);
    // 서버에 남아 있는 지난 기록을 불러온다(새로고침·재시작에도 로그가 유지된다)
    if (!window.__DEMO__) {
      api('GET', '/joblog?mode=' + encodeURIComponent(job)).then(r => {
        const lines = (r && r.lines) || [];
        if (!lines.length) return;
        draw([...lines, ...(_jobLogBuf[job] || [])]);
      }).catch(() => {});
    }
    return wrap;
  }

  function makeCard(job) {
    const c = div('card job-row');
    // 머리: [상태 배지] 9/11(금) 광주송정 → 수서 ............ [중단][삭제]
    c._head = div('job-head');
    c._badge = txt('span', 'job-badge', '');
    c._name = txt('div', 'job-title', '');
    c._act = div('job-actions');
    c._head.append(c._badge, c._name, c._act);
    c._opt = txt('div', 'job-sub', '');
    c._meta = txt('div', 'job-sub muted', '');
    c._err = txt('div', 'job-sub err', '');
    c._trains = div('job-trains');
    // 예약이 끝난 카드는 로그를 접어 둔다 — 버튼을 눌러야 펼쳐진다(2026-09-09 지시)
    c._log = logBox(job);
    c._logToggle = el('button', 'job-log-toggle'); c._logToggle.type = 'button'; c._logToggle.textContent = '실시간 로그 보기';
    // 토글 박스는 항상 보인다 — 상태 한 줄이자 로그 펼치기 버튼. 눌러도 사라지지 않고 그 아래로 로그가 열린다.
    c._logToggle.addEventListener('click', () => {
      // 펼치기 박스는 그대로 두고 그 아래에 로그를 연다(다시 누르면 접힘). 로그 자체의 '숨기기'로도 접힌다.
      c._logOpen = !c._logOpen;
      c._log.style.display = c._logOpen ? '' : 'none';
      c._logToggle.classList.toggle('open', c._logOpen);
      if (c._logOpen) { const le = _jobLogEls[job]; if (le) le.scrollTop = le.scrollHeight; }   // 펼치면 맨 아래부터
    });
    { const le = _jobLogEls[job]; if (le) le._onHide = () => { c._logOpen = false; c._log.style.display = 'none'; c._logToggle.classList.remove('open'); }; }
    c.append(c._head, c._trains, c._opt, c._meta, c._err, c._logToggle, c._log);
    return c;
  }

  // 조회중인 열차 목록 — 예매 탭 열차 행과 같은 서식, 체크박스 자리에 작은 원
  function fillTrains(host, j) {
    const rows = _fillTrainInfo(j);
    const sig = rows.map(t => `${t.no}:${t.type}:${t.dep_time}:${t.arr_time}`).join(',')
              + '|' + (j.won_train || '') + '|' + (j.running ? 1 : 0) + '|' + (j.scheduled ? 1 : 0);
    if (host._sig === sig) return;
    host._sig = sig;
    host.innerHTML = '';
    if (!rows.length) return;
    const box = div('train-list');
    const body = div('train-list-body');
    rows.forEach(t => {
      const r = div('train-row');
      r.appendChild(div('train-dot'));
      const when = t.dep_time ? `${_fmtHHMM(t.dep_time)}${t.arr_time ? '→' + _fmtHHMM(t.arr_time) : ''}` : '';
      if (when) r.appendChild(txt('span', 'train-time', when));
      r.appendChild(txt('span', 'train-name', _trainLabel(t)));
      body.appendChild(r);   // 우측 상태 배지 제거(카드 배지·섹션이 이미 상태를 말한다)
    });
    box.appendChild(body); host.appendChild(box);
  }

  // 감시가 아직 한 번도 조회하지 못했으면 번호만 온다 — 로컬 열차 캐시로 종류·시각을 메운다
  function _fillTrainInfo(j) {
    const rows = j.trains || [];
    if (!rows.length) return rows;
    const key = `${j.dep || ''}|${j.arr || ''}|${j.date || ''}`;
    return rows.map(t => {
      if (t.type && t.arr_time) return t;
      const hit = _tcFind(key, String(t.no));
      return hit ? { no: t.no, type: t.type || hit.type || '', dep_time: t.dep_time || hit.dep_time || '',
                     arr_time: t.arr_time || hit.arr_time || '' } : t;
    });
  }

  function _fmtJobTitle(j) {
    const d = j.date && /^\d{8}$/.test(j.date)
      ? (() => { const t = new Date(+j.date.slice(0, 4), +j.date.slice(4, 6) - 1, +j.date.slice(6, 8));
                 return `${t.getMonth() + 1}/${t.getDate()}(${_WD[t.getDay()]})`; })() : '';
    return `${d} ${j.dep || '?'} → ${j.arr || '?'}`.trim();
  }

  function fill(c, j) {
    const won = !!j.result;
    const sec = _secOf(j); c.dataset.sec = sec;
    // 로그는 기본 접힘. 접힘 버튼 문구가 상태(시작 예정·조회 중·중지 사유)를 말한다(2026-09-09 지시)
    c._log.style.display = c._logOpen ? '' : 'none';   // 토글 박스는 항상 보인다(펼치면 그 아래 로그)
    c._logToggle.classList.toggle('open', !!c._logOpen);
    c._logToggle.textContent = _toggleLabel(j);
    const stopped = !j.running && !won;
    c._badge.textContent = won ? '성공' : (j.scheduled ? '예약' : (j.running ? '감시 중' : '중지'));
    c._badge.className = 'job-badge ' + (won ? 'ok' : (j.scheduled ? 'sched' : (j.running ? 'run' : 'stop')));
    c._name.textContent = _fmtJobTitle(j);
    // 감시가 끝나도 어떤 조건이었는지는 남아야 한다
    if (j.opt_text) c._optText = j.opt_text;
    const seatLine = c._optText ? `좌석 옵션: ${c._optText}` : '';
    const payLine = j.auto_pay
      ? `결제 옵션: 예매+결제(${j.pay_method === 'card' ? '신용카드' : '토스페이'})`
      : '결제 옵션: 예매만';
    c._opt.textContent = [seatLine, payLine].filter(Boolean).join('\n');
    c._opt.style.display = c._opt.textContent ? '' : 'none';
    c._meta.style.display = 'none';   // '조회:N회…' 제거(불필요) — 상태는 접힘 버튼에 있다
    const err = '';   // 중지 사유는 접힘 버튼(_toggleLabel)에 표시한다
    c._err.textContent = err; c._err.style.display = err ? '' : 'none';
    fillTrains(c._trains, j);
    const state = (j.runner || '') + (j.remote ? '|R' : '') + '|' + (j.running ? 'run' : (won ? 'won' : 'stop'));
    if (c._actState === state) return;
    c._actState = state;
    c._act.innerHTML = '';
    const mk = (icon, cls, title, fn) => { const b = el('button', 'job-btn ' + cls); b.type = 'button';
      b.innerHTML = icon; b.title = title; b.setAttribute('aria-label', title);
      b.addEventListener('click', () => { b.disabled = true; fn(); }); return b; };
    if (j.remote) {   // 다른 서버(웹·다른 PC)에서 도는 감시
      const tag = txt('span', 'job-remote-tag', j.runner_label || '웹');
      tag.title = (j.runner_label || '다른 기기') + '에서 실행되는 감시입니다'
        + (j.runner_online ? '' : ' (지금 꺼져 있음)');
      if (!j.runner_online) tag.classList.add('off');
      c._act.appendChild(tag);
      if (!j.running && !won && !j.expired) c._act.appendChild(mk(_ICON_PLAY, 'go', '재개', () => _resumeJob(j, load)));
    } else if (j.running) {
      c._act.appendChild(mk(_ICON_PAUSE, 'stop', '중단', () => api('POST', `/stop?mode=${encodeURIComponent(j.job)}`).finally(load)));
    } else if (won) {   // 성공한 작업은 삭제만
      c._act.appendChild(mk(_ICON_TRASH, 'del', '삭제', () => api('DELETE', `/jobs?mode=${encodeURIComponent(j.job)}`).finally(load)));
    } else {
      c._act.append(
        mk(_ICON_PLAY, 'go', '재개', () => _resumeJob(j, load)),
        mk(_ICON_TRASH, 'del', '삭제', () => api('DELETE', `/jobs?mode=${encodeURIComponent(j.job)}`).finally(load)));
    }
  }

  const _DEMO_JOBS = [
    { job: 'job0', running: true, scheduled: true, start_at: '2026-09-10T07:00:00', label: '광주송정 → 용산', dep: '광주송정', arr: '용산', date: '20261010',
      start_hhmm: '0500', end_hhmm: '1800', adults: 1, attempts: 0, status_text: '예약됨',
      opt_text: '긴급(특실 우선 / 내측 우선 / 순방향 우선)',
      trains: [{ no: '422', type: 'KTX', dep_time: '135400', arr_time: '155600' }] },
    { job: 'urgent', running: true, label: '서울 → 부산', dep: '서울', arr: '부산', date: '20260915',
      start_hhmm: '0800', end_hhmm: '1200', adults: 1, attempts: 42, last_check_at: '08:41:12',
      next_delay_sec: 10, status_text: '감시 중',
      opt_text: '긴급(일반실 우선 / 창측 우선 / 순방향 우선)',
      trains: [{ no: '101', type: 'KTX', dep_time: '081200', arr_time: '110300' },
               { no: '019', type: 'KTX-산천', dep_time: '083500', arr_time: '113800' }] },
    { job: 'job2', running: false, label: '용산 → 광주송정', dep: '용산', arr: '광주송정', date: '20260912',
      start_hhmm: '1300', end_hhmm: '1700', adults: 2, attempts: 118, last_check_at: '07:55:03',
      status_text: '예약 성공', result: { pnr: '3202609740', train_no: '415' },
      auto_pay: true, pay_state: 'paid',
      opt_text: '선호 좌석 (실패시 일반실 우선, 창측 우선, 순방향 우선)', won_train: '415',
      trains: [{ no: '415', type: 'KTX-산천', dep_time: '134000', arr_time: '162200' }] },
    { job: 'job3', running: false, label: '서울 → 강릉', dep: '서울', arr: '강릉', date: '20260925',
      start_hhmm: '0500', end_hhmm: '1100', adults: 1, attempts: 9, last_check_at: '02:14:40',
      status_text: '서버 재시작으로 중단됨 — 재개 가능',
      opt_text: '긴급(일반실 우선 / 창측 우선 / 순방향 우선)',
      trains: [{ no: '832', type: 'KTX-이음', dep_time: '050200', arr_time: '070100' }] },
  ];
  function load() {
    if (window.__DEMO__) { render(_DEMO_JOBS); return; }
    api('GET', '/jobs').then(d => {
      render((d && d.jobs) || []);
    }).catch(() => {});
  }
  function render(jobs) {
    {
      const runCnt = jobs.filter(j => j.running).length;
      _pillJobs = jobs;
      if (_headerPill) _headerPill.update(jobs);
      const tabEl = document.getElementById('tab-results'); if (tabEl) tabEl.classList.toggle('bot-running', runCnt > 0);
      const alive = new Set(jobs.map(j => j.job));
      Object.keys(cards).forEach(k => { if (!alive.has(k)) { cards[k].remove(); delete cards[k]; } });
      const empty = listWrap.querySelector('.tk-empty');
      if (!jobs.length) {
        _SECS.forEach(([k]) => { secs[k].el.style.display = 'none'; });
        if (!empty) emptyBox('등록된 감시가 없습니다. 예매 탭에서 조건을 정하고 시작하세요.', listWrap);
        return;
      }
      if (empty) empty.remove();
      // 섹션 안에서는 최근에 시작한 것이 맨 위. 시작 시각이 없으면 id(시각 기반)로 대신한다.
      const ordered = jobs.slice().sort((a, b) => ((b.started_ts || 0) - (a.started_ts || 0))
                                                 || String(b.job).localeCompare(String(a.job)));
      const bySec = { sched: [], running: [], done: [] };
      ordered.forEach(j => {
        let c = cards[j.job];
        if (!c) {
          c = cards[j.job] = makeCard(j.job); c._job = j.job; secs[_secOf(j)].body.appendChild(c);
          // 붙이기 전에는 scrollHeight가 0이라 draw()의 고정이 무효 — 붙인 뒤 맨 아래로
          const le = _jobLogEls[j.job]; if (le) le.scrollTop = le.scrollHeight;
        }
        fill(c, j); bySec[_secOf(j)].push(j.job);
      });
      // 섹션·순서가 바뀐 경우에만 다시 배치한다(노드 이동은 상태를 유지하지만 불필요한 이동은 피한다)
      _SECS.forEach(([k]) => {
        const want = bySec[k].join(','), have = [].map.call(secs[k].body.children, x => x._job || '').join(',');
        if (want !== have) bySec[k].forEach(job => secs[k].body.appendChild(cards[job]));
        secs[k].cnt.textContent = bySec[k].length ? String(bySec[k].length) : '';
        secs[k].el.style.display = bySec[k].length ? '' : 'none';
      });
    }
  }
  _loadResults = load;
  load();   // 탭을 처음 열기 전에도 목록이 준비돼 있게
  setInterval(() => { if (_currentTab === 'results') load(); }, 5000);
}

// 예매 탭에서 시작한 작업의 요청 본문(재개용)
const _jobBodies = {};

// ── 알림(우상단 종 아이콘): 시스템 기록 + 코레일 공지 ─────────────
let _alertBadge = null;
function _refreshAlertBadge() {
  // 시스템 알림을 안 보여주므로 빨간 점도 안 켠다. 공지 새 글이 생기면 그때만.
  if (!_alertBadge) return;
  _loadNotices().then(d => {
    if (!_alertBadge) return;
    const seen = _noticeSeen();
    const fresh = ((d && d.items) || []).some(n => n.id != null && !seen.includes(n.id));
    _alertBadge.style.display = fresh ? '' : 'none';
  }).catch(() => { _alertBadge.style.display = 'none'; });
}
function _alertsSheet(onClose) {
  const ov = div('picker-overlay top'); const sheet = div('picker-sheet alerts-sheet tall');
  sheet.appendChild(txt('div', 'wheel-title', '알림'));
  const body = div('alerts-body'); sheet.appendChild(body);
  const close = () => { if (ov.parentNode) document.body.removeChild(ov); onClose && onClose(); };
  const bar = div('picker-sheet-cancel'); const cb = div('picker-sheet-cancel-btn'); cb.textContent = '닫기';
  cb.style.color = 'var(--blue)'; cb.addEventListener('click', close); bar.appendChild(cb);
  ov.append(sheet, bar); ov.addEventListener('click', e => { if (e.target === ov) close(); });
  document.body.appendChild(ov);

  // 시스템 알림은 띄우지 않는다(2026-09-09 지시) — 코레일 공지만.
  const ntHead = txt('div', 'group-header', '코레일 공지'); ntHead.style.display = 'none';
  body.appendChild(ntHead);
  const ntWrap = div(''); body.appendChild(ntWrap);

  // 공지와 같은 모양: 위에 날짜·시간, 아래 제목. 누르면 자세히.
  // 날짜·제목을 한 줄에, 누르면 아래로 펼쳐서 본문
  function listCard(items, loadBody) {
    const card = makeGridCard();
    items.forEach((it, i) => {
      if (i) card.appendChild(div('grid-divider-h'));
      const cell = div('grid-cell-full alert-row'); cell.style.cursor = 'pointer';
      cell.append(txt('span', 'alert-when', it.when), txt('span', 'alert-title', it.title));
      const body = div('alert-body');
      let opened = false, loaded = false;
      cell.addEventListener('click', async () => {
        opened = !opened;
        cell.classList.toggle('open', opened);
        body.style.display = opened ? '' : 'none';
        if (opened && !loaded) {
          loaded = true;
          body.textContent = '불러오는 중…';
          try { body.textContent = (await loadBody(it)) || '(내용 없음)'; }
          catch { body.textContent = '(내용을 불러오지 못했습니다)'; }
        }
      });
      body.style.display = 'none';
      card.append(cell, body);
    });
    return card;
  }
  function detailSheet(title, when, text) {
    const o2 = div('picker-overlay'); const sh = div('picker-sheet'); sh.classList.add('u-sheet');
    sh.appendChild(txt('div', 'wheel-title', title));
    const meta = txt('div', 'hint', when); meta.classList.add('u-note'); sh.appendChild(meta);
    sh.appendChild(txt('div', 'notice-body', text || '(내용 없음)'));
    const b2 = div('preset-actions'); const c2 = el('button', 'btn-primary'); c2.textContent = '닫기';
    c2.classList.add('btn-ghost');
    c2.addEventListener('click', () => document.body.removeChild(o2)); b2.appendChild(c2); sh.appendChild(b2);
    o2.appendChild(sh); o2.addEventListener('click', e => { if (e.target === o2) document.body.removeChild(o2); });
    document.body.appendChild(o2);
  }
  ntWrap.appendChild(loadingBox('불러오는 중…'));
  api('POST', '/events/seen').then(() => { if (_alertBadge) _alertBadge.style.display = 'none'; }).catch(() => {});

  _loadNotices().then(d => {
    ntWrap.innerHTML = '';
    const items = ((d && d.items) || []).slice(0, 12);
    // 목록을 본 순간 읽음 처리 — 본문을 펼친 공지만 기록하면 빨간 점이 영영 안 꺼진다
    items.forEach(n => { if (n.id != null) _noticeMarkSeen(n.id); });
    if (_alertBadge) _alertBadge.style.display = 'none';
    if (!items.length) { emptyBox('메시지가 없습니다', ntWrap); return; }
    ntHead.style.display = '';
    ntWrap.appendChild(listCard(items.map(n => ({ when: (n.date || '').slice(5), title: n.title || '', n })), async it => {
      const n = it.n;
      if (!n.text && n.id != null) {
        const d2 = await api('GET', '/notices/' + n.id);
        if (d2 && d2.ok) { n.text = d2.text; n.images = d2.images; }
        _noticeMarkSeen(n.id);
      }
      return n.text || (n.images && n.images.length ? '(이미지 공지 — 코레일에서 보기)' : '');
    }));
  }).catch(() => { ntWrap.innerHTML = ''; emptyBox('메시지가 없습니다', ntWrap); });
}

// 후보 좌석 템플릿 편집 — 열차 종류별 좌석맵에서 직접 골라 1~10순위로 담는다
const _TRAIN_TYPES = ['KTX', 'KTX-산천', 'KTX-이음', 'ITX-새마을', 'ITX-마음', '무궁화호'];
const _ICON_TRASH = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/><path d="M19 6l-1 14a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1L5 6"/><path d="M10 11v6M14 11v6"/></svg>';
const _layoutCache = {};   // `${type}` / `${type}|${car}` → 응답
const _layoutFail = {};    // 같은 키 연속 실패 횟수(3회 넘으면 잠시 조회 중단)

async function _layoutFetch(trainType, carNo, updown, groupId) {
  const ud = updown === 'up' ? 'up' : 'down';
  // 그룹을 키에 넣는다 — 같은 종류·방향이라도 편성이 다르면 좌석 배치가 다르다
  const key = (groupId || trainType + '|' + ud) + (carNo ? '|' + carNo : '');
  if (_layoutCache[key]) return _layoutCache[key];
  if ((_layoutFail[key] || 0) >= 3) return { ok: false, error: '좌석 배치 조회 실패(잠시 후 다시)' };   // 폭주 차단
  const qs = `train_type=${encodeURIComponent(trainType)}&updown=${ud}`
           + (groupId ? `&group=${encodeURIComponent(groupId)}` : '')
           + (carNo ? `&car_no=${encodeURIComponent(carNo)}` : '');
  const r = await api('GET', '/layout?' + qs);
  if (r && r.ok) _layoutCache[key] = r;
  else { const neg = _layoutFail[key] || 0; _layoutFail[key] = neg + 1; }   // 실패 누적 — 반복 두들김 차단
  return r;
}

function _candEditor(tpl, onSave, lockTrain) {
  const t = tpl ? JSON.parse(JSON.stringify(tpl))
                : { id: '', name: '', trainNo: '', trainType: 'KTX', updown: 'down', fallback: 'watch', seats: [] };
  if (!t.trainType) t.trainType = 'KTX';
  if (!t.updown) t.updown = 'down';
  let fbF, listWrap, mapHost, carHost, typeF;
  let curCar = null;

  function rankOf(car, label) {
    const i = t.seats.findIndex(x => String(x.car_no).replace(/^0+/, '') === String(car).replace(/^0+/, '') && x.label === label);
    return i < 0 ? 0 : i + 1;
  }
  let _seatCells = {};   // label → 좌석 버튼(다시 그리지 않고 배지만 바꾼다)
  function refreshRanks() {
    Object.keys(_seatCells).forEach(label => {
      const cell = _seatCells[label];
      const rk = rankOf(curCar, label);
      const ic = cell.querySelector('.seat-ic');   // 아이콘(휠체어·유아동반)은 textContent가 지우므로 보존 후 재부착
      cell.textContent = ic && !rk ? '' : (rk ? String(rk) : String(parseInt(label, 10)));
      if (ic) cell.appendChild(ic);
      cell.classList.toggle('sel', !!rk);
    });
  }
  function toggleSeat(car, sx) {
    const r = rankOf(car, sx.label);
    if (r) t.seats.splice(r - 1, 1);
    else {
      if (t.seats.length >= 10) { notify('10순위까지만 등록할 수 있습니다'); return; }
      t.seats.push({ car_no: String(car).padStart(4, '0'), seat_no: sx.label, label: sx.label,
                     car_label: `${parseInt(car, 10)}호차` });
      toast(`${parseInt(car, 10)}호차 ${sx.label} 좌석이 추가됐습니다`);
    }
    drawSeats(); refreshRanks();
  }
  function drawSeats() {
    listWrap.innerHTML = '';
    if (!t.seats.length) { listWrap.appendChild(txt('div', 'train-list-msg', '좌석맵에서 원하는 자리를 순서대로 누르세요')); return; }
    t.seats.forEach((sx, i) => {
      const row = div('cand-row');
      row.append(txt('span', 'cand-rank', `${i + 1}순위`),
                 txt('span', 'cand-seat', `${parseInt(sx.car_no, 10)}호차 ${sx.label}`));
      const ctrl = div('cand-ctrl');
      // 프리셋 관리와 같은 버튼(pbtn) — 아이콘·크기 통일
      const mk = (label, on, dis, cls) => { const b = el('button', 'pbtn' + (cls ? ' ' + cls : '')); b.type = 'button'; b.textContent = label; b.disabled = !!dis; b.addEventListener('click', on); return b; };
      ctrl.append(
        mk('▲', () => { [t.seats[i - 1], t.seats[i]] = [t.seats[i], t.seats[i - 1]]; drawSeats(); refreshRanks(); }, i === 0),
        mk('▼', () => { [t.seats[i + 1], t.seats[i]] = [t.seats[i], t.seats[i + 1]]; drawSeats(); refreshRanks(); }, i === t.seats.length - 1),
        mk('삭제', () => { t.seats.splice(i, 1); drawSeats(); refreshRanks(); }, false, 'del'));
      row.appendChild(ctrl); listWrap.appendChild(row);
    });
  }
  const loading = (host, msg) => loadingBox(msg, host);
  async function loadCars() {
    loading(carHost, '편성 불러오는 중…'); mapHost.innerHTML = '';
    const r = await _layoutFetch(t.trainType, null, t.updown, t.groupId);
    carHost.innerHTML = '';
    if (!r || !r.ok) { carHost.appendChild(txt('div', 'train-list-msg', (r && r.error) || '편성을 불러오지 못했습니다')); return; }
    const chips = div('car-chips');
    const _dual = (((_groupsById || {})[t.groupId] || {}).bands || []).length > 1;   // 1~8 + 11~18 = 중련 가능
    // 중련이면 11~18호차는 1~8호차와 같은 자리다(두 번째 편성). 별도 칩으로 나열하지 않고
    // 기본 1~8호차 칩에 '(11호차)'처럼 병기만 한다. 예매 시 실제 편성 번호대로 자동 변환(align_cand_cars).
    const baseCars = _dual ? (r.cars || []).filter(c => parseInt(c.car_no, 10) <= 8) : (r.cars || []);
    baseCars.forEach(c => {
      const n = parseInt(c.car_no, 10);
      const mirror = _dual && n <= 8 ? n + 10 : null;
      chips.appendChild(makeCarChip(c, parseInt(curCar, 10) === n,
        () => { curCar = c.car_no; drawCarChips(chips); drawMap(); }, mirror));
    });
    carHost.appendChild(wrapHScroll(chips));
    if ((!curCar || parseInt(curCar, 10) > 8 && _dual) && baseCars.length) curCar = baseCars[0].car_no;
    drawCarChips(chips); drawMap();
    // (배경 프리페치 제거 — 캐시 미스 시 호차마다 재로그인+검색이 폭주했다. 클릭 시에만 조회한다. 2026-09-09)
  }
  function drawCarChips(chips) {
    [...chips.children].forEach(ch => {
      const no = parseInt(ch.dataset.no || ch.textContent, 10);
      ch.classList.toggle('on', parseInt(curCar, 10) === no);
    });
  }
  async function drawMap() {
    if (!curCar) return;
    // 로딩·호차 전환 때 높이가 줄었다 늘어나지 않게, 직전 높이를 최소높이로 고정한다(2026-09-09 지시).
    const held = mapHost.offsetHeight;
    if (held > 40) mapHost.style.minHeight = held + 'px';
    const key = t.trainType + '|' + t.updown + '|' + parseInt(curCar, 10);
    if (!_layoutCache[key]) loading(mapHost, '좌석 배치 불러오는 중…');
    const r = await _layoutFetch(t.trainType, parseInt(curCar, 10), t.updown, t.groupId);
    mapHost.innerHTML = '';
    if (!r || !r.ok) { mapHost.appendChild(txt('div', 'train-list-msg', (r && r.error) || '좌석 배치를 불러오지 못했습니다')); return; }
    const seats = r.seats || [];   // 방향별로 실제 열차를 조회해 오므로 뒤집지 않는다
    _seatCells = {};
    renderSeatTable(mapHost, seats, {
      dirNote: `열차 진행 방향 →  ${t.updown === 'down' ? '하행' : '상행'}`,
      isSel: x => rankOf(curCar, x.label) > 0,
      badge: x => { const rk = rankOf(curCar, x.label); return rk ? String(rk) : ''; },
      onPick: x => toggleSeat(curCar, x),
      onCell: (x, cell) => { _seatCells[x.label] = cell; },
    });
    // 렌더된 실제 높이로 고정값을 맞춘다 — 이후 호차 전환/로딩에서 이 높이를 유지
    requestAnimationFrame(() => { const h = mapHost.scrollHeight; if (h > 40) mapHost.style.minHeight = h + 'px'; });
  }

  const _initialCand = JSON.stringify(t.seats);
  _editModal(tpl ? '선호 좌석 수정' : '선호 좌석 추가', (body) => {
    const c = makeGridCard();
    // 열차 종류 × 진행 방향을 한 항목으로 — 좌석 배치는 이 둘로 결정된다(열차번호는 무관)
    const combos = [];
    _TRAIN_TYPES.forEach(ty => { combos.push([ty + '|down', `${ty} (하행)`]); combos.push([ty + '|up', `${ty} (상행)`]); });
    const _lock = (lockTrain !== undefined) ? lockTrain : !!tpl;   // 수정·기본행은 열차 고정(배치가 달라지면 순위가 깨진다)
    if (_lock) {
      // 고를 수 없는 값이라 드롭다운처럼 보이면 안 된다(파란 글씨 = 누를 수 있다는 신호였다).
      // 이름도 목록과 같은 실제 편성 이름으로 — 'KTX-산천'이 아니라 'KTX-산천A (상행)'(2026-09-10).
      const g0 = t.groupId ? (_groupsById || {})[t.groupId] : null;
      const nameTxt = g0 ? _groupLabel(g0)
                         : `${t.trainType} (${t.updown === 'up' ? '상행' : '하행'})`;
      appendGridFull(c, '열차', txt('div', 'picker-fixed', nameTxt));
    } else {
      typeF = makeSheetPicker(combos, `${t.trainType}|${t.updown}`, v => {
        const [ty, ud] = String(v).split('|');
        t.trainType = ty; t.updown = ud; curCar = null; loadCars();
      });
      typeF.el.classList.add('wide-picker');
      appendGridFull(c, '열차', typeF.el);
    }
    body.appendChild(c); body.appendChild(div('sp10'));
    body.appendChild(txt('div', 'sub-label', '좌석맵에서 고르기'));
    const c2 = makeGridCard(); const cell = div('grid-cell-stack');
    carHost = div(''); mapHost = div('seat-grid-wrap');
    cell.append(carHost, mapHost); c2.appendChild(cell); body.appendChild(c2);
    body.appendChild(div('sp10'));
    {   // 제목 줄 오른쪽에 '모두 삭제'(박스 없이 붉은 글씨만)
      const head3 = div('sub-label-row');
      head3.appendChild(txt('div', 'sub-label', '희망 순위 (위에서부터 1순위, 최대 10개)'));
      const clr = el('button', 'link-danger'); clr.type = 'button'; clr.textContent = '모두 삭제';
      clr.addEventListener('click', async () => {
        if (!t.seats.length) { notify('등록된 좌석이 없습니다.', clr); return; }
        if (!(await askConfirm('희망 순위를 모두 지울까요?', clr))) return;
        t.seats = []; drawSeats(); refreshRanks();
      });
      head3.appendChild(clr);
      body.appendChild(head3);
    }
    const c3 = makeGridCard(); const cell3 = div('grid-cell-stack');
    listWrap = div('cand-list'); cell3.appendChild(listWrap); c3.appendChild(cell3); body.appendChild(c3);
    body.appendChild(div('sp10'));
    drawSeats(); loadCars();
  }, () => {
    if (!t.seats.length) { notify('희망 좌석을 1개 이상 등록하세요'); return false; }
    onSave({ id: t.id || (Date.now() + '' + Math.floor(Math.random() * 999)),
             name: `${t.trainType} ${t.updown === 'up' ? '상행' : '하행'}`,
             trainType: t.trainType, updown: t.updown, fallback: 'auto', seats: t.seats });   // 실패 시 선호 옵션 조건으로 예매
  }, { confirmDiscard: () => (JSON.stringify(t.seats) !== _initialCand)
        ? '수정 사항을 저장하지 않고 닫으시겠습니까?' : '' });
}

function _saveNcardFrom(list) {
  const c = (list || []).filter(x => x && x.ok)[0];
  if (!c || !c.card_no) return;
  const seg = (c.segments || [])[0] || {};
  lsSetPay({ ncardNo: c.card_no, ncard: { no: c.card_no, name: c.name, dep: seg.dep, arr: seg.arr, valid_to: c.valid_to, used: c.used, remaining: c.remaining, ticket_no: c.ticket_no } });
}

function buildSettingsPage() {
  const panel = document.getElementById('settings-panel');
  panel.appendChild(div('sp10'));
  panel.appendChild(txt('div', 'group-header', '계정 · 결제'));
  const card = makeGridCard();
  // 값 오른쪽에 작은 버튼(로그아웃 등)을 둘 수 있는 행
  function row(label, getVal, onClick, btn, plain) {
    const cell = div('grid-cell-full acct-row');
    const val = txt('div', 'acct-state', '');
    const right = div(''); right.classList.add('u-row8'); right.appendChild(val);
    if (btn) {
      const b = el('button', 'acct-mini-btn'); b.type = 'button'; b.textContent = btn.text;
      b.addEventListener('click', ev => { ev.stopPropagation(); btn.onClick(b); });
      right.appendChild(b);
    }
    cell.append(txt('div', 'grid-cell-label', label), right);
    if (onClick) cell.addEventListener('click', onClick); else cell.style.cursor = 'default';
    const refresh = () => {
      const v = getVal(); val.textContent = v || '정보 등록하기';
      val.style.color = (v && plain) ? 'var(--text)' : 'var(--blue)';   // 누를 수 없는 값은 검정
    };
    refresh(); return { cell, refresh };
  }
  // 구글 로그인 계정 — /api/me 로 채운다
  const me = { email: '', admin: false };
  const r0 = row('로그인 계정', () => me.email, null, null, true);
  const r1 = row('KTX 계정', () => lsGetAccount('ktx').memberNo || '', () => _ktxInfoSheet());   // 계정 정보 팝업
  const r2 = row('KTX 결제 수단', () => ktxPayConfigured() ? _payMethodLabel() : '', () => _ktxPayModal(() => r2.refresh()));
  card.append(r0.cell, div('grid-divider-h'), r1.cell, div('grid-divider-h'), r2.cell);
  _renderSettings = () => {   // /api/me 결과로 로그인 계정 행을 채운다
    if (window.__DEMO__) { me.email = 'demo@example.com'; r0.refresh(); return; }
    fetch('/api/me').then(r => r.json()).then(d => {
      me.email = (d && d.email) || '';
      me.admin = !!(d && d.admin);
      if (d && d.email && !d.approved && !d.admin) me.email = d.email + ' · 승인 대기 중';
      else if (me.admin) me.email = d.email + ' (관리자)';
      r0.refresh();
      if (me.admin && !card.querySelector('.acct-admin')) {   // 관리자에게만
        const a = div('grid-cell-full acct-row acct-admin');
        a.append(txt('div', 'grid-cell-label', '관리자 페이지'), txt('div', 'acct-state', '열기'));
        a.addEventListener('click', () => _adminSheet());
        card.append(div('grid-divider-h'), a);
      }
    }).catch(() => { me.email = '정보를 불러올 수 없습니다'; r0.refresh(); });
  };
  panel.appendChild(card);

  // 선호 옵션 기본값 — 예매 탭이 이 값으로 시작한다
  panel.appendChild(div('sp18'));
  panel.appendChild(txt('div', 'group-header', '선호 옵션 설정'));
  const prefCard = makeGridCard();
  // 예매 탭처럼 요약만 보여주고, 자세한 설정은 팝업에서 한다
  const prefCell = div('grid-cell-full acct-row');
  const prefVal = div('etc-chips');
  const prefGear = el('button', 'etc-gear'); prefGear.type = 'button'; prefGear.title = '선호 옵션 수정';
  prefGear.innerHTML = _ICON_GEAR;
  prefGear.addEventListener('click', ev => { ev.stopPropagation(); _prefSheet(); });
  const prefWrap = div('etc-wrap'); prefWrap.append(prefVal, prefGear);
  prefCell.append(txt('div', 'grid-cell-label', '선호 옵션'), prefWrap);
  prefCell.addEventListener('click', () => _prefSheet());
  prefCard.appendChild(prefCell);
  panel.appendChild(prefCard);
  function renderPrefSummary() {
    const p = lsGetSeatPref();
    prefVal.innerHTML = '';
    const chips = [_seatLabel(p.seatClass), _optLabel(_SIDE_OPTS, p.seatSide), _optLabel(_DIR_OPTS, p.seatDir)];
    if (p.allowWaiting) chips.push('예약대기 허용');
    if (p.allowStanding) chips.push('입석 허용');
    chips.filter(Boolean).forEach(c => prefVal.appendChild(txt('span', 'etc-chip', c)));
  }
  function _prefSheet() {
    const draft = lsGetSeatPref();   // 저장을 눌러야 반영된다
    _editModal('선호 옵션 설정', (body) => {
      const listCard = div('etc-list');
      const chipRow = (label, opts, key) => {
        if (listCard.children.length) listCard.appendChild(div('grid-divider-h'));
        const cell = div('grid-cell-full'); cell.classList.add('chip-row');
        const chip = makeChipGroup(opts, () => draft[key], v => { draft[key] = v; });
        const act = div('grid-cell-action'); act.appendChild(chip.el);
        cell.append(txt('div', 'grid-cell-label', label), act);
        listCard.appendChild(cell);
      };
      const toggleRow = (label, key, note) => {
        listCard.appendChild(div('grid-divider-h'));
        const cell = div('grid-cell-full');
        const cb = makeCheckbox(draft[key]);
        const act = div('grid-cell-action'); act.appendChild(cb);
        cell.append(txt('div', 'grid-cell-label', label), act);
        listCard.appendChild(cell);
        const n = txt('div', 'etc-note', note);   // 예매 탭과 같이 — 체크했을 때만 보인다
        n.style.display = draft[key] ? '' : 'none';
        listCard.appendChild(n);
        cb.addEventListener('change', () => { draft[key] = cb.checked; n.style.display = cb.checked ? '' : 'none'; });
      };
      chipRow('좌석등급', _SEAT_OPTS, 'seatClass');
      chipRow('창측 / 내측', _SIDE_OPTS, 'seatSide');
      chipRow('진행 방향', _DIR_OPTS, 'seatDir');
      toggleRow('예약대기 허용', 'allowWaiting', '좌석이 없으면 예약대기로 걸어 둡니다. 예약대기는 자동결제가 되지 않습니다.');
      toggleRow('입석 허용', 'allowStanding', '좌석이 없으면 입석으로 잡습니다.');
      body.appendChild(listCard);
    }, () => { lsSetSeatPref(draft); renderPrefSummary(); });
  }
  renderPrefSummary();
  document.addEventListener('ktx-seatpref', renderPrefSummary);
  // 내 정보(코레일) — 섹션으로 두지 않고 '계정 정보 자세히보기'에서 연다
  const miCard = makeGridCard();
  const miBody = div(''); miBody.style.gridColumn = '1 / -1'; miCard.appendChild(miBody);
  function _miRow(label, val) { const cell = div('grid-cell-full acct-row mi-row'); cell.style.cursor = 'default'; const dv = miBody.children.length ? div('grid-divider-h') : null; if (dv) miBody.appendChild(dv); cell.append(txt('div', 'grid-cell-label', label), txt('div', 'acct-state', val)); miBody.appendChild(cell); }
  function renderMyInfo(d) {
    miBody.innerHTML = '';
    if (!d) { _miRow('내 정보', '불러오는 중…'); return; }
    if (!d.ok) { _miRow('오류', d.error || '조회 실패'); const c = div('grid-cell-stack'); const b = el('button', 'btn-primary'); b.textContent = '다시 시도'; b.className += ' btn-ghost'; b.classList.add('u-btn-wide'); b.addEventListener('click', loadMyInfo); c.appendChild(b); miBody.appendChild(c); return; }
    _miRow('이름', (d.name || '') + (d.sex ? ` (${d.sex})` : ''));
    _miRow('회원번호', d.member_no || '');
    _miRow('휴대폰', d.phone || '-');
    _miRow('이메일', d.email || '-');
    _miRow('코레일 포인트', (d.rail_point != null ? d.rail_point.toLocaleString() + 'P' : '-'));
    _miRow('할인쿠폰', (d.coupon_cnt || 0) + '장');
    const ncs = (d.ncards || []).filter(c => c.ok);
    _saveNcardFrom(d.ncards);
    if (ncs.length) ncs.forEach(c => { const seg = (c.segments || [])[0] || {}; _miRow('N카드', `${seg.dep || '?'}↔${seg.arr || '?'} · ~${(c.valid_to || '').replace(/(\d{4})(\d{2})(\d{2})/, '$2/$3')} · 미사용 ${c.remaining}회`); });
    else _miRow('N카드', '없음');
  }
  // 코레일 호출 사용량(과호출 방어 관문)
  const gaCell = div('grid-cell-full acct-row'); gaCell.style.cursor = 'default';
  gaCell.append(txt('div', 'grid-cell-label', '코레일 호출'), txt('div', 'acct-state', '-'));
  function loadGate() {
    const v = gaCell.lastElementChild;
    api('GET', '/gate').then(d => {
      if (!d || !d.ok) { v.textContent = '-'; return; }
      v.textContent = d.cooling > 0 ? `보호 중 · ${d.cooling}초 후 재개` : `${d.per_min}회/분 (상한 ${d.limit_per_min})`;
      v.style.color = d.cooling > 0 ? 'var(--red)' : (d.per_min > d.limit_per_min * 0.8 ? '#E8590C' : '');
    }).catch(() => { v.textContent = '-'; });
  }
  function loadMyInfo() {
    if (window.__DEMO__) {
      renderMyInfo({ ok: true, name: '홍길동', sex: '남', member_no: '1234567890',
        phone: '010-****-5678', email: 'demo@example.com', rail_point: 12340, coupon_cnt: 2,
        ncards: [{ ok: true, segments: [{ dep: '용산', arr: '광주송정' }], valid_to: '20260930', remaining: 9 }] });
      return;
    }
    const a = lsGetAccount('ktx');
    if (!a.memberNo || !a.password) { renderMyInfo({ ok: false, error: '계정을 먼저 등록하세요' }); return; }
    loadingBox('조회 중…', miBody);
    api('POST', '/myinfo', { member_no: a.memberNo, password: a.password }).then(renderMyInfo).catch(() => renderMyInfo({ ok: false, error: '조회 실패' }));
  }
  renderMyInfo(null);
  _loadMyInfo = loadMyInfo;
  // 코레일 조회는 이 시트를 열 때만 한다(설정 탭을 연다고 부르지 않는다)
  _ktxInfoSheet = () => {
    if (!lsGetAccount('ktx').memberNo && !window.__DEMO__) { notify('KTX 계정을 먼저 등록하세요.'); return; }
    const ov = div('picker-overlay'); const sheet = div('picker-sheet'); sheet.classList.add('u-sheet-pad');
    sheet.appendChild(txt('div', 'wheel-title', 'KTX 계정 정보'));
    sheet.appendChild(miCard);
    const done = () => { if (ov.parentNode) document.body.removeChild(ov); panel.appendChild(miCard); miCard.style.display = 'none'; };
    const bar = div('preset-actions'); bar.style.marginTop = '12px';
    const pw = el('button', 'btn-primary'); pw.textContent = '비밀번호 변경'; pw.classList.add('btn-ghost');
    pw.addEventListener('click', () => { done(); _ktxAccountModal(() => r1.refresh()); });
    const out = el('button', 'btn-primary'); out.textContent = '로그아웃'; out.classList.add('btn-ghost', 'btn-danger');
    out.addEventListener('click', async () => {
      if (!(await askConfirm('KTX 계정 정보를 지울까요? 연결된 기기에서도 다시 로그인해야 합니다.', out))) return;
      done(); ktxLogout();
    });
    bar.append(pw, out); sheet.appendChild(bar);
    const close = { click: done };
    ov.appendChild(sheet);
    ov.addEventListener('click', e => { if (e.target === ov) close.click(); });
    document.body.appendChild(ov);
    miCard.style.display = '';
    loadMyInfo(); loadGate();
  };
  miCard.style.display = 'none';
  panel.appendChild(miCard);   // 평소엔 감춰 둔다(시트를 열 때만 보인다)
  document.addEventListener('ktx-acct-sync', () => { if (miCard.style.display !== 'none') loadGate(); });
  panel.appendChild(div('sp18'));
  // 좌석 후보 — 예매 탭 '후보 등록'이 쓰는 우선순위 목록
  {
    panel.appendChild(txt('div', 'group-header', '선호 좌석 설정'));
  }
  const candWrap = div(''); panel.appendChild(candWrap);
  // 기본 4종(KTX·KTX-산천 × 상·하행)은 등록 여부와 상관없이 항상 보여준다.
  // 좌석 배치 그룹 단위로 등록한다 — 같은 열차종류라도 편성이 다르면 좌석 배치가 다르다.
  // 편성 판별은 조회 응답의 h_trn_clsf_cd 로 한다(07/0A/10). 목록은 이름 오름차순, 상행 먼저.
  function renderCand() {
    candWrap.innerHTML = '';
    candWrap.appendChild(loadingBox('불러오는 중…'));
    Promise.all([_seatGroups(), _ridesByGroup()]).then(([groups, ridden]) => {
      candWrap.innerHTML = '';
      const card = makeGridCard();
      if (!groups.length) {
        card.appendChild(txt('div', 'hint', '좌석 배치 정보를 불러오지 못했습니다'));
        candWrap.appendChild(card); return;
      }
      const list = _candLoad();
      const known = new Set(groups.map(g => g.id));
      const extra = list.filter(x => x.groupId && !known.has(x.groupId)).map(x => ({
        id: x.groupId, name: x.trainType || '기타', train_type: x.trainType || '',
        updown: x.updown || 'down', trains: [], _extra: true,
      }));
      const rows = groups.slice().sort((a, b) =>
        a.name.localeCompare(b.name, 'ko') || (a.updown === 'up' ? -1 : 1)).concat(extra);

      rows.forEach((g, i) => {
        if (i) card.appendChild(div('grid-divider-h'));
        const t = list.find(x => x.groupId === g.id);
        const mirrored = _isMirrored(g.id);
        const cell = div('grid-cell-full acct-row mi-row cand-grp');
        const nameEl = div('grid-cell-label'); nameEl.appendChild(_groupLabelEl(g));
        cell.appendChild(nameEl);
        const rc = ridden[g.id] || 0;
        if (rc) {
          const b = txt('span', 'cand-ride', `${rc}회`);
          hoverTip(b, '최근 3개월 동안 이용한 횟수입니다');
          cell.appendChild(b);
        }
        const seats = (t && t.seats) || [];
        cell.appendChild(seats.length
          ? txt('div', 'acct-state cand-row-seats', seats.map(x => `${parseInt(x.car_no, 10)}호${x.label || x.seat_no}`).join(', '))
          : txt('div', 'acct-state cand-empty', '선호 좌석을 등록하세요'));
        cell.addEventListener('click', () => {
          if (mirrored) {   // 짝 편성은 직접 못 고친다 — 이유를 토스트로
            const src = Object.keys(_mirror).find(k => _mirror[k] === g.id) || '';
            const sn = (_groupsById[src] || {}).name || '';
            notify(`${sn}과 같은 편성입니다. 4·5호차만 맞바꿔 자동으로 채워집니다`);
            return;
          }
          _candEditor(t || { groupId: g.id, trainType: g.train_type, updown: g.updown, fallback: 'watch', seats: [] },
            o => { o.groupId = g.id; const l = _candLoad();
                   const k = l.findIndex(x => x.groupId === g.id);
                   if (k >= 0) l[k] = o; else l.push(o);
                   const mid = _mirrorOf(g.id);
                   if (mid) {   // 4·5호차만 바꿔 짝 편성에 그대로 넣는다
                     const mo = { ...o, id: (o.id || '') + '-m', groupId: mid, seats: _mirrorSeats(o.seats) };
                     const mk = l.findIndex(x => x.groupId === mid);
                     if (mk >= 0) l[mk] = mo; else l.push(mo);
                   }
                   _candSave(l); renderCand(); }, true);
        });
        card.appendChild(cell);
      });
      candWrap.appendChild(card);
    });
  }
  renderCand();
  panel.appendChild(div('sp18'));
  // 프로그램 업데이트 — 윈도우 단독 실행에서만 나온다(웹은 새로고침이 곧 업데이트)
  const updHead = txt('div', 'group-header', '프로그램'); updHead.style.display = 'none';
  const updCard = makeGridCard(); updCard.style.display = 'none';
  panel.append(updHead, updCard);
  const updSpacer = div('sp18'); updSpacer.style.display = 'none'; panel.appendChild(updSpacer);
  _renderUpdateCard = (st) => {
    const on = !!(st && st.enabled);
    [updHead, updCard, updSpacer].forEach(e => e.style.display = on ? '' : 'none');
    if (!on) return;
    updCard.innerHTML = '';

    // 1행: 버전 · 현재 버전 · 확인 버튼
    const row = div('grid-cell-full acct-row');
    row.appendChild(txt('div', 'grid-cell-label', '버전'));
    row.appendChild(txt('div', 'acct-state', st.current || '-'));
    const chk = el('button', 'upd-inline-btn'); chk.type = 'button';
    chk.textContent = st.checking ? '확인 중…' : '업데이트 확인';
    chk.disabled = !!st.checking;
    chk.addEventListener('click', async ev => {
      ev.stopPropagation();
      chk.disabled = true; chk.textContent = '확인 중…';
      let r = await api('POST', '/update/check').catch(() => null);
      for (let i = 0; r && r.checking && i < 30; i++) {
        await new Promise(res => setTimeout(res, 1500));
        r = await api('GET', '/update').catch(() => null);
      }
      _renderUpdateCard(r || st);
      if (!r || !r.ok) { notify('업데이트 확인에 실패했습니다'); return; }
      if (r.error) { notify(`확인 실패: ${r.error}`); return; }
      const newer = r.ready && r.ready !== r.current;
      const exe = r.needs_exe && r.latest && r.latest !== r.current;
      if (newer) notify(`새 버전 ${r.ready} 준비 완료`);
      else if (exe) notify(`새 버전 ${r.latest} — 새 실행 파일이 필요합니다`);
      else notify('최신 버전입니다');
      if (_updLast) renderUpdateHero(r);
    });
    row.appendChild(chk);
    updCard.appendChild(row);

    // 웹 연결 — 이 PC의 설정·결과가 웹(중앙)과 같이 저장되는지
    const hrow = div('grid-cell-full acct-row'); hrow.style.display = 'none';
    hrow.appendChild(txt('div', 'grid-cell-label', '웹 연결'));
    const hstate = txt('div', 'acct-state', '확인 중…'); hrow.appendChild(hstate);
    const hdiv = div('grid-divider-h'); hdiv.style.display = 'none';
    updCard.append(hdiv, hrow);
    api('GET', '/hubstate').then(h => {
      if (!h || !h.local) return;
      hdiv.style.display = ''; hrow.style.display = '';
      hstate.textContent = h.connected ? '연결됨 · 설정을 웹과 함께 저장합니다'
        : (h.linked ? ('연결 대기 · ' + (h.error || '잠시 후 다시 시도합니다'))
                    : '이 PC에만 저장 중 · 구글 로그인하면 웹과 함께 저장됩니다');
    }).catch(() => {});

    // 2행: 새 버전이 있을 때만
    const ready = st.ready && st.ready !== st.current;
    const needsExe = st.needs_exe && st.latest && st.latest !== st.current;
    if (!ready && !needsExe) return;
    updCard.appendChild(div('grid-divider-h'));
    const row2 = div('grid-cell-full acct-row');
    row2.appendChild(txt('div', 'grid-cell-label', `새 버전 ${ready ? st.ready : st.latest}`));
    row2.appendChild(txt('div', 'acct-state', ''));
    if (needsExe) {
      const a = document.createElement('a'); a.className = 'upd-inline-btn blue';
      a.href = 'https://github.com/jdent0228/trainer-releases/releases/latest';
      a.target = '_blank'; a.rel = 'noopener'; a.textContent = '받으러 가기';
      row2.appendChild(a);
    } else {
      const b = el('button', 'upd-inline-btn blue'); b.type = 'button'; b.textContent = '지금 업데이트';
      b.addEventListener('click', ev => { ev.stopPropagation(); _applyUpdate(b); });
      row2.appendChild(b);
    }
    updCard.appendChild(row2);
  };
  _pollUpdate();
  panel.appendChild(div('sp18'));
  // 시스템 정보 — 맨 아래로 모은다(코레일 호출 · 문서)
  panel.appendChild(txt('div', 'group-header', '시스템'));
  {
    const c = makeGridCard();
    c.appendChild(gaCell); panel.appendChild(c); panel.appendChild(div('sp18'));
  }
  // 최하단 구글 로그아웃 — 눈에 띄되 튀지 않게(옅은 붉은 배경)
  {
    const wrap = div('grid-cell-stack'); wrap.style.padding = '0 2px';
    const b = el('button', 'btn-logout'); b.type = 'button'; b.textContent = '로그아웃';
    b.addEventListener('click', async () => {
      if (!(await askConfirm('로그아웃할까요? 다시 구글 로그인을 해야 합니다.', b))) return;
      location.href = '/auth/logout';
    });
    wrap.appendChild(b); panel.appendChild(wrap);
  }
  panel.appendChild(div('sp140'));
}

// 관리자 화면 — 페이지 이동 없이 앱과 같은 모양의 모달로(승인·거절도 여기서)
function _adminSheet() {
  const ov = div('picker-overlay'); const sheet = div('picker-sheet'); sheet.classList.add('u-sheet-pad');
  sheet.appendChild(txt('div', 'wheel-title', '사용자 관리'));
  const bodyEl = div('admin-body'); sheet.appendChild(bodyEl);
  const bar = div('preset-actions'); bar.style.marginTop = '12px';
  const close = el('button', 'btn-primary'); close.textContent = '닫기'; close.classList.add('btn-ghost');
  close.addEventListener('click', () => document.body.removeChild(ov));
  bar.appendChild(close); sheet.appendChild(bar);
  ov.appendChild(sheet);
  ov.addEventListener('click', e => { if (e.target === ov) document.body.removeChild(ov); });
  document.body.appendChild(ov);

  function personRow(u, actions) {
    const cell = div('grid-cell-full acct-row admin-row');
    const who = div('admin-who');
    who.appendChild(txt('div', 'admin-name', u.name || u.email.split('@')[0]));
    who.appendChild(txt('div', 'admin-mail', u.email));
    const act = div(''); act.classList.add('u-row8');
    actions.forEach(([label, action, danger]) => {
      const b = el('button', 'acct-mini-btn' + (danger ? ' danger' : '')); b.type = 'button'; b.textContent = label;
      b.addEventListener('click', async () => {
        if (danger && !(await askConfirm(`${u.name || u.email} 님을 목록에서 제거할까요?`, b))) return;
        b.disabled = true;
        const r = await api('POST', '/admin/user', { email: u.email, action }).catch(() => null);
        if (!r || !r.ok) { notify((r && r.error) || '처리에 실패했습니다.'); b.disabled = false; return; }
        load();
      });
      act.appendChild(b);
    });
    cell.append(who, act); return cell;
  }
  function section(title, list, actions, emptyMsg) {
    const wrap = div('');
    wrap.appendChild(txt('div', 'group-header', `${title} ${list.length}`));
    const card = makeGridCard();
    if (!list.length) card.appendChild(txt('div', 'hint admin-empty', emptyMsg));
    else list.forEach((u, i) => { if (i) card.appendChild(div('grid-divider-h')); card.appendChild(personRow(u, actions)); });
    wrap.appendChild(card); wrap.appendChild(div('sp10'));
    return wrap;
  }
  function load() {
    bodyEl.innerHTML = ''; bodyEl.appendChild(loadingBox('불러오는 중…'));
    api('GET', '/admin/users').then(d => {
      bodyEl.innerHTML = '';
      if (!d || !d.ok) { bodyEl.appendChild(txt('div', 'hint', (d && d.error) || '불러오지 못했습니다')); return; }
      bodyEl.appendChild(section('승인 대기', d.pending || [],
        [['승인', 'approve', false], ['거절', 'reject', true]], '대기 중인 요청이 없습니다'));
      bodyEl.appendChild(section('승인됨', d.approved || [],
        [['제거', 'reject', true]], '승인된 사용자가 없습니다'));
      if ((d.rejected || []).length)
        bodyEl.appendChild(section('거절됨', d.rejected, [['승인', 'approve', false]], ''));
    }).catch(() => { bodyEl.innerHTML = ''; bodyEl.appendChild(txt('div', 'hint', '불러오지 못했습니다')); });
  }
  load();
}

// ── 서버 동기화: 계정별 입력값·프리셋을 DB에 저장 → 다른 기기에서도 자동 로드 ──
// 이 기기에만 두는 값 — 비밀번호 성격의 필드만. 나머지 결제 설정은 계정에 묶여 동기화된다.
// 이 기기에만 남는 값 — 구글 계정 동기화에서 제외한다
const _SECRET_FIELDS = ['payPin', 'cardNo', 'card_no', 'cardPw', 'cardPassword', 'cardExp', 'cardAuthVal'];
// pay.card 안에서 뺄 값. 카드 종류(개인/법인)와 할부 개월은 설정이라 동기화한다.
const _CARD_SECRET_FIELDS = ['cardNo', 'cardExp', 'cardPw', 'cardAuthVal'];
// 동기화에서 빼는 키 — 서버 _SYNC_DROP_* 와 같은 목록이어야 한다.
//  ① 레거시(구모델): 코레일 비밀번호가 평문으로 들어 있고 소유권 검사를 안 거쳐 남의 계정으로 샜다(2026-09-09).
//  ② 기기 캐시: 열차·좌석맵 캐시는 기기마다 다시 채우면 되는데 사용자당 250KB를 계속 실어 날랐다.
const _SYNC_DROP = ['kor_ktx', 'kor_ktx_last', 'kor_receipt', 'kor_ktx_resv',
  'kor_ktx_resv_urgent', 'kor_ktx_resv_normal', 'kor_ktx_resv_special',
  'kor_ktx_traincache', 'kor_ktx_traincache2', 'kor_ktx_seatcache',
  'kor_ktx_tab'];   // 마지막 본 탭은 기기별 상태
function _syncDroppable(k) { return _SYNC_DROP.indexOf(k) >= 0 || k.indexOf('kor_ktx_acct_') === 0; }
function _syncKeyOK(k) { return /^kor_(ktx|receipt)/.test(k) && !_syncDroppable(k); }
// 키별 최종 수정 시각 — 두 기기를 함께 쓸 때 "더 최신인 쪽만 남기기"의 기준
function _syncTsLoad() { try { return JSON.parse(localStorage.getItem('kor_sync_ts') || '{}') || {}; } catch { return {}; } }
function _syncTsSave(o) { try { localStorage.setItem('kor_sync_ts', JSON.stringify(o)); } catch {} }
function _syncHashLoad() { try { return JSON.parse(localStorage.getItem('kor_sync_h') || '{}') || {}; } catch { return {}; } }
function _syncHashSave(o) { try { localStorage.setItem('kor_sync_h', JSON.stringify(o)); } catch {} }
function _cheapHash(v) { const t = String(v == null ? '' : v); let h = 5381; for (let i = 0; i < t.length; i++) h = ((h * 33) ^ t.charCodeAt(i)) >>> 0; return t.length + ':' + h; }
// 이전 마이그레이션이 끝난 기기에서 레거시 키를 지운다(자격증명이 남아 있는 통로를 없앤다)
function _syncDropLegacyLocal() {
  try {
    if (!localStorage.getItem('kor_ktx_account')) return;   // 아직 이전 전이면 원본을 남겨 둔다
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const k = localStorage.key(i);
      if (k && _syncDroppable(k) && k.indexOf('traincache') < 0 && k.indexOf('seatcache') < 0) localStorage.removeItem(k);
    }
  } catch {}
}
function _stripSecrets(v) {   // JSON 문자열에서 비밀 필드만 뺀다
  try {
    const o = JSON.parse(v || 'null');
    if (!o || typeof o !== 'object') return v;
    _SECRET_FIELDS.forEach(f => { delete o[f]; });
    if (o.pay && typeof o.pay === 'object') {
      _SECRET_FIELDS.forEach(f => { delete o.pay[f]; });
      if (o.pay.card && typeof o.pay.card === 'object') _CARD_SECRET_FIELDS.forEach(f => { delete o.pay.card[f]; });
    }
    return JSON.stringify(o);
  } catch { return v; }
}
function _keepSecrets(incoming, localRaw) {   // 받은 값에 이 기기의 비밀 필드를 되살린다
  try {
    const o = JSON.parse(incoming || 'null');
    const l = JSON.parse(localRaw || 'null');
    if (!o || typeof o !== 'object') return incoming;
    if (l && typeof l === 'object') {
      _SECRET_FIELDS.forEach(f => { if (l[f] !== undefined) o[f] = l[f]; });
      if (l.pay && typeof l.pay === 'object') {
        o.pay = Object.assign({}, o.pay || {});
        _SECRET_FIELDS.forEach(f => { if (l.pay[f] !== undefined) o.pay[f] = l.pay[f]; });
        if (l.pay.card && typeof l.pay.card === 'object') {
          o.pay.card = Object.assign({}, o.pay.card || {});
          _CARD_SECRET_FIELDS.forEach(f => { if (l.pay.card[f] !== undefined) o.pay.card[f] = l.pay.card[f]; });
        }
      }
    }
    return JSON.stringify(o);
  } catch { return incoming; }
}
// 이 브라우저가 마지막으로 동기화한 구글 계정. 계정이 바뀌면 남은 데이터를 지운다.
// 안 지우면 이전 계정의 코레일 회원번호·비밀번호가 그대로 보이고, 곧 새 계정 쪽으로
// 다시 저장돼 버린다(2026-09-09 실측 사고).
function _wipeLocalAppData() {
  try {
    Object.keys(localStorage).filter(k => _syncKeyOK(k) || /^kor_/.test(k))
      .forEach(k => { if (k !== 'kor_sync_owner') localStorage.removeItem(k); });
  } catch {}
}
async function _syncOwnerGuard() {
  try {
    const me = await fetch('/api/me').then(r => r.json());
    const who = (me && me.email) || '';
    if (!who) return;                       // 로그인 정보를 모르면 손대지 않는다
    const prev = localStorage.getItem('kor_sync_owner');
    if (prev && prev !== who) {
      _wipeLocalAppData();
      console.warn('[동기화] 로그인 계정이 바뀌어 이 기기의 이전 설정을 지웠습니다');
    }
    localStorage.setItem('kor_sync_owner', who);
  } catch {}
}

async function _syncPull() {
  if (window.__DEMO__) return;   // 데모: 서버 데이터 안 불러옴(실계정 정보 노출 방지)
  await _syncOwnerGuard();
  try {
    // 화면 만들기가 이 요청에 매여 있다 — 서버가 늦으면 기다리지 말고 그냥 진행한다
    // (2026-09-10: exe에서 중앙 서버가 느릴 때 앱 전체가 먹통이 됐다)
    const r = await Promise.race([fetch('/api/sync'),
      new Promise((_, rej) => setTimeout(() => rej(new Error('sync timeout')), 5000))]);
    const j = await r.json();
    if (j && j.ok && j.data && typeof j.data === 'object') {
      // 통째로 덮으면 ① 이 기기의 PIN·카드번호가 날아가고 ② 이 기기가 방금 바꾼 값이 옛 값으로 되돌아간다.
      // 비밀 필드는 남기고, 키마다 시각을 견줘 더 최신인 쪽만 받는다(2026-09-10).
      const sts = (j.ts && typeof j.ts === 'object') ? j.ts : {}, lts = _syncTsLoad(), hs = _syncHashLoad();
      Object.keys(j.data).forEach(k => {
        if (!_syncKeyOK(k)) return;
        const have = localStorage.getItem(k);
        const st = +(sts[k] || 0), lt = +(lts[k] || 0);
        if (have != null && st <= lt) return;   // 이 기기 값이 같거나 더 최신 — 그대로 둔다
        let v = j.data[k];
        try { v = _keepSecrets(v, have); } catch {}
        try { localStorage.setItem(k, v); lts[k] = st || Date.now(); hs[k] = _cheapHash(_stripSecrets(v)); } catch {}
      });
      _syncTsSave(lts); _syncHashSave(hs);
    }
    _syncDropLegacyLocal();
  } catch {}
}
let _syncT = null;
function syncPush() {
  if (window.__DEMO__) return;   // 데모: 서버에 저장 안 함(실데이터 덮어쓰기 방지)
  clearTimeout(_syncT);
  _syncT = setTimeout(() => {
    const data = {}, ts = _syncTsLoad(), hs = _syncHashLoad(), now = Date.now();
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (!_syncKeyOK(k)) continue;
      const v = _stripSecrets(localStorage.getItem(k));
      data[k] = v;
      const h = _cheapHash(v);
      if (hs[k] !== h) { hs[k] = h; ts[k] = now; }   // 이 기기에서 실제로 바뀐 키만 시각 갱신
    }
    _syncTsSave(ts); _syncHashSave(hs);
    fetch('/api/sync', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ data, ts }) }).catch(() => {});
  }, 800);
}

// 서버에서 먼저 불러온 뒤(미리 입력값 채움) UI를 만든다
// 구버전(메인폼 계정) 데이터 → 신모델(설정 계정/조건)로 1회 이전 — 데이터 손실 방지
function _migrateAccountModel() {
  try {
    if (!localStorage.getItem('kor_ktx_account')) {
      const last = localStorage.getItem('kor_ktx_last');
      const raw = last && localStorage.getItem('kor_ktx_acct_' + last);
      const d = raw ? JSON.parse(raw) : null;
      if (d && d.memberNo) {
        localStorage.setItem('kor_ktx_account', JSON.stringify({ memberNo: d.memberNo, password: d.password || '' }));
        if (!localStorage.getItem('kor_ktx_pay')) {
          const rc = (() => { try { return JSON.parse(localStorage.getItem('kor_receipt') || 'null') || {}; } catch { return {}; } })();
          localStorage.setItem('kor_ktx_pay', JSON.stringify({
            method: d.payMethod || 'cash', payPin: d.payPin || '', smartTicket: d.smartTicket !== false, useMileage: !!d.useMileage,
            cashReceipt: !!(d.cashReceipt ?? rc.cashReceipt), receiptType: d.receiptType || rc.receiptType || 'personal',
            bizIdKind: d.bizIdKind || rc.bizIdKind || 'biz', phone: d.phone || rc.phone || '', bizNo: d.bizNo || rc.bizNo || '',
          }));
        }
        if (!localStorage.getItem('kor_ktx_resv')) {
          localStorage.setItem('kor_ktx_resv', JSON.stringify({ dep: d.dep, arr: d.arr, date: d.date, startTime: d.startTime, endTime: d.endTime, adults: d.adults, interval: d.interval, seatClass: d.seatClass, allowWaiting: d.allowWaiting, runMode: d.runMode, useMileage: d.useMileage }));
        }
      }
    }
    // 직전 모델(계정에 payPin/스마트티켓, 별도 kor_receipt)을 결제정보(kor_ktx_pay)로 통합
    if (!localStorage.getItem('kor_ktx_pay')) {
      const ja = (() => { try { return JSON.parse(localStorage.getItem('kor_ktx_account') || 'null') || {}; } catch { return {}; } })();
      const rc = (() => { try { return JSON.parse(localStorage.getItem('kor_receipt') || 'null') || {}; } catch { return {}; } })();
      if (ja.payPin || rc.cashReceipt !== undefined || rc.phone || rc.bizNo) {
        localStorage.setItem('kor_ktx_pay', JSON.stringify({
          method: 'cash', payPin: ja.payPin || '', smartTicket: ja.smartTicket !== false, useMileage: false,
          cashReceipt: !!rc.cashReceipt, receiptType: rc.receiptType || 'personal', bizIdKind: rc.bizIdKind || 'biz', phone: rc.phone || '', bizNo: rc.bizNo || '',
        }));
      }
    }
    // 여러 계정 모델을 썼던 흔적(kor_ktx_accounts)이 있으면 선택 계정 하나만 kor_ktx_account로 복원
    try {
      const arr = JSON.parse(localStorage.getItem('kor_ktx_accounts') || 'null');
      if (Array.isArray(arr) && arr.length) {
        const selId = localStorage.getItem('kor_ktx_account_sel');
        const sel = arr.find(a => a.id === selId) || arr[0];
        localStorage.setItem('kor_ktx_account', JSON.stringify({ nick: sel.nick || '', memberNo: sel.memberNo, password: sel.password || '', pay: sel.pay || (sel.payPin ? { method: 'cash', payPin: sel.payPin } : _lsLegacyPay()) }));
      }
      localStorage.removeItem('kor_ktx_accounts'); localStorage.removeItem('kor_ktx_account_sel');
    } catch {}
    // 단일 계정에 결제정보(pay)가 없으면 레거시 kor_ktx_pay로 채움(계정=로그인+결제 한 세트)
    {
      const a2 = (() => { try { return JSON.parse(localStorage.getItem('kor_ktx_account') || 'null') || {}; } catch { return {}; } })();
      if (a2.memberNo && !a2.pay) { a2.pay = _lsLegacyPay(); localStorage.setItem('kor_ktx_account', JSON.stringify(a2)); }
    }
    // 탭별 조건 키(kor_ktx_resv_<mode>) — 기존 단일 kor_ktx_resv를 세 탭에 복사
    const old = localStorage.getItem('kor_ktx_resv');
    if (old) ['urgent', 'normal', 'special'].forEach(m => { if (!localStorage.getItem('kor_ktx_resv_' + m)) localStorage.setItem('kor_ktx_resv_' + m, old); });
    // SRT는 코레일 통합(2026-09)으로 제거 — 남은 SRT 저장값(계정·카드정보) 정리
    Object.keys(localStorage).filter(k => /^kor_srt_/.test(k)).forEach(k => localStorage.removeItem(k));
  } catch {}
}

// 코레일 계정이 없으면 예매 화면 대신 계정 입력 화면을 먼저 보여준다.
// (구글 로그인만으로는 예매를 할 수 없다 — 코레일 자격증명이 있어야 조회·예약이 된다)
function showOnboarding(onDone) {
  const host = document.getElementById('onboard');
  const app = document.getElementById('app');
  if (!host || !app) { onDone(); return; }
  app.style.display = 'none';
  host.style.display = '';
  host.innerHTML = '';

  const wrap = div('onb-wrap');
  wrap.appendChild(txt('div', 'onb-title', '기차놀이'));
  wrap.appendChild(txt('div', 'onb-desc', '시작하려면 계정을 등록하세요.\n등록한 정보는 이 기기에만 저장됩니다.'));

  const card = makeGridCard();
  // 회원번호·휴대폰번호·이메일을 모두 받는다 — 숫자만 남기면 하이픈·이메일을 못 넣는다(2026-09-10)
  const memInp = makeTextInput('', 150, '회원번호 또는 휴대폰번호');
  const pwInp = makePwInput('', '비밀번호');
  appendGridFull(card, '회원번호/휴대폰', memInp);
  appendGridFull(card, '비밀번호', pwInp);
  wrap.appendChild(card);

  const warn = txt('div', 'warn-line', ''); warn.style.display = 'none'; warn.style.marginTop = '12px';
  wrap.appendChild(warn);

  const btn = makePrimaryButton('시작하기', 'blue', async () => {
    const memberNo = memInp.value.trim();
    if (!memberNo || !pwInp.value) {
      warn.textContent = '회원번호와 비밀번호를 모두 입력하세요';
      warn.style.display = ''; return;
    }
    if (!_loginIdKind(memberNo)) {   // 형식부터 틀리면 코레일에 물어보지 않는다
      warn.textContent = '회원번호 또는 휴대폰번호 형식이 아닙니다. 다시 확인해 주세요';
      warn.style.display = ''; setTimeout(() => memInp.focus(), 50); return;
    }
    // 먼저 코레일에 실제로 로그인되는지 확인한다. 틀리면 이 화면에 그대로 머문다(2026-09-10 지시)
    warn.style.display = 'none';
    btn.disabled = true; const _t = btn.textContent; btn.textContent = '확인 중…';
    const r = await api('POST', '/myinfo', { member_no: memberNo, password: pwInp.value }).catch(() => null);
    btn.disabled = false; btn.textContent = _t;
    if (!r || !r.ok) {
      warn.textContent = (r && r.error) || '로그인에 실패했습니다. 다시 입력해주세요';
      warn.style.display = '';
      pwInp.value = ''; setTimeout(() => memInp.focus(), 50);
      return;
    }
    lsSetAccount('ktx', { memberNo, password: pwInp.value, name: (r.name || '').trim() });
    host.style.display = 'none'; host.innerHTML = '';
    app.style.display = '';
    onDone();
  });
  btn.style.marginTop = '16px';
  wrap.appendChild(btn);
  wrap.appendChild(txt('div', 'onb-foot', '문의 jdent0228@gmail.com'));
  host.appendChild(wrap);
  setTimeout(() => memInp.focus(), 100);
}

// ── 튜토리얼 스포트라이트 ─────────────────────────────────────
// 대상 요소만 남기고 화면을 어둡게 덮는다. 아무 데나 누르면 닫힌다.
function showSpotlight(target, title, sub, btnText) {
  if (!target || !target.getBoundingClientRect) return Promise.resolve();
  try { target.scrollIntoView({ block: 'center' }); } catch {}
  return new Promise(resolve => {
    const wrap = div('spot-wrap');
    const hole = div('spot-hole');
    const tip = div('spot-tip');
    const arrow = div('spot-arrow');
    tip.appendChild(txt('div', 'spot-tip-t', title));
    if (sub) tip.appendChild(txt('div', 'spot-tip-s', sub));
    const foot = div('spot-tip-foot');
    const b = el('button', 'spot-tip-b'); b.type = 'button'; b.textContent = btnText || 'OK';
    foot.appendChild(b); tip.appendChild(foot);
    wrap.append(hole, arrow, tip);
    document.body.appendChild(wrap);

    function place() {
      const r = target.getBoundingClientRect(), pad = 6;
      hole.style.left = (r.left - pad) + 'px';
      hole.style.top = (r.top - pad) + 'px';
      hole.style.width = (r.width + pad * 2) + 'px';
      hole.style.height = (r.height + pad * 2) + 'px';
      const cx = r.left + r.width / 2;
      const below = r.bottom + 16;
      const fits = below + tip.offsetHeight + 16 < window.innerHeight;
      tip.style.left = cx + 'px';
      tip.style.top = (fits ? below : Math.max(12, r.top - 16 - tip.offsetHeight)) + 'px';
      arrow.style.left = cx + 'px';
      arrow.style.top = (fits ? r.bottom + 10 : r.top - 22) + 'px';
    }
    place();
    requestAnimationFrame(() => { place(); wrap.classList.add('on'); });

    let done = false;
    const close = () => {
      if (done) return; done = true;
      wrap.classList.remove('on');
      window.removeEventListener('resize', place);
      setTimeout(() => { if (wrap.parentNode) wrap.parentNode.removeChild(wrap); resolve(); }, 320);
    };
    b.addEventListener('click', close);
    wrap.addEventListener('click', close);
    window.addEventListener('resize', place);
  });
}

// 셋업이 끝난 직후 — 등록된 프리셋을 어디서 보는지 한 번 짚어 준다
function spotlightPresets() {
  const card = document.querySelector('.panel.active .preset-card');
  if (!card) return Promise.resolve();
  return showSpotlight(card, '등록된 프리셋을 확인하세요',
                       '눌러서 바로 그 조건으로 조회할 수 있어요', 'OK');
}

// ── 첫 실행 셋업: 과거 3개월 이용내역 → 자주 타는 조건을 프리셋으로 ─────────
const _WD_KO = ['일','월','화','수','목','금','토'];
const _sleep = ms => new Promise(r => setTimeout(r, ms));

// 셋업 애니메이션 타이밍(ms). setup-demo.html 슬라이더로 맞춘 값을 여기에 반영한다.
// fade/rise 는 CSS 변수(--setup-fade / --setup-rise)와 짝이라 함께 바꿔야 한다.
const SETUP_T = {
  fadeIn: 990,       // 문구가 나타나는 시간
  fadeOut: 900,      // 문구가 사라지는 시간
  swap: 0,         // 다 사라진 뒤 다음 문구가 뜨기까지의 빈 시간
  itemFade: 340,     // 목록 한 항목이 나타나는 시간
  cardFade: 340,     // 클러스터 카드 한 장이 나타나는 시간
  shiftMs: 340,      // 아래에 내용이 붙어 위 문구가 밀려 올라갈 때의 이동 시간
  logoHold: 0,     // ① '기차놀이' 유지
  nameWait: 3200,    // 이름을 받으려고 조회 응답을 기다리는 상한
  greetHold: 500,   // ② 'OO님 환영합니다!' 유지
  subDelay: 350,    // ③ 1줄 → 2줄
  listDelay: 650,   // ③ 2줄 → 승차 기록 목록 시작
  rideStep: 340,     // ③ 승차 기록 한 줄씩 추가되는 간격
  ridesHold: 850,    // ③ 목록이 다 쌓인 뒤 유지
  clusterDelay: 0, // ④ 문구 → 첫 클러스터 카드
  clusterStep: 750,  // ④ 클러스터 카드 하나씩 추가되는 간격
  clusterHold: 1150, // ④ 다 뜬 뒤 유지
  savedHold: 1150,   // ⑤ '프리셋으로 등록했습니다' 유지
  byeHold: 900,     // ⑥ '즐거운 여행 되세요!' 유지
  tipDelay: 500,     // 화면이 뜬 뒤 프리셋 안내(스포트라이트)까지
};

function _histFetch() {
  const a = lsGetAccount('ktx');
  if (!a.memberNo || !a.password) return Promise.resolve({ ok: false, error: '계정 정보가 없습니다' });
  return api('POST', '/history/presets', { member_no: a.memberNo, password: a.password })
    .catch(() => ({ ok: false, error: '조회 실패' }));
}

// 클러스터 → 프리셋. 이미 같은 조건이 있으면 건너뛴다.
function _clustersToPresets(clusters) {
  const list = _presetLoad();
  const sigs = new Set(list.map(_histSig));
  const cur = list.length ? list[0] : {};
  const added = [];
  (clusters || []).forEach((c, i) => {
    const pz = {
      id: Date.now() + '' + i + Math.floor(Math.random() * 999),
      dep: c.dep, arr: c.arr, weekday: c.weekday,
      startTime: c.startTime, endTime: c.endTime,
      adults: cur.adults || 1, interval: cur.interval || 10,
      seatClass: cur.seatClass || 'general_first', allowWaiting: !!cur.allowWaiting,
    };
    if (sigs.has(_histSig(pz))) return;
    sigs.add(_histSig(pz)); list.push(pz); added.push(pz);
  });
  if (added.length) _presetSave(list);
  return added;
}

function _clusterCard(c) {
  const row = div('setup-cluster');
  const top = div('setup-cluster-top');
  top.append(txt('div', 'setup-cluster-route', `${c.dep} → ${c.arr}`),
             txt('span', 'setup-cluster-count', `${c.rides}회`));
  row.appendChild(top);
  row.appendChild(txt('div', 'setup-cluster-sub',
    (typeof c.weekday === 'number' ? `${_WD_KO[c.weekday]}요일 ` : '요일 무관 · ') + `${c.actual_start}~${c.actual_end}`));
  const kinds = [...new Set((c.trains || []).map(t => t.train_type).filter(Boolean))];
  if (kinds.length) row.appendChild(txt('div', 'setup-cluster-trains', kinds.join(' · ')));
  return row;
}

// DOM에 붙이자마자 클래스를 바꾸면 transition이 시작되지 않는 경우가 있어(2026-09-09 실측:
// .on 을 준 뒤 100ms에도 opacity가 0) 확정적으로 도는 Web Animations로 돌린다.
// 제자리 페이드만 — 위아래 이동 없음(2026-09-09 지시)
function _fadeIn(el, ms) {
  return el.animate([{ opacity: 0 }, { opacity: 1 }],
                    { duration: ms, easing: 'ease', fill: 'both' }).finished.catch(() => {});
}
function _fadeOut(el, ms) {
  return el.animate([{ opacity: 1 }, { opacity: 0 }],
                    { duration: ms, easing: 'ease', fill: 'both' }).finished.catch(() => {});
}

// 아래에 내용이 붙으면 가운데 정렬 때문에 위 문구가 위로 밀린다. 그 이동이 순간이동처럼
// 보여서 어색했다(2026-09-09 지적). 붙이기 전 위치를 재 두고, 붙인 뒤 그 차이만큼
// 되돌렸다가 0으로 애니메이션한다(FLIP).
function _flipStart(nodes) {
  // ⚠️ getBoundingClientRect()는 애니메이션이 적용된 '보이는' 위치를 돌려준다.
  // 이전 이동이 아직 돌고 있으면 그 오프셋까지 섞여 다음 계산이 틀어지고,
  // 결과적으로 올라갔다 내려갔다를 반복한다(2026-09-09 지적). 재기 전에 끝낸다.
  const list = [...nodes];
  list.forEach(e => { if (e._flipAnim) { try { e._flipAnim.finish(); } catch {} e._flipAnim = null; } });
  const rec = list.map(e => [e, e.getBoundingClientRect().top]);
  return (ms) => rec.forEach(([e, t0]) => {
    const dy = t0 - e.getBoundingClientRect().top;
    if (Math.abs(dy) > 0.5) {
      e._flipAnim = e.animate([{ transform: `translateY(${dy}px)` }, { transform: 'none' }],
                              { duration: ms, easing: 'ease' });
      e._flipAnim.addEventListener('finish', () => { e._flipAnim = null; });
    }
  });
}

// 다음 단계로 넘어갈 때 — 화면에 떠 있는 문구와 목록을 한꺼번에 지운다
async function _stageClear(textHost, bodyHost) {
  const nodes = [...textHost.children, ...bodyHost.children];
  if (!nodes.length) return;
  await Promise.all(nodes.map(e => _fadeOut(e, SETUP_T.fadeOut)));
  textHost.innerHTML = ''; bodyHost.innerHTML = '';
  await _sleep(SETUP_T.swap);
}

// 문구 등장. 페이드인이 끝난 뒤에 반환하므로, 호출부의 '유지' 시간은 곧 다 보인 채로 머무는 시간이다.
// sub 를 주면 1줄이 다 뜬 뒤 subDelay(ms) 지나 아랫줄이 따로 뜬다.
async function _stageText(host, main, sub, subDelay) {
  const wrap = div('setup-textwrap');
  const m = txt('div', 'setup-line', main); wrap.appendChild(m);
  const sEl = sub ? txt('div', 'setup-line sub', sub) : null;
  if (sEl) wrap.appendChild(sEl);
  host.appendChild(wrap);
  await _fadeIn(m, SETUP_T.fadeIn);
  if (sEl) {
    await _sleep(subDelay == null ? 0 : subDelay);
    await _fadeIn(sEl, SETUP_T.fadeIn);
  }
  return wrap;
}

// 아래 영역에 빈 그릇을 놓는다 — 내용은 _addItem 으로 한 항목씩 채운다
function _stageBody(host, shiftNodes) {
  const back = _flipStart(shiftNodes || []);
  const box = div('setup-body'); box.style.opacity = '1'; host.appendChild(box);
  back(SETUP_T.shiftMs);
  return box;
}

function _addItem(host, node, shiftNodes) {
  _shift(shiftNodes, () => host.appendChild(node));
  _fadeIn(node, SETUP_T.itemFade);
  return node;
}

// DOM을 바꾸기 전후를 한 번의 FLIP으로 묶는다. 여러 변경(스피너 제거 + 줄 추가)을
// 따로 하면 각각이 즉시 반영돼 위아래로 두 번 튄다(2026-09-09 지적).
function _shift(nodes, mutate) {
  const back = _flipStart(nodes || []);
  mutate();
  back(SETUP_T.shiftMs);
}

// 셋업 화면 — 로그인 직후(프리셋이 비어 있을 때) 한 번만
async function showSetup(onDone) {
  const host = document.getElementById('onboard');
  const app = document.getElementById('app');
  if (!host || !app) { onDone(); return; }
  // 화면이 갑자기 흰색으로 튀지 않게 배경째 페이드인한다(2026-09-09 지시)
  app.style.display = 'none';
  host.innerHTML = '';
  host.style.opacity = '0';
  host.style.display = '';
  _fadeIn(host, SETUP_T.fadeIn);

  const stage = div('setup-stage');
  const textHost = div(''); const bodyHost = div(''); bodyHost.style.width = '100%';
  stage.append(textHost, bodyHost); host.appendChild(stage);

  let showTip = false;
  const finish = async (last) => {
    if (last) { await _stageText(textHost, last); await _sleep(SETUP_T.byeHold); }
    await Promise.all([...textHost.children, ...bodyHost.children].map(e => _fadeOut(e, SETUP_T.fadeOut)));
    await _fadeOut(stage, SETUP_T.fadeOut);
    host.style.display = 'none'; host.innerHTML = ''; host.style.opacity = '';
    app.style.display = '';
    onDone();
    // 프리셋이 실제로 등록됐을 때만 짚어 준다
    if (showTip) setTimeout(() => spotlightPresets(), SETUP_T.tipDelay);
  };

  try { localStorage.setItem('kor_setup_done', '1'); } catch {}
  const pending = _histFetch();
  // 코레일 로그인이 틀렸는데 환영 인사부터 하면 안 된다 — 결과를 먼저 확인하고, 실패면 다시 입력받는다.
  // (2026-09-10: 회원번호 칸에 휴대폰번호를 넣어 실패했는데도 환영 화면이 그대로 진행됐다)
  const _authBad = r => !!(r && r.ok === false
    && /로그인|회원번호|비밀번호|계정 정보|휴대폰/.test(String(r.error || '')));
  const _bail = async (r) => {
    await Promise.all([...textHost.children, ...bodyHost.children].map(e => _fadeOut(e, SETUP_T.fadeOut)));
    await _fadeOut(stage, SETUP_T.fadeOut);
    host.style.display = 'none'; host.innerHTML = ''; host.style.opacity = '';
    app.style.display = '';
    notify(((r && r.error) || '코레일 로그인에 실패했습니다') + ' — 다시 입력해주세요');
    _accountSheet(() => showSetup(onDone));   // 고치고 나면 이어서 진행
  };
  // 이름도 이 응답에 실려 온다. 이름을 이미 알아도 로그인 성패는 확인해야 하므로 항상 기다린다.
  let name = (lsGetAccount('ktx').name || '').trim();
  const early = await Promise.race([pending.catch(() => null), _sleep(SETUP_T.nameWait).then(() => null)]);
  if (_authBad(early)) return _bail(early);
  if (!name) name = ((early && early.name) || '').trim();
  await _stageText(textHost, name ? `${name}님 환영합니다!` : '환영합니다!');
  await _sleep(SETUP_T.greetHold);

  await _stageClear(textHost, bodyHost);
  await _stageText(textHost, '과거 내역을 조회 중입니다', '최근 3개월 승차 기록을 살펴보고 있어요', SETUP_T.subDelay);
  await _sleep(SETUP_T.listDelay);
  const listBox = _stageBody(bodyHost, textHost.children);
  const ticker = div(''); const spin = div('setup-spin');
  listBox.append(ticker, spin);

  const res = await pending;
  if (_authBad(res)) return _bail(res);   // 늦게 온 실패도 여기서 잡는다
  const rides = (res && res.ok && res.rides) ? res.rides : [];
  const clusters = (res && res.ok && res.clusters) ? res.clusters : [];
  if (res && res.name && !name) lsSetAccount('ktx', { name: res.name });

  // 조회는 3개월 전체를 하지만 화면에는 최근 몇 건만 흘려 보여준다(사용자 지시).
  // 다만 몇 건을 살펴봤는지는 알려 준다 — 목록만 보면 그만큼만 조회한 줄 안다(2026-09-10).
  {
    const subs = textHost.querySelectorAll('.setup-line.sub');
    const sub = subs[subs.length - 1];
    if (sub && rides.length) {
      sub.textContent = `최근 3개월 승차 기록 ${rides.length}건을 찾았어요`
        + (rides.length > 7 ? ' · 최근 7건만 보여드릴게요' : '');
    } else if (sub) {
      sub.textContent = '최근 3개월 승차 기록이 없어요';
    }
  }
  const show = rides.slice(-7).reverse();
  for (let i = 0; i < show.length; i++) {
    const r = show[i], last = i === show.length - 1;
    const row = div('setup-ride');
    row.append(txt('span', 'd', `${r.date.slice(4, 6)}/${r.date.slice(6)}`),
               txt('span', 'r', `${r.dep}→${r.arr}`),
               txt('span', 't', `${r.time} ${r.train_type} ${r.train_no}`));
    // 마지막 줄은 스피너를 치우는 것과 같은 창에서 처리한다 — 따로 하면 위아래로 두 번 튄다
    _shift([...textHost.children, ...ticker.children], () => {
      if (last && spin.parentNode) spin.remove();
      ticker.appendChild(row);
    });
    _fadeIn(row, SETUP_T.itemFade);
    await _sleep(SETUP_T.rideStep);
  }
  if (spin.parentNode) _shift([...textHost.children, ...ticker.children], () => spin.remove());
  await _sleep(show.length ? SETUP_T.ridesHold : SETUP_T.ridesHold * 1.8);

  if (!res || !res.ok) {
    await _stageClear(textHost, bodyHost);
    await finish('이용 내역을 불러오지 못했어요\n프리셋은 직접 추가할 수 있어요');
    return;
  }
  if (!clusters.length) {
    await _stageClear(textHost, bodyHost);
    await _stageText(textHost, '아직 반복해서 탄 열차가 없네요', '3번 이상 같은 시간대를 타면 자동으로 등록해 드릴게요', SETUP_T.subDelay);
    await _sleep(SETUP_T.clusterHold);
    await finish('그럼 즐거운 여행 되세요!');
    return;
  }

  const added = _clustersToPresets(clusters);
  showTip = added.length > 0;
  await _stageClear(textHost, bodyHost);
  await _stageText(textHost, '이 열차를 자주 이용하셨네요!');
  await _sleep(SETUP_T.clusterDelay);
  const clBox = _stageBody(bodyHost, textHost.children);
  // 카드를 하나씩 따로 띄운다 — 한 박스에 줄을 쌓으면 배경이 안 나뉘고 두 번째부터 페이드가 묻힌다
  for (let i = 0; i < clusters.length; i++) {
    const box = div('card setup-clcard');
    const row = _clusterCard(clusters[i]); row.style.opacity = '1';
    box.appendChild(row); box.style.opacity = '0';
    const back = _flipStart([...textHost.children, ...clBox.children]);
    clBox.appendChild(box);
    back(SETUP_T.shiftMs);
    _fadeIn(box, SETUP_T.cardFade);
    await _sleep(SETUP_T.clusterStep);
  }
  await _sleep(SETUP_T.clusterHold);

  await _stageClear(textHost, bodyHost);
  await _stageText(textHost, added.length
    ? '해당 조건을 프리셋으로 등록할게요'
    : '이미 같은 조건의 프리셋이 등록되어 있어요');
  await _sleep(SETUP_T.savedHold);
  await _stageClear(textHost, bodyHost);
  await finish('그럼 즐거운 여행 되세요!');
}

_syncPull().finally(() => {
  _migrateAccountModel();
  if (!ktxAccountReady() && !window.__DEMO__) {
    showOnboarding(() => { showSetup(() => _boot()); });
    return;
  }
  // 계정은 있는데 프리셋이 하나도 없으면(첫 로그인) 셋업 화면을 한 번 보여준다
  if (!window.__DEMO__ && !_presetLoad().length && !localStorage.getItem('kor_setup_done')) {
    showSetup(() => _boot());
    return;
  }
  _boot();
});

// ── 관리자 전용: 코레일 API 호출 실시간 보기(상단 중앙 토글 + 우측 패널) ──
function installCallLog() {
  fetch('/api/me').then(r => r.json()).then(d => { if (d && d.admin) _mountCallLog(); }).catch(() => {});
}
function _mountCallLog() {
  const btn = el('button', 'calllog-toggle'); btn.type = 'button'; btn.textContent = '실시간 호출 보기';
  document.body.appendChild(btn);
  const panel = div('calllog-panel'); const head = div('calllog-head');
  const openBtn = el('a', 'calllog-open'); openBtn.textContent = '새 탭'; openBtn.href = '/calllog';
  openBtn.target = '_blank'; openBtn.rel = 'noopener'; openBtn.title = '새 탭에서 크게 보기';
  head.append(txt('span', 'calllog-title', '코레일 API 호출'), openBtn, (() => {
    const x = el('button', 'calllog-x'); x.type = 'button'; x.textContent = '×';
    x.addEventListener('click', () => setOn(false)); return x; })());
  const chips = div('calllog-chips');            // 계정별로 걸러 보기
  const body = div('calllog-body');
  const grip = div('calllog-grip');              // 왼쪽 모서리를 끌어 가로폭 조절
  panel.append(grip, head, chips, body); document.body.appendChild(panel);
  let on = false, timer = null, after = 0, sel = '', all = [];
  const seen = {};
  function renderChips() {
    chips.innerHTML = '';
    const names = Object.keys(seen).sort();
    [['', '전체']].concat(names.map(n => [n, `${n} (${seen[n]})`])).forEach(([v, label]) => {
      const b = el('button', 'calllog-chip' + (sel === v ? ' on' : '')); b.type = 'button'; b.textContent = label;
      b.addEventListener('click', () => { sel = v; renderChips(); redraw(); });
      chips.appendChild(b);
    });
  }
  function row(c) {
    const r = div('calllog-row' + (c.cached ? ' cached' : ''));
    r.append(txt('span', 'cl-t', c.t),
             txt('span', 'cl-who', c.who || ''),
             txt('span', 'cl-purpose ' + (c.cached ? 'cache' : (c.host === 'web' ? 'web' : 'mob')), c.purpose),
             txt('span', 'cl-ep', c.ep),
             txt('span', 'cl-params', c.params || ''));
    r.title = (c.who ? c.who + ' · ' : '') + `${c.method} ${c.url}` + (c.params ? '\n' + c.params : '');
    return r;
  }
  function redraw() {
    body.innerHTML = '';
    all.filter(c => !sel || c.who === sel).slice(-300).forEach(c => body.appendChild(row(c)));
    body.scrollTop = body.scrollHeight;
  }
  function poll() {
    api('GET', '/calllog?after=' + after).then(d => {
      if (!d || !d.ok) return;
      let added = 0;
      (d.calls || []).forEach(c => {
        after = Math.max(after, c.n); all.push(c); added++;
        const w = c.who || '?'; seen[w] = (seen[w] || 0) + 1;
      });
      if (all.length > 1200) all = all.slice(-800);
      if (added) { renderChips(); redraw(); }
    }).catch(() => {});
  }
  { // 가로폭 조절 — 끌어서 넓히고, 마지막 폭을 기억한다
    let sx = 0, sw = 0;
    const move = e => { const w = Math.min(Math.max(sw + (sx - e.clientX), 280), window.innerWidth - 80);
      panel.style.width = w + 'px'; document.documentElement.style.setProperty('--calllog-w', w + 'px'); };
    const up = () => { document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up);
      try { localStorage.setItem('kor_calllog_w', panel.style.width); } catch {} };
    grip.addEventListener('mousedown', e => { e.preventDefault(); sx = e.clientX; sw = panel.offsetWidth;
      document.addEventListener('mousemove', move); document.addEventListener('mouseup', up); });
    try { const w = localStorage.getItem('kor_calllog_w');
      if (w) { panel.style.width = w; document.documentElement.style.setProperty('--calllog-w', w); } } catch {}
  }
  function setOn(v) {
    on = v; btn.classList.toggle('on', on); panel.classList.toggle('on', on);
    document.body.classList.toggle('calllog-open', on);
    if (on) { poll(); timer = setInterval(poll, 1500); }
    else { clearTimeout(timer); clearInterval(timer); }
  }
  btn.addEventListener('click', () => setOn(!on));
}

function _boot() {
  buildPage();
  buildSettingsPage();
  buildTicketsPage();
  buildResultsPage();
  _refreshAlertBadge();
  _loadDayStatus();
  openSharedLogStream();
  installPullToRefresh();
  _seatGroups();   // 좌석 배치 그룹·열차 매핑 미리 로드
  installVersionWatch();
  installCallLog();
  // 5분 주기로만 보면 배경 확인이 끝나도 배너가 최대 5분 늦게 뜬다 — 부팅 직후엔 촘촘히 본다
  _pollUpdate(); setInterval(_pollUpdate, 300000);
  [4000, 10000, 25000, 60000].forEach(ms => setTimeout(_pollUpdate, ms));
  refreshHeaderPill();
  setInterval(refreshHeaderPill, 5000);   // 탭과 무관하게 같은 값을 유지
  // 새로고침해도 보던 탭 그대로
  let last = (location.hash || '').replace('#', '');
  if (!_TABS.includes(last)) { try { last = localStorage.getItem('kor_ktx_tab') || ''; } catch { last = ''; } }
  if (_TABS.includes(last) && last !== _currentTab) switchTab(last);
}
