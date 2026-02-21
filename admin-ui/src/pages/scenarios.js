import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { formatDate, escapeHtml, closeModal } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import { makeSortable } from '../sorting.js';
import { renderPagination, buildParams } from '../pagination.js';
import * as tw from '../tw.js';

// ─── State ───────────────────────────────────────────────────
let _activeTab = 'templates';
let _dialoguesOffset = 0;
let _safetyOffset = 0;
let _saving = false; // prevent double-submit on modal save buttons

// ─── Tab switching ───────────────────────────────────────────
function showTab(tab) {
    _activeTab = tab;
    const tabs = ['templates', 'dialogues', 'safety'];
    tabs.forEach(t => {
        const el = document.getElementById(`scenariosContent-${t}`);
        if (el) el.style.display = t === tab ? 'block' : 'none';
    });
    document.querySelectorAll('#page-scenarios .tab-bar button').forEach(b => b.classList.remove('active'));
    const activeBtn = document.querySelector(`#page-scenarios .tab-bar button[data-tab="${tab}"]`);
    if (activeBtn) activeBtn.classList.add('active');

    _dialoguesOffset = 0;
    _safetyOffset = 0;

    const loaders = { templates: loadTemplates, dialogues: loadDialogues, safety: loadSafetyRules };
    if (loaders[tab]) loaders[tab]();
}

