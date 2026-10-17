/* IV Screener page (/screener)
 * 顯示特定標的（如 6187 萬潤）所有權證，並支援批次算 IV。
 *   GET /api/warrants/screen  → metadata + days_to_expiry
 *   GET /api/warrant/<code>/iv?underlying=... → IV + Greeks
 */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const inputEl     = $('stock-input');
  const marketSel   = $('market-select');
  const typeSel     = $('type-select');
  const minDaysEl   = $('min-days');
  const loadBtn     = $('load-btn');
  const computeAll  = $('compute-all-btn');
  const emptyEl     = $('empty');
  const noResultsEl = $('no-results');
  const errorEl     = $('error');
  const errorMsgEl  = $('error-message');
  const resultsBlock = $('results-block');
  const resultsTitle = $('results-title');
  const resultsMeta  = $('results-meta');
  const resultsBody  = $('results-body');
  const progressEl   = $('progress');
  const progressText = $('progress-text');
  const progressFill = $('progress-fill');

  // === 狀態 ===
  const state = {
    rows: [],          // 從 /api/warrants/screen 拿到的原始資料
    ivCache: new Map(),// warrant_code → IV payload
    matched: null,     // {code, name, market}
    abortControllers: [],
    sortKey: 'iv',
    sortAsc: true,
  };

  function show(el) { el.hidden = false; }
  function hide(el) { el.hidden = true; }

  function clearMsg() {
    hide(errorEl);
    hide(progressEl);
    hide(noResultsEl);
    hide(resultsBlock);
    show(emptyEl);
  }

  function escapeHtml(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function formatPct(x) {
    if (x == null) return '—';
    return (x * 100).toFixed(2) + '%';
  }

  function fmt(x, n) {
    if (x == null || Number.isNaN(x)) return '—';
    return Number(x).toFixed(n);
  }

  function colorClass(iv) {
    if (iv == null) return 'iv-pending';
    if (iv < 0.5) return 'iv-low';
    if (iv <= 1.0) return 'iv-mid';
    return 'iv-high';
  }

  function badge(iv) {
    if (iv == null) return '';
    let cls = 'iv-badge ';
    if (iv < 0.5)      cls += 'green';
    else if (iv <= 1.0) cls += 'orange';
    else                cls += 'red';
    return `<span class="${cls}">${formatPct(iv)}</span>`;
  }

  // === API calls ===
  async function loadList() {
    hide(emptyEl);
    hide(noResultsEl);
    hide(resultsBlock);
    hide(errorEl);

    const q = inputEl.value.trim();
    if (!q) {
      errorMsgEl.textContent = '請輸入股票代號或名稱';
      show(errorEl);
      return;
    }
    const params = new URLSearchParams({
      q: q,
      market: marketSel.value,
      min_days: minDaysEl.value || '0',
      limit: '500',
    });
    if (typeSel.value) params.set('type', typeSel.value);

    state.rows = [];
    state.ivCache.clear();
    state.matched = null;

    try {
      const r = await fetch('/api/warrants/screen?' + params.toString());
      const body = await r.json();
      if (!r.ok) {
        errorMsgEl.textContent = body.error || `HTTP ${r.status}`;
        show(errorEl);
        return;
      }
      state.rows = body.results || [];
      state.matched = body.matched_stock || null;
      renderResults(body);
      computeAll.disabled = state.rows.length === 0;
    } catch (e) {
      errorMsgEl.textContent = '網路錯誤：' + e.message;
      show(errorEl);
    }
  }

  async function fetchOneIv(code, underlying) {
    const r = await fetch(`/api/warrant/${encodeURIComponent(code)}/iv?underlying=${encodeURIComponent(underlying)}`);
    return r.json();
  }

  function abortAll() {
    state.abortControllers.forEach((c) => { try { c.abort(); } catch (_) {} });
    state.abortControllers = [];
  }

  // 批次算 IV（sequential，1s 等候：對齊後端禮貌性 sleep）
  async function batchCompute() {
    if (state.rows.length === 0) return;
    computeAll.disabled = true;
    loadBtn.disabled = true;
    abortAll();

    show(progressEl);
    const total = state.rows.length;
    let okCount = 0;
    let errCount = 0;

    for (let i = 0; i < state.rows.length; i++) {
      const w = state.rows[i];
      const code = w.code;
      const underlying = (state.matched && state.matched.code) || '';

      if (!underlying) {
        errCount++;
        w._ivError = 'no underlying';
        updateProgressUI(i + 1, total, code, 'no underlying code in matched_stock');
        continue;
      }

      const ac = new AbortController();
      state.abortControllers.push(ac);
      try {
        const body = await fetchOneIv(code, underlying);
        if (body.implied_volatility != null) {
          state.ivCache.set(code, body);
          w._iv = body.implied_volatility;
          w._ivPayload = body;
          okCount++;
        } else if (body.error) {
          w._ivError = body.error;
          errCount++;
        }
      } catch (e) {
        w._ivError = e.message || 'fetch fail';
        errCount++;
      }

      // Throttle update：每完成 1 筆 → re-render row + progress
      const wEl = resultsBody.querySelector(`tr[data-code="${CSS.escape(code)}"]`);
      if (wEl) updateRow(wEl, w);
      updateProgressUI(i + 1, total, code, errCount ? `(失敗 ${errCount})` : '');

      // 禮貌性 sleep 500ms（後端還有 1s），避免連發太快
      if (i < state.rows.length - 1) await sleep(500);
    }

    hide(progressEl);
    computeAll.disabled = false;
    loadBtn.disabled = false;
    computeAll.textContent = `▶ 重算 IV (上次 ${okCount}/${total} 成功`;
    if (errCount) computeAll.textContent += `,${errCount} 錯)`;
    else computeAll.textContent += '）';
  }

  function updateProgressUI(done, total, lastCode, extra) {
    const pct = (done / total) * 100;
    progressFill.style.width = pct.toFixed(1) + '%';
    progressText.textContent =
      `計算 IV：${done}/${total} ${extra || ''}（最新：${lastCode}）`;
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  // === Render ===
  function renderResults(body) {
    if (!state.rows.length) {
      hide(emptyEl);
      show(noResultsEl);
      hide(resultsBlock);
      return;
    }
    hide(emptyEl);
    hide(noResultsEl);
    show(resultsBlock);

    const matched = state.matched;
    resultsTitle.textContent = matched
      ? `${matched.name}（${matched.code}）— ${state.rows.length} 檔權證`
      : `查詢結果 — ${state.rows.length} 檔`;

    const marketCounts = state.rows.reduce((acc, w) => {
      acc[w.market || '?'] = (acc[w.market || '?'] || 0) + 1;
      return acc;
    }, {});
    const marketTxt = Object.entries(marketCounts).map(([m, n]) => `${m}=${n}`).join(' / ');
    resultsMeta.textContent = `${state.rows.length} 筆 · ${marketTxt}`;

    resultsBody.innerHTML = '';
    state.rows.forEach((w) => {
      const tr = document.createElement('tr');
      tr.dataset.code = w.code;
      resultsBody.appendChild(tr);
      updateRow(tr, w);
    });
  }

  function updateRow(tr, w) {
    const iv = w._iv != null ? w._iv : (state.ivCache.get(w.code) || {}).implied_volatility;
    const payload = w._ivPayload || state.ivCache.get(w.code);
    const delta = payload && payload.greeks ? payload.greeks.delta : null;
    const underlying = payload && payload.underlying ? payload.underlying : null;
    const S = underlying && underlying.price != null ? underlying.price : null;
    const K = w.strike_price;
    let moneyness = null;
    if (S != null && K != null && K > 0) {
      moneyness = S / K;
    }

    const erRaw = w.exercise_ratio_per_mille != null
      ? w.exercise_ratio_per_mille
      : 1.0;
    const erStr = erRaw === 1.0 ? '1.0' : (erRaw / 1000).toFixed(4);

    const days = w.days_to_expiry != null ? w.days_to_expiry : '—';
    const moneynessClass = moneyness == null ? '' : (moneyness >= 1 ? 'moneyness-itm' : 'moneyness-otm');
    const ivClass = colorClass(iv);
    const status = w._ivError
      ? `<span class="iv-cell iv-pending" title="${escapeHtml(w._ivError)}">err</span>`
      : `<span class="iv-cell ${ivClass}">${iv == null ? '— 點按計算 —' : formatPct(iv)}</span>`;

    tr.innerHTML = `
      <td class="code-cell">${escapeHtml(w.code)}</td>
      <td class="name-cell">${escapeHtml(w.name || '')}</td>
      <td>${escapeHtml(w.type || '')}</td>
      <td>${escapeHtml((w.market || '').toUpperCase())}</td>
      <td class="num">${fmt(K, 2)}</td>
      <td class="num">${days}</td>
      <td class="num greek-cell">${erStr}</td>
      <td class="num">${status}</td>
      <td class="num greek-cell">${fmt(delta, 3)}</td>
      <td class="num ${moneynessClass}">${fmt(moneyness, 3)}</td>
      <td>
        <button data-action="iv" data-code="${escapeHtml(w.code)}"
                class="compute-all-btn" style="padding: 0.2rem 0.5rem; font-size: 0.85rem;">
          ${iv == null ? '算 IV' : '重算'}
        </button>
      </td>
    `;
  }

  // === Sort ===
  document.getElementById('results-head').addEventListener('click', (e) => {
    const th = e.target.closest('th[data-sort]');
    if (!th) return;
    const key = th.dataset.sort;
    if (state.sortKey === key) {
      state.sortAsc = !state.sortAsc;
    } else {
      state.sortKey = key;
      state.sortAsc = (key === 'iv' || key === 'days_to_expiry');
    }
    sortRows();
  });

  function sortRows() {
    const key = state.sortKey;
    const dir = state.sortAsc ? 1 : -1;
    const getSortKey = (w) => {
      switch (key) {
        case 'code':               return w.code;
        case 'name':               return w.name || '';
        case 'type':               return w.type || '';
        case 'market':             return w.market || '';
        case 'strike_price':       return w.strike_price ?? Infinity;
        case 'days_to_expiry':     return w.days_to_expiry ?? Infinity;
        case 'exercise_ratio':     return w.exercise_ratio_per_mille ?? 1000;
        case 'iv':                 return w._iv ?? Infinity;
        case 'moneyness': {
          const payload = state.ivCache.get(w.code);
          const K = w.strike_price;
          const S = payload && payload.underlying && payload.underlying.price;
          return (S != null && K > 0) ? (S / K) : Infinity;
        }
        case 'delta': {
          const p = state.ivCache.get(w.code);
          return p && p.greeks ? p.greeks.delta : -Infinity;
        }
        default: return '';
      }
    };
    state.rows.sort((a, b) => {
      const ka = getSortKey(a);
      const kb = getSortKey(b);
      if (typeof ka === 'number' && typeof kb === 'number') return (ka - kb) * dir;
      return String(ka).localeCompare(String(kb)) * dir;
    });
    resultsBody.innerHTML = '';
    state.rows.forEach((w) => {
      const tr = document.createElement('tr');
      tr.dataset.code = w.code;
      resultsBody.appendChild(tr);
      updateRow(tr, w);
    });
  }

  // === Per-row 算 IV ===
  resultsBody.addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-action="iv"]');
    if (!btn) return;
    const code = btn.dataset.code;
    const underlying = state.matched && state.matched.code;
    if (!underlying) {
      errorMsgEl.textContent = '無法判斷 underlying code，請先用「載入權證清單」';
      show(errorEl);
      return;
    }
    btn.disabled = true;
    btn.textContent = '算中…';
    try {
      const body = await fetchOneIv(code, underlying);
      const w = state.rows.find((x) => x.code === code);
      if (w) {
        if (body.implied_volatility != null) {
          w._iv = body.implied_volatility;
          w._ivPayload = body;
          state.ivCache.set(code, body);
        } else if (body.error) {
          w._ivError = body.error;
        }
      }
      const tr = resultsBody.querySelector(`tr[data-code="${CSS.escape(code)}"]`);
      if (tr) updateRow(tr, w);
    } catch (err) {
      errorMsgEl.textContent = err.message;
      show(errorEl);
    } finally {
      btn.disabled = false;
    }
  });

  // === Wire up ===
  loadBtn.addEventListener('click', loadList);
  computeAll.addEventListener('click', batchCompute);
  inputEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      loadList();
    }
  });
})();
