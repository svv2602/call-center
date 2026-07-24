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
        render();
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('sttCorrections.loadFailed', { error: escapeHtml(e.message) })}</div>`;
    }
}

// ═══════════════════════════════════════════════════════════
//  Render
// ═══════════════════════════════════════════════════════════
function render() {
    const container = document.getElementById('sttCorrectionsContent');
    if (!container) return;

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
    container.innerHTML = html;
}

function setFilter(v) {
    _filter = v || '';
    render();
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
};