// ═══════════════════════════════════════════════════════════
//  TAB 1: Шаблоны ответов (with variant support)
// ═══════════════════════════════════════════════════════════
async function loadTemplates() {
    const container = document.getElementById('templatesContainer') || document.getElementById('scenariosContent-templates');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;
    try {
        const data = await api('/training/templates/');
        let items = data.items || [];

        // Populate filter key select with unique keys
        const filterKeyEl = document.getElementById('templateFilterKey');
        if (filterKeyEl) {
            const currentVal = filterKeyEl.value;
            const uniqueKeys = [...new Set(items.map(i => i.template_key))].sort();
            filterKeyEl.innerHTML = `<option value="">${t('training.allTemplateKeys')}</option>`
                + uniqueKeys.map(k => `<option value="${escapeHtml(k)}"${k === currentVal ? ' selected' : ''}>${escapeHtml(k)}</option>`).join('');
        }

        // Client-side filtering
        const filterKey = document.getElementById('templateFilterKey')?.value || '';
        const searchText = (document.getElementById('templateSearch')?.value || '').toLowerCase().trim();
        if (filterKey) items = items.filter(i => i.template_key === filterKey);
        if (searchText) items = items.filter(i =>
            (i.title || '').toLowerCase().includes(searchText) ||
            (i.content || '').toLowerCase().includes(searchText) ||
            (i.template_key || '').toLowerCase().includes(searchText)
        );

        if (items.length === 0) {
            container.innerHTML = `
                <div class="mb-4"><button class="${tw.btnPrimary}" onclick="window._pages.scenarios.showCreateTemplate()">${t('training.newTemplate')}</button></div>
                <div class="${tw.emptyState}">${t('training.noTemplates')}</div>`;
            return;
        }

        // Group by template_key
        const grouped = {};
        for (const item of items) {
            if (!grouped[item.template_key]) grouped[item.template_key] = [];
            grouped[item.template_key].push(item);
        }

        let rows = '';
        for (const [key, variants] of Object.entries(grouped)) {
            for (let i = 0; i < variants.length; i++) {
                const item = variants[i];
                const isFirst = i === 0;
                const variantCount = variants.length;
                rows += `
                <tr class="${tw.trHover}">
                    ${isFirst ? `<td class="${tw.td}" data-label="${t('training.templateKey')}" rowspan="${variantCount}"><span class="${tw.badgeBlue}">${escapeHtml(key)}</span><br><span class="${tw.mutedText} text-xs">${variantCount} ${t('training.variantCount', {count: variantCount})}</span><br><button class="${tw.btnPrimary} ${tw.btnSm} mt-1" onclick="window._pages.scenarios.addVariant('${escapeHtml(key)}')">${t('training.addVariant')}</button></td>` : `<td class="${tw.td} mobile-only-cell" data-label="${t('training.templateKey')}"><span class="${tw.badgeBlue}">${escapeHtml(key)}</span></td>`}
                    <td class="${tw.td}" data-label="#"><span class="${tw.badge}">#${item.variant_number}</span></td>
                    <td class="${tw.td}" data-label="${t('training.templateTitle')}">${escapeHtml(item.title)}</td>
                    <td class="${tw.td}" data-label="${t('training.content')}"><span class="${tw.mutedText}">${escapeHtml((item.content || '').substring(0, 80))}${(item.content || '').length > 80 ? '...' : ''}</span></td>
                    <td class="${tw.td}" data-label="${t('training.activeCol')}">${item.is_active !== false ? `<span class="${tw.badgeGreen}">${t('common.yes')}</span>` : `<span class="${tw.badgeRed}">${t('common.no')}</span>`}</td>
                    <td class="${tw.tdActions}">
                        <div class="relative inline-block">
                            <button class="px-1.5 py-0.5 text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 text-sm cursor-pointer" onclick="this.nextElementSibling.classList.toggle('hidden')">&hellip;</button>
                            <div class="hidden absolute right-0 z-20 mt-1 w-36 bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-md shadow-lg py-1">
                                <button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" onclick="window._pages.scenarios.editTemplate('${item.id}')">${t('common.edit')}</button>
                                ${variantCount > 1 ? `<button class="w-full text-left px-3 py-1.5 text-xs text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/30 cursor-pointer" data-id="${escapeHtml(item.id)}" data-name="${escapeHtml(item.title)}" onclick="window._pages.scenarios.deleteTemplate(this.dataset.id, this.dataset.name)">${t('common.delete')}</button>` : ''}
                            </div>
                        </div>
                    </td>
                </tr>`;
            }
        }

        container.innerHTML = `
            <div class="mb-4">
                <button class="${tw.btnPrimary}" onclick="window._pages.scenarios.showCreateTemplate()">${t('training.newTemplate')}</button>
                <span class="${tw.mutedText} ml-3 text-sm">${t('training.variantsHint')}</span>
            </div>
            <div class="overflow-x-auto"><table class="${tw.table}"><thead><tr><th class="${tw.th}">${t('training.templateKey')}</th><th class="${tw.th}">#</th><th class="${tw.th}">${t('training.templateTitle')}</th><th class="${tw.th}">${t('training.content')}</th><th class="${tw.th}">${t('training.activeCol')}</th><th class="${tw.th}">${t('training.actions')}</th></tr></thead><tbody>
            ${rows}
            </tbody></table></div>`;
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('training.loadFailed', {error: escapeHtml(e.message)})}</div>`;
    }
}

function showCreateTemplate() {
    document.getElementById('templateModalTitle').textContent = t('training.newTemplate');
    document.getElementById('editTemplateId').value = '';
    document.getElementById('templateKey').value = '';
    document.getElementById('templateKey').disabled = false;
    document.getElementById('templateTitle').value = '';
    document.getElementById('templateContent').value = '';
    document.getElementById('templateDescription').value = '';
    document.getElementById('responseTemplateModal').classList.add('show');
}

function addVariant(templateKey) {
    document.getElementById('templateModalTitle').textContent = t('training.addVariantTitle');
    document.getElementById('editTemplateId').value = '';
    document.getElementById('templateKey').value = templateKey;
    document.getElementById('templateKey').disabled = true;
    document.getElementById('templateTitle').value = '';
    document.getElementById('templateContent').value = '';
    document.getElementById('templateDescription').value = '';
    document.getElementById('responseTemplateModal').classList.add('show');
}

async function editTemplate(id) {
    try {
        const data = await api(`/training/templates/${id}`);
        const item = data.item;
        document.getElementById('templateModalTitle').textContent = t('training.editTemplate');
        document.getElementById('editTemplateId').value = id;
        document.getElementById('templateKey').value = item.template_key || '';
        document.getElementById('templateKey').disabled = true;
        document.getElementById('templateTitle').value = item.title || '';
        document.getElementById('templateContent').value = item.content || '';
        document.getElementById('templateDescription').value = item.description || '';
        document.getElementById('responseTemplateModal').classList.add('show');
    } catch (e) { showToast(t('training.loadFailed', {error: e.message}), 'error'); }
}

async function saveTemplate() {
    if (_saving) return;
    const id = document.getElementById('editTemplateId').value;
    const templateKey = document.getElementById('templateKey').value.trim();
    const title = document.getElementById('templateTitle').value.trim();
    const content = document.getElementById('templateContent').value.trim();
    const description = document.getElementById('templateDescription').value.trim();
    if (!title || !content) { showToast(t('training.titleContentRequired'), 'error'); return; }
    _saving = true;
    try {
        if (id) {
            await api(`/training/templates/${id}`, { method: 'PATCH', body: JSON.stringify({ title, content, description: description || null }) });
        } else {
            if (!templateKey) { showToast(t('training.keyRequired'), 'error'); return; }
            await api('/training/templates/', { method: 'POST', body: JSON.stringify({ template_key: templateKey, title, content, description: description || null }) });
        }
        closeModal('responseTemplateModal');
        showToast(t('training.templateSaved'));
        loadTemplates();
    } catch (e) { showToast(t('training.saveFailed', {error: e.message}), 'error'); } finally { _saving = false; }
}

async function deleteTemplate(id, title) {
    if (!confirm(t('training.deleteVariantConfirm', {title}))) return;
    try {
        await api(`/training/templates/${id}`, { method: 'DELETE' });
        showToast(t('training.variantDeleted'));
        loadTemplates();
    } catch (e) { showToast(t('training.deleteFailed', {error: e.message}), 'error'); }
}

// ═══════════════════════════════════════════════════════════
//  TAB 2: Сценарии диалогов
// ═══════════════════════════════════════════════════════════
async function loadDialogues(offset) {
    if (offset !== undefined) _dialoguesOffset = offset;
    const container = document.getElementById('dialoguesContainer') || document.getElementById('scenariosContent-dialogues');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    const params = buildParams({
        offset: _dialoguesOffset,
        filters: { scenario_type: 'dialogueFilterScenario', phase: 'dialogueFilterPhase', is_active: 'dialogueFilterActive' },
    });

    try {
        const data = await api(`/training/dialogues/?${params}`);
        const items = data.items || [];
        if (items.length === 0) {
            container.innerHTML = `
                <div class="mb-4"><button class="${tw.btnPrimary}" onclick="window._pages.scenarios.showCreateDialogue()">${t('training.newDialogue')}</button></div>
                <div class="${tw.emptyState}">${t('training.noDialogues')}</div>`;
            renderPagination({ containerId: 'dialoguesPagination', total: 0, offset: 0 });
            return;
        }
        container.innerHTML = `
            <div class="mb-4"><button class="${tw.btnPrimary}" onclick="window._pages.scenarios.showCreateDialogue()">${t('training.newDialogue')}</button></div>
            <div class="overflow-x-auto"><table class="${tw.table}" id="dialoguesTable"><thead><tr><th class="${tw.thSortable}" data-sortable>${t('training.dialogueTitle')}</th><th class="${tw.thSortable}" data-sortable>${t('training.scenario')}</th><th class="${tw.thSortable}" data-sortable>${t('training.phase')}</th><th class="${tw.th}">${t('training.tools')}</th><th class="${tw.thSortable}" data-sortable>${t('training.activeCol')}</th><th class="${tw.th}">${t('training.actions')}</th></tr></thead><tbody>
            ${items.map(item => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}" data-label="${t('training.dialogueTitle')}">${escapeHtml(item.title)}</td>
                    <td class="${tw.td}" data-label="${t('training.scenario')}"><span class="${tw.badgeBlue}">${escapeHtml(item.scenario_type)}</span></td>
                    <td class="${tw.td}" data-label="${t('training.phase')}"><span class="${tw.badge}">${escapeHtml(item.phase)}</span></td>
                    <td class="${tw.td}" data-label="${t('training.tools')}">${(item.tools_used || []).map(t => `<span class="${tw.badgeGray}">${escapeHtml(t)}</span>`).join(' ')}</td>
                    <td class="${tw.td}" data-label="${t('training.activeCol')}">${item.is_active !== false ? `<span class="${tw.badgeGreen}">${t('common.yes')}</span>` : `<span class="${tw.badgeRed}">${t('common.no')}</span>`}</td>
                    <td class="${tw.tdActions}">
                        <div class="relative inline-block">
                            <button class="px-1.5 py-0.5 text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 text-sm cursor-pointer" onclick="this.nextElementSibling.classList.toggle('hidden')">&hellip;</button>
                            <div class="hidden absolute right-0 z-20 mt-1 w-36 bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-md shadow-lg py-1">
                                <button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" onclick="window._pages.scenarios.editDialogue('${item.id}')">${t('common.edit')}</button>
                                <button class="w-full text-left px-3 py-1.5 text-xs text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/30 cursor-pointer" data-id="${escapeHtml(item.id)}" data-name="${escapeHtml(item.title)}" onclick="window._pages.scenarios.deleteDialogue(this.dataset.id, this.dataset.name)">${t('common.delete')}</button>
                            </div>
                        </div>
                    </td>
                </tr>
            `).join('')}
            </tbody></table></div>`;

        makeSortable('dialoguesTable');
        renderPagination({
            containerId: 'dialoguesPagination',
            total: data.total,
            offset: _dialoguesOffset,
            onPage: (newOffset) => loadDialogues(newOffset),
        });
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('training.loadFailed', {error: escapeHtml(e.message)})}</div>`;
    }
}

function showCreateDialogue() {
    document.getElementById('dialogueModalTitle').textContent = t('training.newDialogue');
    document.getElementById('editDialogueId').value = '';
    document.getElementById('dialogueTitle').value = '';
    document.getElementById('dialogueScenarioType').value = 'tire_search';
    document.getElementById('dialoguePhase').value = 'mvp';
    document.getElementById('dialogueJSON').value = '[\n  {"role": "customer", "text": ""},\n  {"role": "agent", "text": ""}\n]';
    document.getElementById('dialogueDescription').value = '';
    document.getElementById('dialogueModal').classList.add('show');
}

async function editDialogue(id) {
    try {
        const data = await api(`/training/dialogues/${id}`);
        const item = data.item;
        document.getElementById('dialogueModalTitle').textContent = t('training.editDialogue');
        document.getElementById('editDialogueId').value = id;
        document.getElementById('dialogueTitle').value = item.title || '';
        document.getElementById('dialogueScenarioType').value = item.scenario_type || 'tire_search';
        document.getElementById('dialoguePhase').value = item.phase || 'mvp';
        document.getElementById('dialogueJSON').value = JSON.stringify(item.dialogue || [], null, 2);
        document.getElementById('dialogueDescription').value = item.description || '';
        document.getElementById('dialogueModal').classList.add('show');
    } catch (e) { showToast(t('training.loadFailed', {error: e.message}), 'error'); }
}

async function saveDialogue() {
    if (_saving) return;
    const id = document.getElementById('editDialogueId').value;
    const title = document.getElementById('dialogueTitle').value.trim();
    const scenarioType = document.getElementById('dialogueScenarioType').value;
    const phase = document.getElementById('dialoguePhase').value;
    const description = document.getElementById('dialogueDescription').value.trim();
    let dialogue;
    try { dialogue = JSON.parse(document.getElementById('dialogueJSON').value); } catch { showToast(t('training.invalidJSON'), 'error'); return; }
    if (!title) { showToast(t('training.titleRequired'), 'error'); return; }

    const toolsUsed = [...new Set(dialogue.filter(d => d.tool_calls).flatMap(d => d.tool_calls.map(tc => tc.name)))];
    const body = { title, scenario_type: scenarioType, phase, dialogue, tools_used: toolsUsed, description: description || null };

    _saving = true;
    try {
        if (id) {
            await api(`/training/dialogues/${id}`, { method: 'PATCH', body: JSON.stringify(body) });
        } else {
            await api('/training/dialogues/', { method: 'POST', body: JSON.stringify(body) });
        }
        closeModal('dialogueModal');
        showToast(t('training.dialogueSaved'));
        loadDialogues(_dialoguesOffset);
    } catch (e) { showToast(t('training.saveFailed', {error: e.message}), 'error'); } finally { _saving = false; }
}

async function deleteDialogue(id, title) {
    if (!confirm(t('training.deleteConfirm', {title}))) return;
    try {
        await api(`/training/dialogues/${id}`, { method: 'DELETE' });
        showToast(t('training.dialogueDeleted'));
        loadDialogues(_dialoguesOffset);
    } catch (e) { showToast(t('training.deleteFailed', {error: e.message}), 'error'); }
}

// ═══════════════════════════════════════════════════════════
//  TAB 3: Правила безопасности
// ═══════════════════════════════════════════════════════════
const SEVERITY_WEIGHT = { critical: 4, high: 3, medium: 2, low: 1 };

function severityBadge(sev) {
    switch (sev) {
        case 'critical': return `<span class="${tw.badgeRed}">${escapeHtml(sev)}</span>`;
        case 'high': return `<span class="${tw.badgeYellow}">${escapeHtml(sev)}</span>`;
        case 'medium': return `<span class="${tw.badgeBlue}">${escapeHtml(sev)}</span>`;
        default: return `<span class="${tw.badge}">${escapeHtml(sev)}</span>`;
    }
}

async function loadSafetyRules(offset) {
    if (offset !== undefined) _safetyOffset = offset;
    const container = document.getElementById('safetyRulesContainer') || document.getElementById('scenariosContent-safety');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    const params = buildParams({
        offset: _safetyOffset,
        filters: { rule_type: 'safetyFilterType', severity: 'safetyFilterSeverity', is_active: 'safetyFilterActive' },
    });

    try {
        const data = await api(`/training/safety-rules/?${params}`);
        const items = data.items || [];
        if (items.length === 0) {
            container.innerHTML = `
                <div class="mb-4"><button class="${tw.btnPrimary}" onclick="window._pages.scenarios.showCreateSafetyRule()">${t('training.newSafetyRule')}</button></div>
                <div class="${tw.emptyState}">${t('training.noSafetyRules')}</div>`;
            renderPagination({ containerId: 'safetyPagination', total: 0, offset: 0 });
            return;
        }
        container.innerHTML = `
            <div class="mb-4">
                <button class="${tw.btnPrimary}" onclick="window._pages.scenarios.showCreateSafetyRule()">${t('training.newSafetyRule')}</button>
                <button class="${tw.btnSecondary} ml-2" id="regressionTestBtn" onclick="window._pages.scenarios.runSafetyRegressionTest()">${t('training.regressionTest')}</button>
            </div>
            <div class="overflow-x-auto"><table class="${tw.table}" id="safetyRulesTable"><thead><tr><th class="${tw.thSortable}" data-sortable>${t('training.ruleTitle')}</th><th class="${tw.thSortable}" data-sortable>${t('training.ruleType')}</th><th class="${tw.thSortable}" data-sortable>${t('training.severity')}</th><th class="${tw.th}">${t('training.triggerInput')}</th><th class="${tw.thSortable}" data-sortable>${t('training.activeCol')}</th><th class="${tw.th}">${t('training.actions')}</th></tr></thead><tbody>
            ${items.map(item => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}" data-label="${t('training.ruleTitle')}">${escapeHtml(item.title)}</td>
                    <td class="${tw.td}" data-label="${t('training.ruleType')}"><span class="${tw.badgeBlue}">${escapeHtml(item.rule_type)}</span></td>
                    <td class="${tw.td}" data-label="${t('training.severity')}" data-sort-value="${SEVERITY_WEIGHT[item.severity] || 0}">${severityBadge(item.severity)}</td>
                    <td class="${tw.td}" data-label="${t('training.triggerInput')}"><span class="${tw.mutedText}">${escapeHtml((item.trigger_input || '').substring(0, 60))}${(item.trigger_input || '').length > 60 ? '...' : ''}</span></td>
                    <td class="${tw.td}" data-label="${t('training.activeCol')}">${item.is_active !== false ? `<span class="${tw.badgeGreen}">${t('common.yes')}</span>` : `<span class="${tw.badgeRed}">${t('common.no')}</span>`}</td>
                    <td class="${tw.tdActions}">
                        <div class="relative inline-block">
                            <button class="px-1.5 py-0.5 text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 text-sm cursor-pointer" onclick="this.nextElementSibling.classList.toggle('hidden')">&hellip;</button>
                            <div class="hidden absolute right-0 z-20 mt-1 w-36 bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-md shadow-lg py-1">
                                <button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" onclick="window._pages.scenarios.editSafetyRule('${item.id}')">${t('common.edit')}</button>
                                <button class="w-full text-left px-3 py-1.5 text-xs text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/30 cursor-pointer" data-id="${escapeHtml(item.id)}" data-name="${escapeHtml(item.title)}" onclick="window._pages.scenarios.deleteSafetyRule(this.dataset.id, this.dataset.name)">${t('common.delete')}</button>
                            </div>
                        </div>
                    </td>
                </tr>
            `).join('')}
            </tbody></table></div>`;

        makeSortable('safetyRulesTable');
        renderPagination({
            containerId: 'safetyPagination',
            total: data.total,
            offset: _safetyOffset,
            onPage: (newOffset) => loadSafetyRules(newOffset),
        });
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('training.loadFailed', {error: escapeHtml(e.message)})}</div>`;
    }
}

function showCreateSafetyRule() {
    document.getElementById('safetyRuleModalTitle').textContent = t('training.newSafetyRule');
    document.getElementById('editSafetyRuleId').value = '';
    document.getElementById('safetyRuleTitle').value = '';
    document.getElementById('safetyRuleType').value = 'behavioral';
    document.getElementById('safetyRuleSeverity').value = 'medium';
    document.getElementById('safetyTriggerInput').value = '';
    document.getElementById('safetyExpectedBehavior').value = '';
    document.getElementById('safetyRuleModal').classList.add('show');
}

async function editSafetyRule(id) {
    try {
        const data = await api(`/training/safety-rules/${id}`);
        const item = data.item;
        document.getElementById('safetyRuleModalTitle').textContent = t('training.editSafetyRule');
        document.getElementById('editSafetyRuleId').value = id;
        document.getElementById('safetyRuleTitle').value = item.title || '';
        document.getElementById('safetyRuleType').value = item.rule_type || 'behavioral';
        document.getElementById('safetyRuleSeverity').value = item.severity || 'medium';
        document.getElementById('safetyTriggerInput').value = item.trigger_input || '';
        document.getElementById('safetyExpectedBehavior').value = item.expected_behavior || '';
        document.getElementById('safetyRuleModal').classList.add('show');
    } catch (e) { showToast(t('training.loadFailed', {error: e.message}), 'error'); }
}

async function saveSafetyRule() {
    if (_saving) return;
    const id = document.getElementById('editSafetyRuleId').value;
    const title = document.getElementById('safetyRuleTitle').value.trim();
    const ruleType = document.getElementById('safetyRuleType').value;
    const severity = document.getElementById('safetyRuleSeverity').value;
    const triggerInput = document.getElementById('safetyTriggerInput').value.trim();
    const expectedBehavior = document.getElementById('safetyExpectedBehavior').value.trim();
    if (!title || !triggerInput || !expectedBehavior) { showToast(t('training.fieldsRequired'), 'error'); return; }

    const body = { title, rule_type: ruleType, severity, trigger_input: triggerInput, expected_behavior: expectedBehavior };
    _saving = true;
    try {
        if (id) {
            await api(`/training/safety-rules/${id}`, { method: 'PATCH', body: JSON.stringify(body) });
        } else {
            await api('/training/safety-rules/', { method: 'POST', body: JSON.stringify(body) });
        }
        closeModal('safetyRuleModal');
        showToast(t('training.safetyRuleSaved'));
        loadSafetyRules(_safetyOffset);
    } catch (e) { showToast(t('training.saveFailed', {error: e.message}), 'error'); } finally { _saving = false; }
}

async function deleteSafetyRule(id, title) {
    if (!confirm(t('training.deleteConfirm', {title}))) return;
    try {
        await api(`/training/safety-rules/${id}`, { method: 'DELETE' });
        showToast(t('training.safetyRuleDeleted'));
        loadSafetyRules(_safetyOffset);
    } catch (e) { showToast(t('training.deleteFailed', {error: e.message}), 'error'); }
}

// ═══════════════════════════════════════════════════════════
//  Safety rules — regression test
// ═══════════════════════════════════════════════════════════
async function runSafetyRegressionTest() {
    const btn = document.getElementById('regressionTestBtn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = t('training.regressionRunning');
    }

    try {
        const data = await api('/training/safety-rules/regression-test', { method: 'POST' });
        showRegressionResults(data);
    } catch (e) {
        showToast(t('training.regressionFailed', { error: e.message }), 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = t('training.regressionTest');
        }
    }
}

function showRegressionResults(data) {
    const results = data.results || [];
    const rows = results.map(r => `
        <tr class="${tw.trHover}">
            <td class="${tw.td}">
                ${r.passed
                    ? `<span class="${tw.badgeGreen}">${t('training.regressionPassed')}</span>`
                    : `<span class="${tw.badgeRed}">${t('training.regressionFailed_label')}</span>`}
            </td>
            <td class="${tw.td}">${escapeHtml(r.title)}</td>
            <td class="${tw.td}">${severityBadge(r.severity)}</td>
            <td class="${tw.td}"><span class="${tw.mutedText}">${escapeHtml((r.trigger_input || '').substring(0, 80))}</span></td>
            <td class="${tw.td}"><span class="${tw.mutedText}">${escapeHtml((r.expected || '').substring(0, 80))}</span></td>
            <td class="${tw.td}"><span class="${tw.mutedText}">${escapeHtml((r.actual || '').substring(0, 120))}</span></td>
            <td class="${tw.td}"><span class="${tw.mutedText}">${escapeHtml((r.reason || '').substring(0, 80))}</span></td>
        </tr>
    `).join('');

    const modal = document.getElementById('regressionResultsModal');
    if (!modal) {
        // Create modal dynamically
        const div = document.createElement('div');
        div.id = 'regressionResultsModal';
        div.className = 'modal';
        div.innerHTML = `
            <div class="modal-content" style="max-width:900px">
                <div class="modal-header">
                    <h3>${t('training.regressionResults')}</h3>
                    <button class="modal-close" onclick="window._pages.scenarios.closeRegressionModal()">&times;</button>
                </div>
                <div class="modal-body" id="regressionResultsBody"></div>
            </div>`;
        document.body.appendChild(div);
    }

    const body = document.getElementById('regressionResultsBody');
    body.innerHTML = `
        <div class="mb-4">
            <span class="${tw.badgeGreen} mr-2">${t('training.regressionPassedCount', { count: data.passed })}</span>
            <span class="${tw.badgeRed}">${t('training.regressionFailedCount', { count: data.failed })}</span>
            <span class="${tw.mutedText} ml-2">${t('training.regressionTotal', { count: data.total })}</span>
        </div>
        <div class="overflow-x-auto"><table class="${tw.table}"><thead><tr>
            <th class="${tw.th}">${t('training.regressionStatus')}</th>
            <th class="${tw.th}">${t('training.ruleTitle')}</th>
            <th class="${tw.th}">${t('training.severity')}</th>
            <th class="${tw.th}">${t('training.triggerInput')}</th>
            <th class="${tw.th}">${t('training.expectedBehavior')}</th>
            <th class="${tw.th}">${t('training.regressionActual')}</th>
            <th class="${tw.th}">${t('training.regressionReason')}</th>
        </tr></thead><tbody>${rows}</tbody></table></div>`;

    document.getElementById('regressionResultsModal').classList.add('show');
}

function closeRegressionModal() {
    closeModal('regressionResultsModal');
}

// ─── Init ────────────────────────────────────────────────────
export function init() {
    registerPageLoader('scenarios', () => showTab(_activeTab));
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.relative')) {
            document.querySelectorAll('#page-scenarios .relative > div:not(.hidden)').forEach(m => m.classList.add('hidden'));
        }
    });
}

window._pages = window._pages || {};
window._pages.scenarios = {
    showTab,
    loadTemplates, showCreateTemplate, addVariant, editTemplate, saveTemplate, deleteTemplate,
    loadDialogues, showCreateDialogue, editDialogue, saveDialogue, deleteDialogue,
    loadSafetyRules, showCreateSafetyRule, editSafetyRule, saveSafetyRule, deleteSafetyRule,
    runSafetyRegressionTest, closeRegressionModal,
};
