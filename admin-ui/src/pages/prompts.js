import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { formatDate, escapeHtml, closeModal } from '../utils.js';
import { registerPageLoader } from '../router.js';

async function loadPromptVersions() {
    try {
        const data = await api('/prompts');
        const versions = data.versions || [];
        if (versions.length === 0) {
            document.getElementById('promptVersions').innerHTML = `
                <div style="margin-bottom:1rem"><button class="btn btn-primary" onclick="window._pages.prompts.showCreatePrompt()">+ New Version</button></div>
                <div class="empty-state">No prompt versions found</div>`;
            return;
        }
        document.getElementById('promptVersions').innerHTML = `
            <div style="margin-bottom:1rem"><button class="btn btn-primary" onclick="window._pages.prompts.showCreatePrompt()">+ New Version</button></div>
            <table><thead><tr><th>Name</th><th>Active</th><th>Created</th><th>Action</th></tr></thead><tbody>
            ${versions.map(v => `
                <tr>
                    <td>${escapeHtml(v.name)}</td>
                    <td>${v.is_active ? '<span class="badge badge-green">Active</span>' : ''}</td>
                    <td>${formatDate(v.created_at)}</td>
                    <td>${!v.is_active ? `<button class="btn btn-primary btn-sm" onclick="window._pages.prompts.activatePrompt('${v.id}')">Activate</button>` : ''}</td>
                </tr>
            `).join('')}
            </tbody></table>
        `;
    } catch (e) {
        document.getElementById('promptVersions').innerHTML = `<div class="empty-state">Failed to load prompts: ${escapeHtml(e.message)}
            <br><button class="btn btn-primary btn-sm" onclick="window._pages.prompts.loadPromptVersions()" style="margin-top:.5rem">Retry</button></div>`;
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
    if (!name || !systemPrompt) { showToast('Name and system prompt are required', 'error'); return; }
    try {
        await api('/prompts', { method: 'POST', body: JSON.stringify({ name, system_prompt: systemPrompt }) });
        closeModal('createPromptModal');
        showToast('Prompt version created');
        loadPromptVersions();
    } catch (e) { showToast('Failed to create prompt: ' + e.message, 'error'); }
}

async function activatePrompt(id) {
    if (!confirm('Activate this prompt version? All calls will use it.')) return;
    try {
        await api(`/prompts/${id}/activate`, { method: 'PATCH' });
        showToast('Prompt version activated');
        loadPromptVersions();
    } catch (e) { showToast('Failed to activate prompt: ' + e.message, 'error'); }
}

async function loadABTests() {
    try {
        const data = await api('/prompts/ab-tests');
        const tests = data.tests || [];
        if (tests.length === 0) {
            document.getElementById('promptABTests').innerHTML = '<div class="empty-state">No A/B tests found</div>';
            return;
        }
        document.getElementById('promptABTests').innerHTML = `
            <table><thead><tr><th>Test</th><th>Variant A</th><th>Variant B</th><th>Calls A/B</th><th>Quality A/B</th><th>Status</th><th>Action</th></tr></thead><tbody>
            ${tests.map(t => `
                <tr>
                    <td>${escapeHtml(t.test_name)}</td>
                    <td>${escapeHtml(t.variant_a_name)}</td>
                    <td>${escapeHtml(t.variant_b_name)}</td>
                    <td>${t.calls_a}/${t.calls_b}</td>
                    <td>${(t.quality_a || 0).toFixed(2)}/${(t.quality_b || 0).toFixed(2)}</td>
                    <td><span class="badge ${t.status === 'active' ? 'badge-blue' : 'badge-green'}">${escapeHtml(t.status)}</span></td>
                    <td>${t.status === 'active' ? `<button class="btn btn-danger btn-sm" onclick="window._pages.prompts.stopABTest('${t.id}')">Stop</button>` : ''}</td>
                </tr>
            `).join('')}
            </tbody></table>
        `;
    } catch (e) {
        document.getElementById('promptABTests').innerHTML = `<div class="empty-state">Failed to load A/B tests: ${escapeHtml(e.message)}</div>`;
    }
}

async function stopABTest(id) {
    if (!confirm('Stop this A/B test?')) return;
    try {
        await api(`/prompts/ab-tests/${id}/stop`, { method: 'PATCH' });
        showToast('A/B test stopped');
        loadABTests();
    } catch (e) { showToast('Failed to stop test: ' + e.message, 'error'); }
}

export function init() {
    registerPageLoader('prompts', () => loadPromptVersions());
}

window._pages = window._pages || {};
window._pages.prompts = { loadPromptVersions, showPromptTab, showCreatePrompt, createPrompt, activatePrompt, loadABTests, stopABTest };
