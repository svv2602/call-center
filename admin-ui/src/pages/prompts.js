import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { formatDate, escapeHtml, closeModal } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import * as tw from '../tw.js';

async function loadPromptVersions() {
    try {
        const data = await api('/prompts');
        const versions = data.versions || [];
        if (versions.length === 0) {
            document.getElementById('promptVersions').innerHTML = `
                <div class="mb-4"><button class="${tw.btnPrimary}" onclick="window._pages.prompts.showCreatePrompt()">${t('prompts.newVersion')}</button></div>
                <div class="${tw.emptyState}">${t('prompts.noVersions')}</div>`;
            return;
        }
        document.getElementById('promptVersions').innerHTML = `
            <div class="mb-4"><button class="${tw.btnPrimary}" onclick="window._pages.prompts.showCreatePrompt()">${t('prompts.newVersion')}</button></div>
            <div class="overflow-x-auto"><table class="${tw.table}"><thead><tr><th class="${tw.th}">${t('prompts.name')}</th><th class="${tw.th}">${t('prompts.active')}</th><th class="${tw.th}">${t('prompts.created')}</th><th class="${tw.th}">${t('prompts.action')}</th></tr></thead><tbody>
            ${versions.map(v => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}">${escapeHtml(v.name)}</td>
                    <td class="${tw.td}">${v.is_active ? `<span class="${tw.badgeGreen}">${t('prompts.activeLabel')}</span>` : ''}</td>
                    <td class="${tw.td}">${formatDate(v.created_at)}</td>
                    <td class="${tw.td}">${!v.is_active ? `<button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.prompts.activatePrompt('${v.id}')">${t('prompts.activateBtn')}</button>` : ''}</td>
                </tr>
            `).join('')}
            </tbody></table></div>
        `;
    } catch (e) {
        document.getElementById('promptVersions').innerHTML = `<div class="${tw.emptyState}">${t('prompts.failedToLoad', {error: escapeHtml(e.message)})}
            <br><button class="${tw.btnPrimary} ${tw.btnSm} mt-2" onclick="window._pages.prompts.loadPromptVersions()">${t('common.retry')}</button></div>`;
    }
}

function showPromptTab(tab, btn) {
    document.querySelectorAll('#page-prompts .tab-bar button').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    document.getElementById('promptVersions').style.display = tab === 'versions' ? 'block' : 'none';
    document.getElementById('promptABTests').style.display = tab === 'abtests' ? 'block' : 'none';
    if (tab === 'versions') loadPromptVersions();
    else loadABTests();
}

function showCreatePrompt() {
    document.getElementById('promptName').value = '';
    document.getElementById('promptSystemPrompt').value = '';
    document.getElementById('createPromptModal').classList.add('show');
}

async function createPrompt() {
    const name = document.getElementById('promptName').value.trim();
    const systemPrompt = document.getElementById('promptSystemPrompt').value.trim();
    if (!name || !systemPrompt) { showToast(t('prompts.nameRequired'), 'error'); return; }
    try {
        await api('/prompts', { method: 'POST', body: JSON.stringify({ name, system_prompt: systemPrompt }) });
        closeModal('createPromptModal');
        showToast(t('prompts.created_toast'));
        loadPromptVersions();
    } catch (e) { showToast(t('prompts.createFailed', {error: e.message}), 'error'); }
}

async function activatePrompt(id) {
    if (!confirm(t('prompts.activateConfirm'))) return;
    try {
        await api(`/prompts/${id}/activate`, { method: 'PATCH' });
        showToast(t('prompts.activated'));
        loadPromptVersions();
    } catch (e) { showToast(t('prompts.activateFailed', {error: e.message}), 'error'); }
}

async function loadABTests() {
    try {
        const data = await api('/prompts/ab-tests');
        const tests = data.tests || [];
        if (tests.length === 0) {
            document.getElementById('promptABTests').innerHTML = `<div class="${tw.emptyState}">${t('prompts.noABTests')}</div>`;
            return;
        }
        document.getElementById('promptABTests').innerHTML = `
            <div class="overflow-x-auto"><table class="${tw.table}"><thead><tr><th class="${tw.th}">${t('prompts.testName')}</th><th class="${tw.th}">${t('prompts.variantA')}</th><th class="${tw.th}">${t('prompts.variantB')}</th><th class="${tw.th}">${t('prompts.callsAB')}</th><th class="${tw.th}">${t('prompts.qualityAB')}</th><th class="${tw.th}">${t('prompts.abStatus')}</th><th class="${tw.th}">${t('prompts.abAction')}</th></tr></thead><tbody>
            ${tests.map(tc => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}">${escapeHtml(tc.test_name)}</td>
                    <td class="${tw.td}">${escapeHtml(tc.variant_a_name)}</td>
                    <td class="${tw.td}">${escapeHtml(tc.variant_b_name)}</td>
                    <td class="${tw.td}">${tc.calls_a}/${tc.calls_b}</td>
                    <td class="${tw.td}">${(tc.quality_a || 0).toFixed(2)}/${(tc.quality_b || 0).toFixed(2)}</td>
                    <td class="${tw.td}"><span class="${tc.status === 'active' ? tw.badgeBlue : tw.badgeGreen}">${escapeHtml(tc.status)}</span></td>
                    <td class="${tw.td}">${tc.status === 'active' ? `<button class="${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.prompts.stopABTest('${tc.id}')">${t('prompts.stopBtn')}</button>` : ''}</td>
                </tr>
            `).join('')}
            </tbody></table></div>
        `;
    } catch (e) {
        document.getElementById('promptABTests').innerHTML = `<div class="${tw.emptyState}">${t('prompts.failedToLoadAB', {error: escapeHtml(e.message)})}</div>`;
    }
}

async function stopABTest(id) {
    if (!confirm(t('prompts.stopConfirm'))) return;
    try {
        await api(`/prompts/ab-tests/${id}/stop`, { method: 'PATCH' });
        showToast(t('prompts.stopped'));
        loadABTests();
    } catch (e) { showToast(t('prompts.stopFailed', {error: e.message}), 'error'); }
}

export function init() {
    registerPageLoader('prompts', () => loadPromptVersions());
}

window._pages = window._pages || {};
window._pages.prompts = { loadPromptVersions, showPromptTab, showCreatePrompt, createPrompt, activatePrompt, loadABTests, stopABTest };
