/* 市場熱度排行 (/market)
   拉 /api/market/top-traded、渲染表格、支援排序與跳轉到 /screener。
 */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const marketSel      = $('market-select');
  const limitInput     = $('limit-input');
  const minAmountInput = $('min-amount-input');
  const loadBtn        = $('load-btn');
  const emptyEl        = $('empty');
  const errorEl        = $('error');
  const errorMsgEl     = $('error-message');
  const resultsBlock   = $('results-block');
  const resultsTitle   = $('results-title');
  const resultsMeta    = $('results-meta');
  const resultsBody    = $('results-body');
  const proxyNoteEl    = $('proxy-note');

  const state = {
    rows: [],
    sortKey: 'total_amount',
    sortAsc: false,
  };

  function show(el) { el.hidden = false; }
  function hide(el) { el.hidden = true; }

  function escapeHtml(s) {
    if (s == null) return '';
    return String(s).replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function fmtInt(x) {
    if (x == null) return '—';
    return Math.round(x).toLocaleString('zh-TW');
  }
  function fmtPct(x) {
    if (x == null) return '—';
    return (x * 100).toFixed(1) + '%';
  }

  async function loadList() {
    hide(emptyEl); hide(errorEl); hide(resultsBlock);
    const params = new URLSearchParams({
      limit: limitInput.value || '20',
      market: marketSel.value || 'all',
      min_amount: minAmountInput.value || '0',
    });

    try {
      const r = await fetch('/api/market/top-traded?' + params.toString());
      const body = await r.json();
      if (!r.ok) {
        errorMsgEl.textContent = body.error || `HTTP ${r.status}`;
        show(errorEl);
        return;
      }
      state.rows = body.results || [];
      proxyNoteEl.textContent = body.proxy_note || '';
      if (state.rows.length === 0) {
        show(emptyEl);
        return;
      }
      show(resultsBlock);
      render();
    } catch (e) {
      errorMsgEl.textContent = '網路錯誤：' + e.message;
      show(errorEl);
    }
  }

  function render() {
    const n = state.rows.length;
    const grandTotal = state.rows.reduce((s, r) => s + r.total_amount, 0);
    resultsTitle.textContent = `市場熱度 Top ${n}`;
    resultsMeta.textContent = `合計 ${fmtInt(grandTotal)} 元 · 占該 N 檔 ${fmtInt(grandTotal)} 元（100%）`;
    resultsBody.innerHTML = '';
    const sorted = [...state.rows].sort(compareBy(state.sortKey, state.sortAsc));
    sorted.forEach((r, i) => {
      const tr = document.createElement('tr');
      tr.dataset.code = r.stock_code || '';
      resultsBody.appendChild(tr);
      updateRow(tr, r, i + 1);
    });
  }

  function compareBy(key, asc) {
    return (a, b) => {
      let ka, kb;
      switch (key) {
        case 'rank':          ka = a._rank ?? 0; kb = b._rank ?? 0; break;
        case 'stock_name':    ka = a.stock_name || ''; kb = b.stock_name || ''; break;
        case 'stock_code':    ka = a.stock_code || '9999'; kb = b.stock_code || '9999'; break;
        case 'market':        ka = a.market || ''; kb = b.market || ''; break;
        case 'total_amount':  ka = a.total_amount || 0; kb = b.total_amount || 0; break;
        case 'share':         ka = a.share || 0; kb = b.share || 0; break;
        case 'total_volume':  ka = a.total_volume || 0; kb = b.total_volume || 0; break;
        case 'warrant_count': ka = a.warrant_count || 0; kb = b.warrant_count || 0; break;
        default: return 0;
      }
      if (typeof ka === 'number') return asc ? ka - kb : kb - ka;
      return asc ? String(ka).localeCompare(String(kb)) : String(kb).localeCompare(String(ka));
    };
  }

  function updateRow(tr, r, defaultRank) {
    r._rank = defaultRank;
    const screened = r.stock_code
      ? `<a href="/screener?q=${encodeURIComponent(r.stock_code)}&market=${encodeURIComponent(r.market)}&type=%E8%AA%8D%E8%B3%BC">→ IV 篩選</a>`
      : `<span class="iv-cell iv-pending">缺代號</span>`;
    tr.innerHTML = `
      <td class="num iv-cell" style="font-weight: 600; color: var(--text-muted);">${defaultRank}</td>
      <td>${escapeHtml(r.stock_name)}</td>
      <td class="code-cell">${escapeHtml(r.stock_code || '?')}</td>
      <td>${escapeHtml((r.market || '').toUpperCase())}</td>
      <td class="num iv-cell">${fmtInt(r.total_amount)}</td>
      <td class="num">${fmtPct(r.share)}</td>
      <td class="num">${fmtInt(r.total_volume)}</td>
      <td class="num">${fmtInt(r.warrant_count)}</td>
      <td>${screened}</td>
    `;
  }

  document.getElementById('results-head').addEventListener('click', (e) => {
    const th = e.target.closest('th[data-sort]');
    if (!th) return;
    const key = th.dataset.sort;
    if (key === 'rank') return; // rank 是預設序，不切
    if (state.sortKey === key) {
      state.sortAsc = !state.sortAsc;
    } else {
      state.sortKey = key;
      state.sortAsc = (key !== 'total_amount' && key !== 'total_volume' && key !== 'warrant_count' && key !== 'share');
    }
    render();
  });

  loadBtn.addEventListener('click', loadList);

  // 自動載入一次
  document.addEventListener('DOMContentLoaded', loadList);
})();
