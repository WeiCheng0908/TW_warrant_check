/* TWSE 權證查詢系統 — 前端邏輯
 * 純 vanilla JS，無相依。
 * 對齊 design.md 的 API contract：
 *   GET /api/stocks?q=...      → { results: [{code, name}, ...] }
 *   GET /api/warrants?q=...&new_only=...&type=...
 *                             → { query, matched_stock, count, results: [...] }
 *   GET /api/health            → { status, warrants, stocks, last_updated }
 */
(function () {
  'use strict';

  // === DOM 參考 ===
  const $ = (id) => document.getElementById(id);
  const input          = $('search-input');
  const suggestionsEl  = $('suggestions');
  const filterNewOnly  = $('filter-new-only');
  const filterCall     = $('filter-call');
  const filterPut      = $('filter-put');
  const resetBtn       = $('reset-filters');
  const loadingEl      = $('loading');
  const emptyEl        = $('empty');
  const noResultsEl    = $('no-results');
  const errorEl        = $('error');
  const errorMsgEl     = $('error-message');
  const statusBarEl    = $('status-bar');
  const resultsBlock   = $('results-block');
  const resultsTitle   = $('results-title');
  const resultsMeta    = $('results-meta');
  const resultsBody    = $('results-body');
  const lastUpdatedEl  = $('last-updated');

  // === 狀態 ===
  const state = {
    selectedStock: null,    // {code, name} | null
    suggestions: [],
    suggestionIndex: -1,
    lastQuery: '',
    stockAbort: null,
    warrantAbort: null,
  };

  // === 工具 ===
  function debounce(fn, ms) {
    let timer = null;
    return function (...args) {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), ms);
    };
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

  function formatDate(iso) {
    if (!iso) return '—';
    return iso; // API 已回 ISO 格式
  }

  function formatNumber(n, digits) {
    if (n == null || n === '') return '—';
    const num = Number(n);
    if (Number.isNaN(num)) return '—';
    return num.toLocaleString('zh-TW', {
      minimumFractionDigits: digits || 0,
      maximumFractionDigits: digits || 0,
    });
  }

  function summarizeNotes(notes) {
    if (!notes) return '';
    const cleaned = String(notes)
      .replace(/\r/g, '')
      .replace(/\s+/g, ' ')
      .trim();
    if (cleaned.length <= 80) return cleaned;
    return cleaned.slice(0, 80) + '…';
  }

  // === 隱藏全部狀態區塊 ===
  function hideAllStates() {
    loadingEl.hidden = true;
    emptyEl.hidden = true;
    noResultsEl.hidden = true;
    errorEl.hidden = true;
    resultsBlock.hidden = true;
    statusBarEl.hidden = true;
  }

  function showLoading() { hideAllStates(); loadingEl.hidden = false; }
  function showEmpty()   { hideAllStates(); emptyEl.hidden = false; }

  function showError(message) {
    hideAllStates();
    errorMsgEl.textContent = message || '發生未知錯誤';
    errorEl.hidden = false;
  }

  function showNoResults(query) {
    hideAllStates();
    noResultsEl.hidden = false;
    const p = noResultsEl.querySelector('p');
    if (p && query) {
      p.textContent = `😕 找不到「${query}」相關的權證。`;
    } else if (p) {
      p.textContent = '😕 找不到符合條件的權證。';
    }
  }

  function showStatus(text) {
    statusBarEl.textContent = text;
    statusBarEl.hidden = false;
  }

  // === Autocomplete ===
  function closeSuggestions() {
    suggestionsEl.hidden = true;
    suggestionsEl.innerHTML = '';
    state.suggestions = [];
    state.suggestionIndex = -1;
    input.setAttribute('aria-expanded', 'false');
  }

  function renderSuggestions(items) {
    if (!items || items.length === 0) {
      suggestionsEl.innerHTML =
        '<li class="suggestion-empty" role="presentation">沒有符合的標的</li>';
    } else {
      suggestionsEl.innerHTML = items
        .map(
          (s, i) =>
            `<li class="suggestion-item" role="option" data-index="${i}" ` +
            `data-code="${escapeHtml(s.code)}" data-name="${escapeHtml(s.name)}">` +
            `<span class="code">${escapeHtml(s.code)}</span>` +
            `<span class="name">${escapeHtml(s.name)}</span></li>`
        )
        .join('');
    }
    suggestionsEl.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  }

  async function fetchStocks(q) {
    if (state.stockAbort) state.stockAbort.abort();
    const ctrl = new AbortController();
    state.stockAbort = ctrl;
    try {
      const url = `/api/stocks?q=${encodeURIComponent(q)}`;
      const res = await fetch(url, { signal: ctrl.signal });
      if (!res.ok) { closeSuggestions(); return; }
      const data = await res.json();
      state.suggestions = (data && data.results) || [];
      renderSuggestions(state.suggestions);
    } catch (err) {
      if (err.name === 'AbortError') return;
      closeSuggestions();
    }
  }

  const onInputDebounced = debounce(function (val) {
    const q = val.trim();
    if (!q) { closeSuggestions(); return; }
    fetchStocks(q);
  }, 200);

  input.addEventListener('input', (e) => {
    state.selectedStock = null;
    onInputDebounced(e.target.value);
  });

  // 用 mousedown 避免 input blur 先關閉
  suggestionsEl.addEventListener('mousedown', (e) => {
    const li = e.target.closest('.suggestion-item');
    if (!li) return;
    e.preventDefault();
    selectStock({ code: li.dataset.code, name: li.dataset.name });
  });

  function highlightSuggestion(idx) {
    const items = suggestionsEl.querySelectorAll('.suggestion-item');
    items.forEach((el, i) => {
      if (i === idx) el.setAttribute('aria-selected', 'true');
      else el.removeAttribute('aria-selected');
    });
    const sel = suggestionsEl.querySelector('.suggestion-item[aria-selected="true"]');
    if (sel) sel.scrollIntoView({ block: 'nearest' });
  }

  function selectStock(stock) {
    state.selectedStock = stock;
    input.value = `${stock.code} ${stock.name || ''}`.trim();
    closeSuggestions();
    runWarrantQuery();
  }

  input.addEventListener('keydown', (e) => {
    const items = state.suggestions;
    if (e.key === 'ArrowDown') {
      if (items.length === 0) return;
      e.preventDefault();
      state.suggestionIndex = (state.suggestionIndex + 1) % items.length;
      highlightSuggestion(state.suggestionIndex);
    } else if (e.key === 'ArrowUp') {
      if (items.length === 0) return;
      e.preventDefault();
      state.suggestionIndex = (state.suggestionIndex - 1 + items.length) % items.length;
      highlightSuggestion(state.suggestionIndex);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (state.suggestionIndex >= 0 && items[state.suggestionIndex]) {
        selectStock(items[state.suggestionIndex]);
      } else {
        closeSuggestions();
        runWarrantQuery();
      }
    } else if (e.key === 'Escape') {
      closeSuggestions();
    }
  });

  input.addEventListener('blur', () => {
    setTimeout(closeSuggestions, 120);
  });

  // === 篩選參數 ===
  function currentFilters() {
    const params = {};
    if (filterNewOnly.checked) params.new_only = 'true';
    // 認購 / 認售 互斥：兩個都勾時依使用者最後動作取消另一個
    if (filterCall.checked && !filterPut.checked) params.type = '認購';
    else if (filterPut.checked && !filterCall.checked) params.type = '認售';
    return params;
  }

  // === 主查詢：權證清單 ===
  async function runWarrantQuery() {
    const rawQuery =
      (state.selectedStock && state.selectedStock.code) || input.value.trim();
    state.lastQuery = rawQuery;

    if (!rawQuery) { showEmpty(); return; }

    if (state.warrantAbort) state.warrantAbort.abort();
    const ctrl = new AbortController();
    state.warrantAbort = ctrl;

    showLoading();

    const filters = currentFilters();
    const params = new URLSearchParams({ q: rawQuery });
    Object.entries(filters).forEach(([k, v]) => params.set(k, v));
    const url = `/api/warrants?${params.toString()}`;

    try {
      const res = await fetch(url, { signal: ctrl.signal });
      if (res.status === 404) {
        const data = await res.json().catch(() => ({}));
        showError(data.error || `找不到標的：${rawQuery}`);
        return;
      }
      if (!res.ok) {
        showError(`伺服器錯誤（HTTP ${res.status}）`);
        return;
      }
      const data = await res.json();
      renderWarrantResults(data, filters);
    } catch (err) {
      if (err.name === 'AbortError') return;
      showError('網路錯誤，請稍後再試。');
    }
  }

  function renderWarrantResults(data, filters) {
    hideAllStates();
    const results = (data && data.results) || [];
    const matched = data && data.matched_stock;
    const count =
      data && typeof data.count === 'number' ? data.count : results.length;

    if (matched && matched.code) {
      resultsTitle.textContent =
        `${matched.code} ${matched.name || ''}`.trim();
    } else {
      resultsTitle.textContent = (data && data.query) || '';
    }
    const desc = [];
    if (filters.new_only) desc.push('僅今天新發行');
    if (filters.type) desc.push(filters.type);
    const filterText = desc.length ? `（篩選：${desc.join('、')}）` : '';
    resultsMeta.textContent = `共 ${count} 筆權證${filterText}`;

    if (results.length === 0) {
      showNoResults(data && data.query);
      return;
    }

    resultsBody.innerHTML = results.map(rowHtml).join('');
    resultsBlock.hidden = false;
  }

  function rowHtml(r) {
    const type = r.type || '';
    const badgeClass =
      type === '認購' ? 'badge-call' : type === '認售' ? 'badge-put' : '';
    const typeBadge = type
      ? `<span class="badge ${badgeClass}">${escapeHtml(type)}</span>`
      : '<span class="muted">—</span>';

    const strike = r.strike_price != null
      ? formatNumber(r.strike_price, r.strike_price % 1 === 0 ? 0 : 2)
      : '—';

    return (
      '<tr>' +
        `<td class="code-cell">${escapeHtml(r.code)}</td>` +
        `<td class="name-cell">${escapeHtml(r.name || '')}</td>` +
        `<td>${typeBadge}</td>` +
        `<td class="num">${escapeHtml(strike)}</td>` +
        `<td>${escapeHtml(formatDate(r.last_trade_date))}</td>` +
        `<td class="notes-cell" title="${escapeHtml(r.notes || '')}">` +
          `${escapeHtml(summarizeNotes(r.notes))}` +
        '</td>' +
      '</tr>'
    );
  }

  // === 篩選器互動 ===
  function onFilterChange(evt) {
    // 認購 / 認售 互斥
    if (filterCall.checked && filterPut.checked) {
      if (evt && evt.target === filterCall) filterPut.checked = false;
      else filterCall.checked = false;
    }
    if (state.lastQuery) runWarrantQuery();
  }

  filterNewOnly.addEventListener('change', onFilterChange);
  filterCall.addEventListener('change', onFilterChange);
  filterPut.addEventListener('change', onFilterChange);

  resetBtn.addEventListener('click', () => {
    filterNewOnly.checked = false;
    filterCall.checked = false;
    filterPut.checked = false;
    if (state.lastQuery) runWarrantQuery();
  });

  // === 健康檢查 ===
  async function loadHealth() {
    try {
      const res = await fetch('/api/health');
      if (!res.ok) throw new Error('bad status');
      const data = await res.json();
      lastUpdatedEl.textContent = data.last_updated
        ? `資料更新時間：${data.last_updated}`
        : '資料更新時間：未知';
    } catch (err) {
      lastUpdatedEl.textContent = '資料更新時間：無法取得';
    }
  }

  // === 啟動 ===
  showEmpty();
  loadHealth();

  // 匯出供偵錯
  window.__twseApp = { state, runWarrantQuery };
})();
