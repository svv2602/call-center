import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { escapeHtml, closeModal } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import { makeSortable } from '../sorting.js';
import * as tw from '../tw.js';

// ═══════════════════════════════════════════════════════════
//  Инструменты
// ═══════════════════════════════════════════════════════════
async function loadTools() {
    const container = document.getElementById('toolsContent');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;
    try {
        const data = await api('/training/tools/');
        const items = data.items || [];
        container.innerHTML = `
            <div class="overflow-x-auto min-h-[480px]"><table class="${tw.table}" id="toolsTable"><thead><tr><th class="${tw.thSortable}" data-sortable>${t('training.toolName')}</th><th class="${tw.th}">${t('training.description')}</th><th class="${tw.thSortable}" data-sortable>${t('training.override')}</th><th class="${tw.th}">${t('training.actions')}</th></tr></thead><tbody>
            ${items.map(item => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}" data-label="${t('training.toolName')}"><span class="${tw.badgeBlue}">${escapeHtml(item.name)}</span></td>
                    <td class="${tw.td}" data-label="${t('training.description')}"><span class="${tw.mutedText}">${escapeHtml((item.effective_description || '').substring(0, 100))}${(item.effective_description || '').length > 100 ? '...' : ''}</span></td>
                    <td class="${tw.td}" data-label="${t('training.override')}">${item.has_override ? `<span class="${tw.badgeYellow}">${t('training.overridden')}</span>` : `<span class="${tw.badge}">${t('training.default')}</span>`}</td>
                    <td class="${tw.tdActions}" data-label="${t('training.actions')}">
                        <div class="flex flex-wrap gap-1">
                            <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.tools.editToolOverride('${escapeHtml(item.name)}')">${t('common.edit')}</button>
                            ${item.has_override ? `<button class="${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.tools.resetToolOverride('${escapeHtml(item.name)}')">${t('training.reset')}</button>` : ''}
                        </div>
                    </td>
                </tr>
            `).join('')}
            </tbody></table></div>`;

        makeSortable('toolsTable');
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('training.loadFailed', {error: escapeHtml(e.message)})}</div>`;
    }
}

async function editToolOverride(toolName) {
    try {
        const data = await api('/training/tools/');
        const tool = (data.items || []).find(t => t.name === toolName);
        if (!tool) { showToast(t('training.toolNotFound'), 'error'); return; }

        document.getElementById('toolOverrideModalTitle').textContent = `${t('training.editTool')}: ${toolName}`;
        document.getElementById('editToolName').value = toolName;
        document.getElementById('toolOriginalDescription').textContent = tool.description;
        document.getElementById('toolOverrideDescription').value = (tool.override && tool.override.description) || tool.description;
        document.getElementById('toolOverrideModal').classList.add('show');
    } catch (e) { showToast(t('training.loadFailed', {error: e.message}), 'error'); }
}

async function saveToolOverride() {
    const toolName = document.getElementById('editToolName').value;
    const description = document.getElementById('toolOverrideDescription').value.trim();
    if (!description) { showToast(t('training.descriptionRequired'), 'error'); return; }
    try {
        await api(`/training/tools/${toolName}`, { method: 'PATCH', body: JSON.stringify({ description }) });
        closeModal('toolOverrideModal');
        showToast(t('training.toolOverrideSaved'));
        loadTools();
    } catch (e) { showToast(t('training.saveFailed', {error: e.message}), 'error'); }
}

async function resetToolOverride(toolName) {
    if (!confirm(t('training.resetConfirm', {name: toolName}))) return;
    try {
        await api(`/training/tools/${toolName}`, { method: 'DELETE' });
        showToast(t('training.toolOverrideReset'));
        loadTools();
    } catch (e) { showToast(t('training.resetFailed', {error: e.message}), 'error'); }
}

// ═══════════════════════════════════════════════════════════
//  Init & exports
// ═══════════════════════════════════════════════════════════
export function init() {
    registerPageLoader('tools', loadTools);
}

window._pages = window._pages || {};
window._pages.tools = {
    loadTools, editToolOverride, saveToolOverride, resetToolOverride,
};
