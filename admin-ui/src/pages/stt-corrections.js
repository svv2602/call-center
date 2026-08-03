import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { escapeHtml } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import * as tw from '../tw.js';

// ─── State ───────────────────────────────────────────────────
let _rules = [];
let _validContexts = ['any', 'date', 'time', 'city', 'plate', 'station', 'phone'];
let _filter = '';
let _activeTab = 'rules';  // 'rules' | 'suggestions'
let _suggestions = [];
let _suggestionsLoaded = false;

// ═══════════════════════════════════════════════════════════
//  Load
// ═══════════════════════════════════════════════════════════
async function loadData() {
    const container = document.getElementById('sttCorrectionsContent');
    if (!container) return;
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;
    try {
        const data = await api('/admin/stt/corrections');
        _rules = data.rules || [];
        if (Array.isArray(data.valid_contexts) && data.valid_contexts.length) {
            _validContexts = data.valid_contexts;
        }
        // Load suggestion count lazily so the tab header can show a badge.
        if (!_suggestionsLoaded) {
            try {
                const s = await api('/admin/stt/corrections/suggestions?status=pending&limit=200');
                _suggestions = s.suggestions || [];
                _suggestionsLoaded = true;
            } catch (_) {
                _suggestions = [];
            }
        }
        render();
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('sttCorrections.loadFailed', { error: escapeHtml(e.message) })}</div>`;
    }
}

async function _reloadSuggestions() {
    try {
        const s = await api('/admin/stt/corrections/suggestions?status=pending&limit=200');
        _suggestions = s.suggestions || [];
        _suggestionsLoaded = true;
    } catch (_) {
        _suggestions = [];
    }
}

function switchTab(tab) {
    _activeTab = tab;
    render();
}

// ═══════════════════════════════════════════════════════════
//  Render — tabs
// ═══════════════════════════════════════════════════════════
function render() {
    const container = document.getElementById('sttCorrectionsContent');
    if (!container) return;

    const pendingCount = _suggestions.filter(s => s.status === 'pending').length;
    const tabsHtml = `
        <div class="flex gap-1 border-b border-neutral-200 dark:border-neutral-800 mb-4">
            <button onclick="window._pages.sttCorrections.switchTab('rules')"
                class="px-4 py-2 text-sm border-b-2 ${_activeTab === 'rules' ? 'border-blue-500 text-blue-600 font-semibold' : 'border-transparent text-neutral-500 hover:text-neutral-800'}">
                ${t('sttCorrections.tabRules')} <span class="text-xs text-neutral-400">(${_rules.length})</span>
            </button>
            <button onclick="window._pages.sttCorrections.switchTab('suggestions')"
                class="px-4 py-2 text-sm border-b-2 ${_activeTab === 'suggestions' ? 'border-blue-500 text-blue-600 font-semibold' : 'border-transparent text-neutral-500 hover:text-neutral-800'}">
                ${t('sttCorrections.tabSuggestions')}
                ${pendingCount > 0 ? `<span class="ml-1 inline-block bg-amber-100 text-amber-800 text-xs px-1.5 py-0.5 rounded">${pendingCount}</span>` : ''}
            </button>
        </div>`;

    if (_activeTab === 'suggestions') {
        container.innerHTML = tabsHtml + _renderSuggestionsTab();
        return;
    }

    container.innerHTML = tabsHtml + _renderRulesTab();
}

function _renderRulesTab() {
    const total = _rules.length;
    const enabled = _rules.filter(r => r.enabled !== false).length;
    const filter = _filter.trim().toLowerCase();
    const visible = filter
        ? _rules.filter(r =>
            (r.pattern || '').toLowerCase().includes(filter) ||
            (r.replacement || '').toLowerCase().includes(filter) ||
            (r.note || '').toLowerCase().includes(filter) ||
            (r.context_hint || '').toLowerCase().includes(filter))
        : _rules;

    let html = `
        <div class="flex flex-wrap gap-3 items-center mb-4">
            <div class="${tw.badgeGray}">${t('sttCorrections.total')}: ${total}</div>
            <div class="${tw.badgeGreen}">${t('sttCorrections.enabled')}: ${enabled}</div>
            <div class="flex-1"></div>
            <input type="text" placeholder="${t('sttCorrections.filterPlaceholder')}"
                oninput="window._pages.sttCorrections.setFilter(this.value)"
                value="${escapeHtml(_filter)}"
                class="${tw.formInput} max-w-xs"/>
            <button onclick="window._pages.sttCorrections.openTest()" class="${tw.btnSecondary}">${t('sttCorrections.test')}</button>
            <button onclick="window._pages.sttCorrections.openImport()" class="${tw.btnSecondary}">${t('sttCorrections.import')}</button>
            <button onclick="window._pages.sttCorrections.openAdd()" class="${tw.btnPrimary}">${t('sttCorrections.addRule')}</button>
        </div>

        <div class="overflow-x-auto">
        <table class="${tw.table}">
            <thead class="">
                <tr>
                    <th class="${tw.th}">${t('sttCorrections.pattern')}</th>
                    <th class="${tw.th}">${t('sttCorrections.replacement')}</th>
                    <th class="${tw.th}">${t('sttCorrections.context')}</th>
                    <th class="${tw.th}">${t('sttCorrections.status')}</th>
                    <th class="${tw.th}">${t('sttCorrections.note')}</th>
                    <th class="${tw.th} text-right">${t('sttCorrections.actions')}</th>
                </tr>
            </thead>
            <tbody class="">`;
    if (visible.length === 0) {
        html += `<tr><td colspan="6" class="p-4 text-center text-neutral-500">${t('sttCorrections.empty')}</td></tr>`;
    } else {
        for (const r of visible) {
            const ctx = r.context_hint || 'any';
            const badge = ctx === 'any' ? tw.badgeNeutral : tw.badgeBlue;
            const statusBadge = r.enabled === false ? tw.badgeRed : tw.badgeGreen;
            const statusLabel = r.enabled === false ? t('sttCorrections.disabled') : t('sttCorrections.enabled');
            html += `
                <tr>
                    <td class="${tw.td}"><code class="text-xs">${escapeHtml(r.pattern || '')}</code></td>
                    <td class="${tw.td}"><code class="text-xs">${escapeHtml(r.replacement || '')}</code></td>
                    <td class="${tw.td}"><span class="${badge}">${escapeHtml(ctx)}</span></td>
                    <td class="${tw.td}"><span class="${statusBadge}">${statusLabel}</span></td>
                    <td class="${tw.td} text-xs text-neutral-500">${escapeHtml(r.note || '')}</td>
                    <td class="${tw.td} text-right whitespace-nowrap">
                        <button onclick="window._pages.sttCorrections.toggleEnabled('${escapeHtml(r.id)}')" class="text-blue-600 hover:underline text-sm cursor-pointer mr-2">${r.enabled === false ? t('sttCorrections.enable') : t('sttCorrections.disable')}</button>
                        <button onclick="window._pages.sttCorrections.openEdit('${escapeHtml(r.id)}')" class="text-blue-600 hover:underline text-sm cursor-pointer mr-2">${t('common.edit')}</button>
                        <button onclick="window._pages.sttCorrections.remove('${escapeHtml(r.id)}')" class="text-red-600 hover:underline text-sm cursor-pointer">${t('common.delete')}</button>
                    </td>
                </tr>`;
        }
    }
    html += `</tbody></table></div>`;
    return html;
}

function setFilter(v) {
    _filter = v || '';
    render();
}

// ═══════════════════════════════════════════════════════════
//  Suggestions tab
// ═══════════════════════════════════════════════════════════
function _renderSuggestionsTab() {
    const pending = _suggestions.filter(s => s.status === 'pending');

    let html = `
        <div class="flex flex-wrap gap-3 items-center mb-4">
            <div class="${tw.badgeGray}">${t('sttCorrections.suggestions.total')}: ${_suggestions.length}</div>
            <div class="${tw.badgeYellow}">${t('sttCorrections.suggestions.pending')}: ${pending.length}</div>
            <div class="flex-1"></div>
            <button onclick="window._pages.sttCorrections.rescan()" class="${tw.btnSecondary}">${t('sttCorrections.suggestions.rescan')}</button>
        </div>
        <p class="text-xs text-neutral-500 mb-3">${t('sttCorrections.suggestions.hint')}</p>`;

    if (pending.length === 0) {
        html += `<div class="${tw.emptyState}">${t('sttCorrections.suggestions.empty')}</div>`;
        return html;
    }

    html += `
        <div class="overflow-x-auto">
        <table class="${tw.table}">
            <thead>
                <tr>
                    <th class="${tw.th}">${t('sttCorrections.suggestions.token')}</th>
                    <th class="${tw.th}">${t('sttCorrections.suggestions.count')}</th>
                    <th class="${tw.th}">${t('sttCorrections.suggestions.context')}</th>
                    <th class="${tw.th}">${t('sttCorrections.suggestions.proposedReplacement')}</th>
                    <th class="${tw.th}">${t('sttCorrections.suggestions.samples')}</th>
                    <th class="${tw.th} text-right">${t('sttCorrections.actions')}</th>
                </tr>
            </thead>
            <tbody>`;

    for (const s of pending) {
        const ctxBadge = s.detected_context === 'any' ? tw.badgeGray : tw.badgeBlue;
        const samples = Array.isArray(s.sample_transcripts) ? s.sample_transcripts : [];
        const sampleTexts = samples.slice(0, 3).map(sam =>
            `<div class="text-xs text-neutral-500 truncate max-w-xs" title="${escapeHtml(sam.text || '')}">"${escapeHtml(sam.text || '')}"</div>`
        ).join('');
        const replacement = s.proposed_replacement
            ? `<code class="text-xs">${escapeHtml(s.proposed_replacement)}</code>${s.match_distance != null ? ` <span class="text-xs text-neutral-400">(d=${s.match_distance})</span>` : ''}`
            : `<span class="text-xs text-neutral-400 italic">${t('sttCorrections.suggestions.noAutoMatch')}</span>`;

        html += `
            <tr>
                <td class="${tw.td}"><code class="text-sm">${escapeHtml(s.bad_token || '')}</code></td>
                <td class="${tw.td}"><span class="${tw.badgeGray}">${s.occurrence_count}</span></td>
                <td class="${tw.td}"><span class="${ctxBadge}">${escapeHtml(s.detected_context || 'any')}</span></td>
                <td class="${tw.td}">${replacement}</td>
                <td class="${tw.td}">${sampleTexts || '<span class="text-xs text-neutral-400">—</span>'}</td>
                <td class="${tw.td} text-right whitespace-nowrap">
                    <button onclick="window._pages.sttCorrections.openApprove('${escapeHtml(s.id)}')" class="text-green-600 hover:underline text-sm cursor-pointer mr-2">${t('sttCorrections.suggestions.approve')}</button>
                    <button onclick="window._pages.sttCorrections.rejectSuggestion('${escapeHtml(s.id)}')" class="text-red-600 hover:underline text-sm cursor-pointer">${t('sttCorrections.suggestions.reject')}</button>
                </td>
            </tr>`;
    }

    html += `</tbody></table></div>`;
    return html;
}

function openApprove(id) {
    const s = _suggestions.find(x => x.id === id);
    if (!s) return;

    const html = `
        <div id="sttSuggestModal" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div class="bg-white dark:bg-neutral-900 rounded-lg p-6 shadow-xl w-full max-w-lg">
            <h3 class="text-lg font-semibold mb-3">${t('sttCorrections.suggestions.approveTitle')}</h3>
            <div class="text-xs text-neutral-500 mb-3">${t('sttCorrections.suggestions.approveHint', { token: escapeHtml(s.bad_token) })}</div>
            <label class="block mb-2 text-sm">${t('sttCorrections.pattern')} <span class="text-xs text-neutral-500">(regex)</span></label>
            <input id="suggPattern" class="${tw.formInput} w-full mb-3" value="${escapeHtml(s.proposed_pattern || '')}"/>
            <label class="block mb-2 text-sm">${t('sttCorrections.replacement')}</label>
            <input id="suggReplacement" class="${tw.formInput} w-full mb-3" value="${escapeHtml(s.proposed_replacement || '')}" placeholder="${t('sttCorrections.suggestions.replacementPlaceholder')}"/>
            <label class="block mb-2 text-sm">${t('sttCorrections.context')}</label>
            <select id="suggContext" class="${tw.formInput} w-full mb-3">${_contextOptions(s.detected_context || 'any')}</select>
            <label class="block mb-2 text-sm">${t('sttCorrections.note')}</label>
            <input id="suggNote" class="${tw.formInput} w-full mb-3" value="auto-suggested from token '${escapeHtml(s.bad_token)}' (seen ${s.occurrence_count}×)"/>
            <div class="flex justify-end gap-2">
                <button onclick="window._pages.sttCorrections.closeApprove()" class="${tw.btnSecondary}">${t('common.cancel')}</button>
                <button onclick="window._pages.sttCorrections.submitApprove('${escapeHtml(id)}')" class="${tw.btnPrimary}">${t('sttCorrections.suggestions.createRule')}</button>
            </div>
          </div>
        </div>`;
    document.body.insertAdjacentHTML('beforeend', html);
}

function closeApprove() {
    const el = document.getElementById('sttSuggestModal');
    if (el) el.remove();
}

async function submitApprove(id) {
    const pattern = document.getElementById('suggPattern').value.trim();
    const replacement = document.getElementById('suggReplacement').value;
    const context_hint = document.getElementById('suggContext').value;
    const note = document.getElementById('suggNote').value;
    if (!pattern) {
        showToast(t('sttCorrections.patternRequired'), 'error');
        return;
    }
    if (!replacement.trim()) {
        if (!confirm(t('sttCorrections.suggestions.emptyReplacementConfirm'))) return;
    }
    try {
        await api(`/admin/stt/corrections/suggestions/${encodeURIComponent(id)}/approve`, {
            method: 'POST',
            body: JSON.stringify({ pattern, replacement, context_hint, note }),
            headers: { 'Content-Type': 'application/json' },
        });
        closeApprove();
        showToast(t('sttCorrections.suggestions.promoted'), 'success');
        await _reloadSuggestions();
        await loadData();
    } catch (e) {
        showToast(t('sttCorrections.saveFailed', { error: e.message }), 'error');
    }
}

async function rejectSuggestion(id) {
    const reason = prompt(t('sttCorrections.suggestions.rejectPrompt')) || '';
    try {
        await api(`/admin/stt/corrections/suggestions/${encodeURIComponent(id)}/reject`, {
            method: 'POST',
            body: JSON.stringify({ reason }),
            headers: { 'Content-Type': 'application/json' },
        });
        showToast(t('sttCorrections.suggestions.rejected'), 'success');
        await _reloadSuggestions();
        render();
    } catch (e) {
        showToast(t('sttCorrections.saveFailed', { error: e.message }), 'error');
    }
}

async function rescan() {
    try {
        const r = await api('/admin/stt/corrections/suggestions/rescan', {
            method: 'POST',
            body: JSON.stringify({ days: 30, min_occurrences: 2 }),
            headers: { 'Content-Type': 'application/json' },
        });
        showToast(t('sttCorrections.suggestions.rescanQueued', { id: (r.task_id || '').slice(0, 8) }), 'success');
    } catch (e) {
        showToast(t('sttCorrections.saveFailed', { error: e.message }), 'error');
    }
}

// ═══════════════════════════════════════════════════════════
//  Modals — Add / Edit / Test
// ═══════════════════════════════════════════════════════════
function _contextOptions(selected) {
    return _validContexts.map(c =>
        `<option value="${c === 'any' ? '' : c}" ${((selected || 'any') === c) ? 'selected' : ''}>${c}</option>`
    ).join('');
}

function _openRuleModal(mode, rule) {
    const isEdit = mode === 'edit';
    const title = isEdit ? t('sttCorrections.editRule') : t('sttCorrections.addRule');
    const html = `
        <div id="sttCorrModal" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div class="bg-white dark:bg-neutral-900 rounded-lg p-6 shadow-xl w-full max-w-lg">
            <h3 class="text-lg font-semibold mb-3">${title}</h3>
            <label class="block mb-2 text-sm">${t('sttCorrections.pattern')} <span class="text-xs text-neutral-500">(regex)</span></label>
            <input id="corrPattern" class="${tw.formInput} w-full mb-3" value="${escapeHtml(rule?.pattern || '')}" placeholder="e.g. \\bлет\\b"/>
            <label class="block mb-2 text-sm">${t('sttCorrections.replacement')}</label>
            <input id="corrReplacement" class="${tw.formInput} w-full mb-3" value="${escapeHtml(rule?.replacement || '')}" placeholder="e.g. липня"/>
            <label class="block mb-2 text-sm">${t('sttCorrections.context')}</label>
            <select id="corrContext" class="${tw.formInput} w-full mb-3">${_contextOptions(rule?.context_hint || 'any')}</select>
            <label class="block mb-2 text-sm">${t('sttCorrections.note')}</label>
            <input id="corrNote" class="${tw.formInput} w-full mb-3" value="${escapeHtml(rule?.note || '')}"/>
            <label class="flex items-center gap-2 mb-4 text-sm">
                <input type="checkbox" id="corrEnabled" ${rule?.enabled !== false ? 'checked' : ''}/>
                ${t('sttCorrections.enabledLabel')}
            </label>
            <div class="flex justify-end gap-2">
                <button onclick="window._pages.sttCorrections.closeModal()" class="${tw.btnSecondary}">${t('common.cancel')}</button>
                <button onclick="window._pages.sttCorrections.save('${isEdit ? escapeHtml(rule.id) : ''}')" class="${tw.btnPrimary}">${t('common.save')}</button>
            </div>
          </div>
        </div>`;
    document.body.insertAdjacentHTML('beforeend', html);
}

function openAdd() {
    _openRuleModal('add', null);
}

function openEdit(id) {
    const rule = _rules.find(r => r.id === id);
    if (!rule) return;
    _openRuleModal('edit', rule);
}

function closeModal() {
    const el = document.getElementById('sttCorrModal');
    if (el) el.remove();
    const el2 = document.getElementById('sttCorrTestModal');
    if (el2) el2.remove();
}

async function save(id) {
    const pattern = document.getElementById('corrPattern').value.trim();
    const replacement = document.getElementById('corrReplacement').value;
    const context_hint = document.getElementById('corrContext').value;
    const note = document.getElementById('corrNote').value;
    const enabled = document.getElementById('corrEnabled').checked;
    if (!pattern) {
        showToast(t('sttCorrections.patternRequired'), 'error');
        return;
    }
    const body = { pattern, replacement, context_hint, note, enabled, flags: 'i' };
    try {
        if (id) {
            await api(`/admin/stt/corrections/${encodeURIComponent(id)}`, {
                method: 'PUT',
                body: JSON.stringify(body),
                headers: { 'Content-Type': 'application/json' },
            });
        } else {
            await api('/admin/stt/corrections', {
                method: 'POST',
                body: JSON.stringify(body),
                headers: { 'Content-Type': 'application/json' },
            });
        }
        closeModal();
        showToast(t('sttCorrections.saved'), 'success');
        await loadData();
    } catch (e) {
        showToast(t('sttCorrections.saveFailed', { error: e.message }), 'error');
    }
}

async function remove(id) {
    if (!confirm(t('sttCorrections.confirmDelete'))) return;
    try {
        await api(`/admin/stt/corrections/${encodeURIComponent(id)}`, { method: 'DELETE' });
        showToast(t('sttCorrections.deleted'), 'success');
        await loadData();
    } catch (e) {
        showToast(t('sttCorrections.deleteFailed', { error: e.message }), 'error');
    }
}

async function toggleEnabled(id) {
    const rule = _rules.find(r => r.id === id);
    if (!rule) return;
    try {
        await api(`/admin/stt/corrections/${encodeURIComponent(id)}`, {
            method: 'PUT',
            body: JSON.stringify({ enabled: rule.enabled === false }),
            headers: { 'Content-Type': 'application/json' },
        });
        await loadData();
    } catch (e) {
        showToast(t('sttCorrections.saveFailed', { error: e.message }), 'error');
    }
}

// ─── Test modal ─────────────────────────────────────────────
function openTest() {
    const html = `
        <div id="sttCorrTestModal" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div class="bg-white dark:bg-neutral-900 rounded-lg p-6 shadow-xl w-full max-w-lg">
            <h3 class="text-lg font-semibold mb-3">${t('sttCorrections.testTitle')}</h3>
            <label class="block mb-2 text-sm">${t('sttCorrections.testInput')}</label>
            <textarea id="corrTestText" rows="3" class="${tw.formInput} w-full mb-3" placeholder="28 лет"></textarea>
            <label class="block mb-2 text-sm">${t('sttCorrections.testContext')}</label>
            <select id="corrTestContext" class="${tw.formInput} w-full mb-3">${_contextOptions('any')}</select>
            <div id="corrTestResult" class="mb-3"></div>
            <div class="flex justify-end gap-2">
                <button onclick="window._pages.sttCorrections.closeModal()" class="${tw.btnSecondary}">${t('common.close')}</button>
                <button onclick="window._pages.sttCorrections.runTest()" class="${tw.btnPrimary}">${t('sttCorrections.run')}</button>
            </div>
          </div>
        </div>`;
    document.body.insertAdjacentHTML('beforeend', html);
}

async function runTest() {
    const text = document.getElementById('corrTestText').value;
    const context_hint = document.getElementById('corrTestContext').value;
    const resultEl = document.getElementById('corrTestResult');
    try {
        const r = await api('/admin/stt/corrections/test', {
            method: 'POST',
            body: JSON.stringify({ text, context_hint }),
            headers: { 'Content-Type': 'application/json' },
        });
        const changedBadge = r.changed ? `<span class="${tw.badgeGreen}">${t('sttCorrections.changed')}</span>` : `<span class="${tw.badgeGray}">${t('sttCorrections.unchanged')}</span>`;
        const rules = (r.applied_rule_ids || []).map(id => `<code>${escapeHtml(id)}</code>`).join(', ') || `<span class="text-xs text-neutral-500">${t('sttCorrections.noRules')}</span>`;
        resultEl.innerHTML = `
            <div class="border-t pt-3 mt-2 text-sm">
                <div class="mb-1">${changedBadge}</div>
                <div class="mb-1"><b>${t('sttCorrections.input')}:</b> <code>${escapeHtml(r.input)}</code></div>
                <div class="mb-1"><b>${t('sttCorrections.output')}:</b> <code>${escapeHtml(r.output)}</code></div>
                <div class="mb-1"><b>${t('sttCorrections.rulesApplied')}:</b> ${rules}</div>
            </div>`;
    } catch (e) {
        resultEl.innerHTML = `<div class="text-red-600 text-sm">${t('sttCorrections.testFailed', { error: escapeHtml(e.message) })}</div>`;
    }
}

// ─── Import modal ───────────────────────────────────────────
function openImport() {
    const html = `
        <div id="sttCorrImportModal" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div class="bg-white dark:bg-neutral-900 rounded-lg p-6 shadow-xl w-full max-w-2xl">
            <h3 class="text-lg font-semibold mb-3">${t('sttCorrections.importTitle')}</h3>
            <p class="text-xs text-neutral-500 mb-3">${t('sttCorrections.importHint')}</p>
            <div class="flex items-center gap-3 mb-3">
                <input type="file" id="corrImportFile" accept=".tsv,.csv,.txt"
                    onchange="window._pages.sttCorrections._loadFileIntoTextarea(this)"
                    class="text-sm"/>
                <label class="text-sm flex items-center gap-1">
                    <input type="checkbox" id="corrImportSkipDup" checked/>
                    ${t('sttCorrections.importSkipDup')}
                </label>
                <label class="text-sm flex items-center gap-1">
                    ${t('sttCorrections.importDelimiter')}
                    <select id="corrImportDelim" class="${tw.formInput} text-sm py-0.5 px-1">
                        <option value="\t" selected>TAB</option>
                        <option value=",">,</option>
                        <option value=";">;</option>
                    </select>
                </label>
            </div>
            <textarea id="corrImportText" rows="10" spellcheck="false"
                class="${tw.formInput} w-full font-mono text-xs mb-3"
                placeholder="pattern${'\t'}replacement${'\t'}context_hint${'\t'}note&#10;\\bвифт\\b${'\t'}вівторок${'\t'}date${'\t'}from call abc123"></textarea>
            <div id="corrImportResult" class="mb-3"></div>
            <div class="flex justify-end gap-2">
                <button onclick="window._pages.sttCorrections.closeImport()" class="${tw.btnSecondary}">${t('common.close')}</button>
                <button onclick="window._pages.sttCorrections.runImport()" class="${tw.btnPrimary}">${t('sttCorrections.importRun')}</button>
            </div>
          </div>
        </div>`;
    document.body.insertAdjacentHTML('beforeend', html);
}

function closeImport() {
    const el = document.getElementById('sttCorrImportModal');
    if (el) el.remove();
}

function _loadFileIntoTextarea(input) {
    const file = input.files && input.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
        document.getElementById('corrImportText').value = e.target.result || '';
    };
    reader.readAsText(file, 'utf-8');
}

function _parseImportText(text, delim) {
    // Split into non-empty lines. Header row required.
    const lines = text.split(/\r?\n/).map(l => l).filter((l, i) => i === 0 || l.trim().length);
    if (lines.length < 2) return { rules: [], errors: ['no data rows'] };

    const header = lines[0].split(delim).map(s => s.trim());
    const idx = (name) => header.indexOf(name);
    if (idx('pattern') < 0 || idx('replacement') < 0) {
        return { rules: [], errors: ['header must contain pattern and replacement columns'] };
    }

    const iP = idx('pattern'), iR = idx('replacement');
    const iC = idx('context_hint'), iN = idx('note');
    const iE = idx('enabled'), iF = idx('flags');

    const rules = [];
    const errors = [];
    for (let n = 1; n < lines.length; n++) {
        const cols = lines[n].split(delim);
        const pattern = (cols[iP] || '').trim();
        const replacement = cols[iR] || '';
        if (!pattern) continue;
        if (!replacement.trim()) continue;  // uncurated row — skip silently
        const enabledRaw = iE >= 0 ? (cols[iE] || '').trim().toLowerCase() : 'true';
        rules.push({
            pattern,
            replacement,
            context_hint: iC >= 0 ? (cols[iC] || '').trim() : '',
            note: iN >= 0 ? (cols[iN] || '') : '',
            flags: iF >= 0 && (cols[iF] || '').trim() ? cols[iF].trim() : 'i',
            enabled: !['false', '0', 'no', 'off'].includes(enabledRaw),
        });
    }
    return { rules, errors };
}

async function runImport() {
    const text = document.getElementById('corrImportText').value || '';
    const delim = document.getElementById('corrImportDelim').value || '\t';
    const skipDup = document.getElementById('corrImportSkipDup').checked;
    const resultEl = document.getElementById('corrImportResult');

    const parsed = _parseImportText(text, delim);
    if (parsed.errors.length) {
        resultEl.innerHTML = `<div class="text-red-600 text-sm">${escapeHtml(parsed.errors.join('; '))}</div>`;
        return;
    }
    if (parsed.rules.length === 0) {
        resultEl.innerHTML = `<div class="text-neutral-500 text-sm">${t('sttCorrections.importEmpty')}</div>`;
        return;
    }

    resultEl.innerHTML = `<div class="text-sm text-neutral-500">${t('sttCorrections.importSending', { n: parsed.rules.length })}</div>`;
    try {
        const r = await api('/admin/stt/corrections/bulk', {
            method: 'POST',
            body: JSON.stringify({ rules: parsed.rules, skip_duplicates: skipDup }),
            headers: { 'Content-Type': 'application/json' },
        });
        const bad = (r.results || []).filter(x => x.status === 'error');
        const skipped = (r.results || []).filter(x => x.status === 'skipped');
        let html = `
            <div class="text-sm">
                <span class="${tw.badgeGreen}">${t('sttCorrections.importCreated', { n: r.created || 0 })}</span>
                <span class="${tw.badgeGray}">${t('sttCorrections.importSkipped', { n: r.skipped || 0 })}</span>
                <span class="${r.errors ? tw.badgeRed : tw.badgeGray}">${t('sttCorrections.importErrors', { n: r.errors || 0 })}</span>
            </div>`;
        if (bad.length) {
            html += `<ul class="text-xs text-red-600 mt-2 list-disc pl-4">` +
                bad.slice(0, 10).map(x => `<li>row ${x.index}: ${escapeHtml(x.error || '')}</li>`).join('') +
                `</ul>`;
        }
        if (skipped.length && skipped.length <= 10) {
            html += `<ul class="text-xs text-neutral-500 mt-2 list-disc pl-4">` +
                skipped.map(x => `<li>row ${x.index}: ${escapeHtml(x.reason || '')}</li>`).join('') +
                `</ul>`;
        }
        resultEl.innerHTML = html;
        showToast(t('sttCorrections.importDone', { n: r.created || 0 }), 'success');
        await loadData();
    } catch (e) {
        resultEl.innerHTML = `<div class="text-red-600 text-sm">${escapeHtml(e.message)}</div>`;
    }
}

// ═══════════════════════════════════════════════════════════
//  Init
// ═══════════════════════════════════════════════════════════
export function init() {
    registerPageLoader('stt-corrections', loadData);
}

window._pages = window._pages || {};
window._pages.sttCorrections = {
    setFilter, openAdd, openEdit, closeModal, save, remove, toggleEnabled,
    openTest, runTest,
    openImport, closeImport, runImport, _loadFileIntoTextarea,
    switchTab,
    openApprove, closeApprove, submitApprove, rejectSuggestion, rescan,
};
