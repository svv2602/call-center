import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { escapeHtml } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import * as tw from '../tw.js';

// ─── State ───────────────────────────────────────────────────
let _data = null;
let _activeTab = 'base';
let _basePhrases = [];
let _customPhrases = [];
let _baseFilter = '';
let _autoFilter = '';
let _customFilter = '';

// ═══════════════════════════════════════════════════════════
//  Load data
// ═══════════════════════════════════════════════════════════
async function loadData() {
    const container = document.getElementById('sttHintsContent');
    if (!container) return;
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;
    try {
        _data = await api('/admin/stt/phrase-hints');
        _basePhrases = [...(_data.base_phrases || [])];
        _customPhrases = [...(_data.custom_phrases || [])];
        render();
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('sttHints.loadFailed', { error: escapeHtml(e.message) })}</div>`;
    }
}

// ═══════════════════════════════════════════════════════════
//  Render
// ═══════════════════════════════════════════════════════════
function render() {
    const container = document.getElementById('sttHintsContent');
    if (!container || !_data) return;

    const stats = _data.stats || {};
    const pct = stats.google_limit ? Math.round((stats.total / stats.google_limit) * 100) : 0;
    const updatedAt = stats.updated_at
        ? new Date(stats.updated_at).toLocaleString()
        : t('sttHints.neverSynced');

    const customizedBadge = stats.base_customized
        ? `<span class="${tw.badgeYellow} ml-1">${t('sttHints.modified')}</span>`
        : '';

    let html = `
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
            <div class="bg-neutral-50 dark:bg-neutral-800 rounded-lg p-3 text-center">
                <div class="text-lg font-bold text-neutral-900 dark:text-neutral-100">${stats.base_count || 0}</div>
                <div class="text-xs text-neutral-500 dark:text-neutral-400">${t('sttHints.base')}${customizedBadge}</div>
            </div>
            <div class="bg-neutral-50 dark:bg-neutral-800 rounded-lg p-3 text-center">
                <div class="text-lg font-bold text-neutral-900 dark:text-neutral-100">${stats.auto_count || 0}</div>
                <div class="text-xs text-neutral-500 dark:text-neutral-400">${t('sttHints.auto')}</div>
            </div>
            <div class="bg-neutral-50 dark:bg-neutral-800 rounded-lg p-3 text-center">
                <div class="text-lg font-bold text-neutral-900 dark:text-neutral-100">${stats.custom_count || 0}</div>
                <div class="text-xs text-neutral-500 dark:text-neutral-400">${t('sttHints.custom')}</div>
            </div>
            <div class="bg-neutral-50 dark:bg-neutral-800 rounded-lg p-3 text-center">
                <div class="text-lg font-bold ${pct > 90 ? 'text-red-600' : 'text-neutral-900 dark:text-neutral-100'}">${stats.total || 0} / ${stats.google_limit || 5000}</div>
                <div class="text-xs text-neutral-500 dark:text-neutral-400">${t('sttHints.total')} (${pct}%)</div>
            </div>
        </div>
        <div class="text-xs text-neutral-500 dark:text-neutral-400 mb-4">
            ${t('sttHints.lastSync')}: ${escapeHtml(updatedAt)}
        </div>`;

    // Tabs
    html += `<div class="${tw.tabBar}">
        <button class="${tw.tabBtn} ${_activeTab === 'base' ? 'border-blue-600 text-blue-600 dark:text-blue-400 dark:border-blue-400 font-semibold' : ''}" onclick="window._pages.sttHints.switchTab('base')">${t('sttHints.tabBase')} (${_basePhrases.length})</button>
        <button class="${tw.tabBtn} ${_activeTab === 'auto' ? 'border-blue-600 text-blue-600 dark:text-blue-400 dark:border-blue-400 font-semibold' : ''}" onclick="window._pages.sttHints.switchTab('auto')">${t('sttHints.tabAuto')} (${(_data.auto_phrases || []).length})</button>
        <button class="${tw.tabBtn} ${_activeTab === 'custom' ? 'border-blue-600 text-blue-600 dark:text-blue-400 dark:border-blue-400 font-semibold' : ''}" onclick="window._pages.sttHints.switchTab('custom')">${t('sttHints.tabCustom')} (${_customPhrases.length})</button>
    </div>`;

    // Tab content
    if (_activeTab === 'base') {
        html += renderBaseTab();
    } else if (_activeTab === 'auto') {
        html += renderAutoTab();
    } else {
        html += renderCustomTab();
    }

    container.innerHTML = html;
}

function _filterPhrases(phrases, filter) {
    if (!filter) return phrases;
    const lower = filter.toLowerCase();
    return phrases.filter(p => p.toLowerCase().includes(lower));
}

function renderBaseTab() {
    const filtered = _filterPhrases(_basePhrases, _baseFilter);
    let html = `
        <div class="flex flex-wrap items-center gap-2 mb-3">
            <input type="text" id="sttBaseSearch" class="${tw.filterInput} flex-1 min-w-48" placeholder="${t('sttHints.searchPlaceholder')}" value="${escapeHtml(_baseFilter)}" oninput="window._pages.sttHints.filterBase(this.value)">
            <span class="text-xs text-neutral-400">${filtered.length} / ${_basePhrases.length}</span>
        </div>
        <div class="flex items-center gap-2 mb-3">
            <input type="text" id="sttBaseNewPhrase" class="${tw.filterInput} flex-1" placeholder="${t('sttHints.addPhrasePlaceholder')}" onkeydown="if(event.key==='Enter')window._pages.sttHints.addBasePhrase()">
            <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.sttHints.addBasePhrase()">${t('common.add')}</button>
        </div>
        <div class="max-h-96 overflow-y-auto border border-neutral-200 dark:border-neutral-700 rounded-lg mb-3">`;

    if (filtered.length === 0) {
        html += `<div class="${tw.emptyState}">${t('sttHints.noResults')}</div>`;
    } else {
        html += '<ul class="divide-y divide-neutral-100 dark:divide-neutral-800">';
        for (const phrase of filtered) {
            html += `<li class="flex items-center justify-between px-3 py-1.5 text-sm text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800/50">
                <span class="font-mono text-xs">${escapeHtml(phrase)}</span>
                <button class="text-neutral-400 hover:text-red-500 text-xs ml-2 shrink-0" onclick="window._pages.sttHints.removeBasePhrase('${escapeHtml(phrase.replace(/'/g, "\\'"))}')" title="${t('common.delete')}">&#10005;</button>
            </li>`;
        }
        html += '</ul>';
    }
    html += '</div>';

    html += `<div class="flex flex-wrap items-center gap-2">
        <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.sttHints.saveBase()">${t('sttHints.saveBase')}</button>
        <button class="${tw.btnSm} text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20" onclick="window._pages.sttHints.resetBase()">${t('sttHints.resetBase')}</button>
    </div>`;

    return html;
}

function renderAutoTab() {
    const autoPhrases = _data.auto_phrases || [];
    const filtered = _filterPhrases(autoPhrases, _autoFilter);
    let html = `
        <div class="flex flex-wrap items-center gap-2 mb-3">
            <input type="text" id="sttAutoSearch" class="${tw.filterInput} flex-1 min-w-48" placeholder="${t('sttHints.searchPlaceholder')}" value="${escapeHtml(_autoFilter)}" oninput="window._pages.sttHints.filterAuto(this.value)">
            <span class="text-xs text-neutral-400">${filtered.length} / ${autoPhrases.length}</span>
        </div>
        <div class="max-h-96 overflow-y-auto border border-neutral-200 dark:border-neutral-700 rounded-lg mb-3">`;

    if (filtered.length === 0) {
        html += `<div class="${tw.emptyState}">${t('sttHints.noResults')}</div>`;
    } else {
        html += '<ul class="divide-y divide-neutral-100 dark:divide-neutral-800">';
        for (const phrase of filtered) {
            html += `<li class="px-3 py-1.5 text-sm text-neutral-700 dark:text-neutral-300">
                <span class="font-mono text-xs">${escapeHtml(phrase)}</span>
            </li>`;
        }
        html += '</ul>';
    }
    html += '</div>';

    html += `<div class="flex flex-wrap items-center gap-2">
        <button class="${tw.btnSm} border border-neutral-300 dark:border-neutral-600 text-neutral-700 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-800" onclick="window._pages.sttHints.refreshFromCatalog()">${t('sttHints.refreshCatalog')}</button>
    </div>`;

    return html;
}

function renderCustomTab() {
    const filtered = _filterPhrases(_customPhrases, _customFilter);
    let html = `
        <div class="flex flex-wrap items-center gap-2 mb-3">
            <input type="text" id="sttCustomSearch" class="${tw.filterInput} flex-1 min-w-48" placeholder="${t('sttHints.searchPlaceholder')}" value="${escapeHtml(_customFilter)}" oninput="window._pages.sttHints.filterCustom(this.value)">
            <span class="text-xs text-neutral-400">${filtered.length} / ${_customPhrases.length}</span>
        </div>
        <div class="flex items-center gap-2 mb-3">
            <input type="text" id="sttCustomNewPhrase" class="${tw.filterInput} flex-1" placeholder="${t('sttHints.addPhrasePlaceholder')}" onkeydown="if(event.key==='Enter')window._pages.sttHints.addCustomPhrase()">
            <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.sttHints.addCustomPhrase()">${t('common.add')}</button>
        </div>
        <div class="text-[11px] text-neutral-400 dark:text-neutral-500 mb-3">${t('sttHints.customHint')}</div>
        <div class="max-h-96 overflow-y-auto border border-neutral-200 dark:border-neutral-700 rounded-lg mb-3">`;

    if (filtered.length === 0) {
        html += `<div class="${tw.emptyState}">${t('sttHints.noResults')}</div>`;
    } else {
        html += '<ul class="divide-y divide-neutral-100 dark:divide-neutral-800">';
        for (const phrase of filtered) {
            html += `<li class="flex items-center justify-between px-3 py-1.5 text-sm text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800/50">
                <span class="font-mono text-xs">${escapeHtml(phrase)}</span>
                <button class="text-neutral-400 hover:text-red-500 text-xs ml-2 shrink-0" onclick="window._pages.sttHints.removeCustomPhrase('${escapeHtml(phrase.replace(/'/g, "\\'"))}')" title="${t('common.delete')}">&#10005;</button>
            </li>`;
        }
        html += '</ul>';
    }
    html += '</div>';

    html += `<div class="flex flex-wrap items-center gap-2">
        <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.sttHints.saveCustom()">${t('sttHints.saveCustom')}</button>
    </div>`;

    return html;
}

// ═══════════════════════════════════════════════════════════
//  Tab switching
// ═══════════════════════════════════════════════════════════
function switchTab(tab) {
    _activeTab = tab;
    render();
}

// ═══════════════════════════════════════════════════════════
//  Filters
// ═══════════════════════════════════════════════════════════
function filterBase(value) { _baseFilter = value; render(); }
function filterAuto(value) { _autoFilter = value; render(); }
function filterCustom(value) { _customFilter = value; render(); }

// ═══════════════════════════════════════════════════════════
//  Base phrase operations
// ═══════════════════════════════════════════════════════════
function addBasePhrase() {
    const input = document.getElementById('sttBaseNewPhrase');
    if (!input) return;
    const phrase = input.value.trim();
    if (!phrase) return;
    if (_basePhrases.includes(phrase)) {
        showToast(t('sttHints.duplicatePhrase'), 'error');
        return;
    }
    _basePhrases.push(phrase);
    input.value = '';
    render();
}

function removeBasePhrase(phrase) {
    _basePhrases = _basePhrases.filter(p => p !== phrase);
    render();
}

async function saveBase() {
    try {
        await api('/admin/stt/phrase-hints/base', {
            method: 'PATCH',
            body: JSON.stringify({ phrases: _basePhrases }),
        });
        showToast(t('sttHints.baseSaved', { count: _basePhrases.length }), 'success');
        await loadData();
    } catch (e) {
        showToast(t('sttHints.saveFailed', { error: e.message }), 'error');
    }
}

async function resetBase() {
    if (!confirm(t('sttHints.resetBaseConfirm'))) return;
    try {
        await api('/admin/stt/phrase-hints/base/reset', { method: 'POST' });
        showToast(t('sttHints.resetBaseDone'), 'success');
        await loadData();
    } catch (e) {
        showToast(t('sttHints.saveFailed', { error: e.message }), 'error');
    }
}

// ═══════════════════════════════════════════════════════════
//  Custom phrase operations
// ═══════════════════════════════════════════════════════════
function addCustomPhrase() {
    const input = document.getElementById('sttCustomNewPhrase');
    if (!input) return;
    const phrase = input.value.trim();
    if (!phrase) return;
    if (_customPhrases.includes(phrase)) {
        showToast(t('sttHints.duplicatePhrase'), 'error');
        return;
    }
    _customPhrases.push(phrase);
    input.value = '';
    render();
}

function removeCustomPhrase(phrase) {
    _customPhrases = _customPhrases.filter(p => p !== phrase);
    render();
}

async function saveCustom() {
    try {
        await api('/admin/stt/phrase-hints/custom', {
            method: 'PATCH',
            body: JSON.stringify({ phrases: _customPhrases }),
        });
        showToast(t('sttHints.customSaved', { count: _customPhrases.length }), 'success');
        await loadData();
    } catch (e) {
        showToast(t('sttHints.saveFailed', { error: e.message }), 'error');
    }
}

// ═══════════════════════════════════════════════════════════
//  Catalog refresh
// ═══════════════════════════════════════════════════════════
async function refreshFromCatalog() {
    showToast(t('sttHints.refreshing'), 'info');
    try {
        await api('/admin/stt/phrase-hints/refresh', { method: 'POST' });
        showToast(t('sttHints.refreshed'), 'success');
        await loadData();
    } catch (e) {
        showToast(t('sttHints.refreshFailed', { error: e.message }), 'error');
    }
}

// ═══════════════════════════════════════════════════════════
//  Init
// ═══════════════════════════════════════════════════════════
export function init() {
    registerPageLoader('stt-hints', loadData);
}

window._pages = window._pages || {};
window._pages.sttHints = {
    switchTab, filterBase, filterAuto, filterCustom,
    addBasePhrase, removeBasePhrase, saveBase, resetBase,
    addCustomPhrase, removeCustomPhrase, saveCustom,
    refreshFromCatalog,
};
