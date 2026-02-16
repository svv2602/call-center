import { api, apiUpload } from '../api.js';
import { showToast } from '../notifications.js';
import { formatDate, escapeHtml, closeModal } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import * as tw from '../tw.js';

// ─── State ───────────────────────────────────────────────────
let _categories = [];
let _activeTab = 'prompts';

// ─── Tab switching ───────────────────────────────────────────
function showTab(tab) {
    _activeTab = tab;
    const tabs = ['prompts', 'knowledge', 'templates', 'dialogues', 'safety', 'tools'];
    tabs.forEach(t => {
        const el = document.getElementById(`trainingContent-${t}`);
        if (el) el.style.display = t === tab ? 'block' : 'none';
    });
    document.querySelectorAll('#page-training .tab-bar button').forEach(b => b.classList.remove('active'));
    const activeBtn = document.querySelector(`#page-training .tab-bar button[data-tab="${tab}"]`);
    if (activeBtn) activeBtn.classList.add('active');

    // Load data for the tab
    const loaders = { prompts: loadPromptVersions, knowledge: loadKnowledge, templates: loadTemplates, dialogues: loadDialogues, safety: loadSafetyRules, tools: loadTools };
    if (loaders[tab]) loaders[tab]();
}

// ═══════════════════════════════════════════════════════════
//  TAB 1: Промпты (from prompts.js)
// ═══════════════════════════════════════════════════════════
async function loadPromptVersions() {
    const container = document.getElementById('trainingContent-prompts');
    try {
        const data = await api('/prompts');
        const versions = data.versions || [];
        if (versions.length === 0) {
            container.innerHTML = `
                <div class="mb-4"><button class="${tw.btnPrimary}" onclick="window._pages.training.showCreatePrompt()">${t('prompts.newVersion')}</button></div>
                <div class="${tw.emptyState}">${t('prompts.noVersions')}</div>`;
            return;
        }
        container.innerHTML = `
            <div class="mb-4"><button class="${tw.btnPrimary}" onclick="window._pages.training.showCreatePrompt()">${t('prompts.newVersion')}</button></div>
            <div class="overflow-x-auto"><table class="${tw.table}"><thead><tr><th class="${tw.th}">${t('prompts.name')}</th><th class="${tw.th}">${t('prompts.active')}</th><th class="${tw.th}">${t('prompts.created')}</th><th class="${tw.th}">${t('prompts.action')}</th></tr></thead><tbody>
            ${versions.map(v => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}">${escapeHtml(v.name)}</td>
                    <td class="${tw.td}">${v.is_active ? `<span class="${tw.badgeGreen}">${t('prompts.activeLabel')}</span>` : ''}</td>
                    <td class="${tw.td}">${formatDate(v.created_at)}</td>
                    <td class="${tw.td}">${!v.is_active ? `<button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.training.activatePrompt('${v.id}')">${t('prompts.activateBtn')}</button>` : ''}</td>
                </tr>
            `).join('')}
            </tbody></table></div>`;
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('prompts.failedToLoad', {error: escapeHtml(e.message)})}
            <br><button class="${tw.btnPrimary} ${tw.btnSm} mt-2" onclick="window._pages.training.loadPromptVersions()">${t('common.retry')}</button></div>`;
    }
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

// ═══════════════════════════════════════════════════════════
//  TAB 2: База знаний (from knowledge.js)
// ═══════════════════════════════════════════════════════════
function embeddingBadge(status) {
    switch (status) {
        case 'indexed': return `<span class="${tw.badgeGreen}">indexed</span>`;
        case 'pending': return `<span class="${tw.badgeYellow}">pending</span>`;
        case 'processing': return `<span class="${tw.badgeBlue}">processing</span>`;
        case 'error': return `<span class="${tw.badgeRed}">error</span>`;
        default: return `<span class="${tw.badge}">${escapeHtml(status || 'unknown')}</span>`;
    }
}

function populateCategorySelect(selectEl, categories, includeAll) {
    selectEl.innerHTML = '';
    if (includeAll) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = t('knowledge.allCategories');
        selectEl.appendChild(opt);
    }
    for (const cat of categories) {
        const opt = document.createElement('option');
        opt.value = cat.value;
        opt.textContent = cat.label;
        selectEl.appendChild(opt);
    }
}

async function loadCategories() {
    try {
        const data = await api('/knowledge/article-categories');
        _categories = data.categories || [];
    } catch {
        _categories = [
            { value: 'brands', label: 'Brands' },
            { value: 'guides', label: 'Guides' },
            { value: 'faq', label: 'FAQ' },
            { value: 'comparisons', label: 'Comparisons' },
            { value: 'general', label: 'General' },
        ];
    }
    const filterSel = document.getElementById('kbCategory');
    if (filterSel) populateCategorySelect(filterSel, _categories, true);
    const articleSel = document.getElementById('articleCategory');
    if (articleSel) populateCategorySelect(articleSel, _categories, false);
    const importSel = document.getElementById('importCategory');
    if (importSel) populateCategorySelect(importSel, _categories, true);
}

async function loadKnowledge() {
    await loadCategories();
    await loadArticles();
}

async function loadArticles() {
    const container = document.getElementById('articlesContainer');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    const params = new URLSearchParams();
    const cat = document.getElementById('kbCategory')?.value;
    const search = document.getElementById('kbSearch')?.value;
    if (cat) params.set('category', cat);
    if (search) params.set('search', search);

    try {
        const data = await api(`/knowledge/articles?${params}`);
        const articles = data.articles || [];
        if (articles.length === 0) {
            container.innerHTML = `<div class="${tw.emptyState}">${t('knowledge.noArticles')}</div>`;
            return;
        }
        container.innerHTML = `
            <div class="overflow-x-auto"><table class="${tw.table}"><thead><tr><th class="${tw.th}">${t('knowledge.articleTitle')}</th><th class="${tw.th}">${t('knowledge.category')}</th><th class="${tw.th}">${t('knowledge.embedding')}</th><th class="${tw.th}">${t('knowledge.activeCol')}</th><th class="${tw.th}">${t('knowledge.updated')}</th><th class="${tw.th}">${t('knowledge.actions')}</th></tr></thead><tbody>
            ${articles.map(a => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}">${escapeHtml(a.title)}</td>
                    <td class="${tw.td}"><span class="${tw.badgeBlue}">${escapeHtml(a.category)}</span></td>
                    <td class="${tw.td}">${embeddingBadge(a.embedding_status)}</td>
                    <td class="${tw.td}">${a.active !== false ? `<span class="${tw.badgeGreen}">${t('common.yes')}</span>` : `<span class="${tw.badgeRed}">${t('common.no')}</span>`}</td>
                    <td class="${tw.td}">${formatDate(a.updated_at || a.created_at)}</td>
                    <td class="${tw.td}">
                        <div class="flex flex-wrap gap-1">
                            <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.training.editArticle('${a.id}')">${t('knowledge.editBtn')}</button>
                            <button class="${tw.btnGreen} ${tw.btnSm}" onclick="window._pages.training.reindexArticle('${a.id}')">${t('knowledge.reindexBtn')}</button>
                            <button class="${tw.btnSecondary} ${tw.btnSm}" onclick="window._pages.training.toggleArticle('${a.id}', ${a.active !== false})">${a.active !== false ? t('common.deactivate') : t('common.activate')}</button>
                            <button class="${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.training.deleteArticle('${a.id}', '${escapeHtml(a.title).replace(/'/g, "\\'")}')">×</button>
                        </div>
                    </td>
                </tr>
            `).join('')}
            </tbody></table></div>
            <p class="${tw.mutedText} mt-2">${t('knowledge.total', {count: data.total})}</p>`;
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('knowledge.failedToLoad', {error: escapeHtml(e.message)})}
            <br><button class="${tw.btnPrimary} ${tw.btnSm} mt-2" onclick="window._pages.training.loadArticles()">${t('common.retry')}</button></div>`;
    }
}

function showCreateArticle() {
    document.getElementById('articleModalTitle').textContent = t('knowledge.newArticleTitle');
    document.getElementById('editArticleId').value = '';
    document.getElementById('articleTitle').value = '';
    document.getElementById('articleCategory').value = 'faq';
    document.getElementById('articleContent').value = '';
    document.getElementById('createArticleModal').classList.add('show');
}

async function editArticle(id) {
    try {
        const data = await api(`/knowledge/articles/${id}`);
        const a = data.article;
        document.getElementById('articleModalTitle').textContent = t('knowledge.editArticleTitle');
        document.getElementById('editArticleId').value = id;
        document.getElementById('articleTitle').value = a.title || '';
        document.getElementById('articleCategory').value = a.category || 'faq';
        document.getElementById('articleContent').value = a.content || '';
        document.getElementById('createArticleModal').classList.add('show');
    } catch (e) { showToast(t('knowledge.loadFailed', {error: e.message}), 'error'); }
}

async function saveArticle() {
    const id = document.getElementById('editArticleId').value;
    const title = document.getElementById('articleTitle').value.trim();
    const category = document.getElementById('articleCategory').value;
    const content = document.getElementById('articleContent').value.trim();
    if (!title || !content) { showToast(t('knowledge.titleRequired'), 'error'); return; }
    try {
        if (id) {
            await api(`/knowledge/articles/${id}`, { method: 'PATCH', body: JSON.stringify({ title, category, content }) });
            showToast(t('knowledge.articleUpdated'));
        } else {
            await api('/knowledge/articles', { method: 'POST', body: JSON.stringify({ title, category, content }) });
            showToast(t('knowledge.articleCreated'));
        }
        closeModal('createArticleModal');
        loadArticles();
    } catch (e) { showToast(t('knowledge.saveFailed', {error: e.message}), 'error'); }
}

async function toggleArticle(id, currentlyActive) {
    try {
        await api(`/knowledge/articles/${id}`, { method: 'PATCH', body: JSON.stringify({ active: !currentlyActive }) });
        showToast(currentlyActive ? t('knowledge.articleDeactivated') : t('knowledge.articleActivated'));
        loadArticles();
    } catch (e) { showToast(t('knowledge.toggleFailed', {error: e.message}), 'error'); }
}

async function deleteArticle(id, title) {
    if (!confirm(t('knowledge.deleteConfirm', {title}))) return;
    try {
        await api(`/knowledge/articles/${id}`, { method: 'DELETE' });
        showToast(t('knowledge.articleDeleted'));
        loadArticles();
    } catch (e) { showToast(t('knowledge.deleteFailed', {error: e.message}), 'error'); }
}

async function reindexArticle(id) {
    try {
        const data = await api(`/knowledge/articles/${id}/reindex`, { method: 'POST' });
        showToast(data.message || t('knowledge.reindexQueued'));
        loadArticles();
    } catch (e) { showToast(t('knowledge.reindexFailed', {error: e.message}), 'error'); }
}

async function reindexAll() {
    if (!confirm(t('knowledge.reindexAllConfirm'))) return;
    try {
        const data = await api('/knowledge/reindex-all', { method: 'POST' });
        showToast(data.message || t('knowledge.reindexAllDispatched'));
        loadArticles();
    } catch (e) { showToast(t('knowledge.reindexAllFailed', {error: e.message}), 'error'); }
}

function showImportModal() {
    document.getElementById('importFiles').value = '';
    const importSel = document.getElementById('importCategory');
    if (importSel) importSel.value = '';
    document.getElementById('importDocumentsModal').classList.add('show');
}

async function importDocuments() {
    const fileInput = document.getElementById('importFiles');
    const categorySelect = document.getElementById('importCategory');
    const files = fileInput.files;
    if (!files || files.length === 0) { showToast(t('knowledge.importNoFiles'), 'error'); return; }

    const formData = new FormData();
    for (const file of files) formData.append('files', file);

    let url = '/knowledge/articles/import';
    const cat = categorySelect ? categorySelect.value : '';
    if (cat) url += `?category=${encodeURIComponent(cat)}`;

    try {
        const data = await apiUpload(url, formData);
        const msgs = [t('knowledge.importResult', {imported: data.imported})];
        if (data.errors > 0) msgs.push(t('knowledge.importErrors', {errors: data.errors}));
        showToast(msgs.join(', '), data.errors > 0 ? 'warning' : 'success');
        if (data.error_details && data.error_details.length > 0) {
            for (const err of data.error_details) showToast(`${err.filename}: ${err.error}`, 'error');
        }
        closeModal('importDocumentsModal');
        loadArticles();
    } catch (e) { showToast(t('knowledge.importFailed', {error: e.message}), 'error'); }
}

// ═══════════════════════════════════════════════════════════
//  TAB 3: Шаблоны ответов
// ═══════════════════════════════════════════════════════════
async function loadTemplates() {
    const container = document.getElementById('trainingContent-templates');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;
    try {
        const data = await api('/training/templates');
        const items = data.items || [];
        if (items.length === 0) {
            container.innerHTML = `
                <div class="mb-4"><button class="${tw.btnPrimary}" onclick="window._pages.training.showCreateTemplate()">${t('training.newTemplate')}</button></div>
                <div class="${tw.emptyState}">${t('training.noTemplates')}</div>`;
            return;
        }
        container.innerHTML = `
            <div class="mb-4"><button class="${tw.btnPrimary}" onclick="window._pages.training.showCreateTemplate()">${t('training.newTemplate')}</button></div>
            <div class="overflow-x-auto"><table class="${tw.table}"><thead><tr><th class="${tw.th}">${t('training.templateKey')}</th><th class="${tw.th}">${t('training.templateTitle')}</th><th class="${tw.th}">${t('training.content')}</th><th class="${tw.th}">${t('training.activeCol')}</th><th class="${tw.th}">${t('training.actions')}</th></tr></thead><tbody>
            ${items.map(item => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}"><span class="${tw.badgeBlue}">${escapeHtml(item.template_key)}</span></td>
                    <td class="${tw.td}">${escapeHtml(item.title)}</td>
                    <td class="${tw.td}"><span class="${tw.mutedText}">${escapeHtml((item.content || '').substring(0, 80))}${(item.content || '').length > 80 ? '...' : ''}</span></td>
                    <td class="${tw.td}">${item.is_active !== false ? `<span class="${tw.badgeGreen}">${t('common.yes')}</span>` : `<span class="${tw.badgeRed}">${t('common.no')}</span>`}</td>
                    <td class="${tw.td}"><button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.training.editTemplate('${item.id}')">${t('common.edit')}</button></td>
                </tr>
            `).join('')}
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
    const id = document.getElementById('editTemplateId').value;
    const templateKey = document.getElementById('templateKey').value.trim();
    const title = document.getElementById('templateTitle').value.trim();
    const content = document.getElementById('templateContent').value.trim();
    const description = document.getElementById('templateDescription').value.trim();
    if (!title || !content) { showToast(t('training.titleContentRequired'), 'error'); return; }
    try {
        if (id) {
            await api(`/training/templates/${id}`, { method: 'PATCH', body: JSON.stringify({ title, content, description: description || null }) });
        } else {
            if (!templateKey) { showToast(t('training.keyRequired'), 'error'); return; }
            await api('/training/templates', { method: 'POST', body: JSON.stringify({ template_key: templateKey, title, content, description: description || null }) });
        }
        closeModal('responseTemplateModal');
        showToast(t('training.templateSaved'));
        loadTemplates();
    } catch (e) { showToast(t('training.saveFailed', {error: e.message}), 'error'); }
}

// ═══════════════════════════════════════════════════════════
//  TAB 4: Сценарии диалогов
// ═══════════════════════════════════════════════════════════
async function loadDialogues() {
    const container = document.getElementById('trainingContent-dialogues');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;
    try {
        const data = await api('/training/dialogues');
        const items = data.items || [];
        if (items.length === 0) {
            container.innerHTML = `
                <div class="mb-4"><button class="${tw.btnPrimary}" onclick="window._pages.training.showCreateDialogue()">${t('training.newDialogue')}</button></div>
                <div class="${tw.emptyState}">${t('training.noDialogues')}</div>`;
            return;
        }
        container.innerHTML = `
            <div class="mb-4"><button class="${tw.btnPrimary}" onclick="window._pages.training.showCreateDialogue()">${t('training.newDialogue')}</button></div>
            <div class="overflow-x-auto"><table class="${tw.table}"><thead><tr><th class="${tw.th}">${t('training.dialogueTitle')}</th><th class="${tw.th}">${t('training.scenario')}</th><th class="${tw.th}">${t('training.phase')}</th><th class="${tw.th}">${t('training.tools')}</th><th class="${tw.th}">${t('training.activeCol')}</th><th class="${tw.th}">${t('training.actions')}</th></tr></thead><tbody>
            ${items.map(item => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}">${escapeHtml(item.title)}</td>
                    <td class="${tw.td}"><span class="${tw.badgeBlue}">${escapeHtml(item.scenario_type)}</span></td>
                    <td class="${tw.td}"><span class="${tw.badge}">${escapeHtml(item.phase)}</span></td>
                    <td class="${tw.td}">${(item.tools_used || []).map(t => `<span class="${tw.badgeGray}">${escapeHtml(t)}</span>`).join(' ')}</td>
                    <td class="${tw.td}">${item.is_active !== false ? `<span class="${tw.badgeGreen}">${t('common.yes')}</span>` : `<span class="${tw.badgeRed}">${t('common.no')}</span>`}</td>
                    <td class="${tw.td}">
                        <div class="flex flex-wrap gap-1">
                            <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.training.editDialogue('${item.id}')">${t('common.edit')}</button>
                            <button class="${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.training.deleteDialogue('${item.id}', '${escapeHtml(item.title).replace(/'/g, "\\'")}')">${t('common.delete')}</button>
                        </div>
                    </td>
                </tr>
            `).join('')}
            </tbody></table></div>`;
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

    try {
        if (id) {
            await api(`/training/dialogues/${id}`, { method: 'PATCH', body: JSON.stringify(body) });
        } else {
            await api('/training/dialogues', { method: 'POST', body: JSON.stringify(body) });
        }
        closeModal('dialogueModal');
        showToast(t('training.dialogueSaved'));
        loadDialogues();
    } catch (e) { showToast(t('training.saveFailed', {error: e.message}), 'error'); }
}

async function deleteDialogue(id, title) {
    if (!confirm(t('training.deleteConfirm', {title}))) return;
    try {
        await api(`/training/dialogues/${id}`, { method: 'DELETE' });
        showToast(t('training.dialogueDeleted'));
        loadDialogues();
    } catch (e) { showToast(t('training.deleteFailed', {error: e.message}), 'error'); }
}

// ═══════════════════════════════════════════════════════════
//  TAB 5: Правила безопасности
// ═══════════════════════════════════════════════════════════
function severityBadge(sev) {
    switch (sev) {
        case 'critical': return `<span class="${tw.badgeRed}">${escapeHtml(sev)}</span>`;
        case 'high': return `<span class="${tw.badgeYellow}">${escapeHtml(sev)}</span>`;
        case 'medium': return `<span class="${tw.badgeBlue}">${escapeHtml(sev)}</span>`;
        default: return `<span class="${tw.badge}">${escapeHtml(sev)}</span>`;
    }
}

async function loadSafetyRules() {
    const container = document.getElementById('trainingContent-safety');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;
    try {
        const data = await api('/training/safety-rules');
        const items = data.items || [];
        if (items.length === 0) {
            container.innerHTML = `
                <div class="mb-4"><button class="${tw.btnPrimary}" onclick="window._pages.training.showCreateSafetyRule()">${t('training.newSafetyRule')}</button></div>
                <div class="${tw.emptyState}">${t('training.noSafetyRules')}</div>`;
            return;
        }
        container.innerHTML = `
            <div class="mb-4"><button class="${tw.btnPrimary}" onclick="window._pages.training.showCreateSafetyRule()">${t('training.newSafetyRule')}</button></div>
            <div class="overflow-x-auto"><table class="${tw.table}"><thead><tr><th class="${tw.th}">${t('training.ruleTitle')}</th><th class="${tw.th}">${t('training.ruleType')}</th><th class="${tw.th}">${t('training.severity')}</th><th class="${tw.th}">${t('training.triggerInput')}</th><th class="${tw.th}">${t('training.activeCol')}</th><th class="${tw.th}">${t('training.actions')}</th></tr></thead><tbody>
            ${items.map(item => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}">${escapeHtml(item.title)}</td>
                    <td class="${tw.td}"><span class="${tw.badgeBlue}">${escapeHtml(item.rule_type)}</span></td>
                    <td class="${tw.td}">${severityBadge(item.severity)}</td>
                    <td class="${tw.td}"><span class="${tw.mutedText}">${escapeHtml((item.trigger_input || '').substring(0, 60))}${(item.trigger_input || '').length > 60 ? '...' : ''}</span></td>
                    <td class="${tw.td}">${item.is_active !== false ? `<span class="${tw.badgeGreen}">${t('common.yes')}</span>` : `<span class="${tw.badgeRed}">${t('common.no')}</span>`}</td>
                    <td class="${tw.td}">
                        <div class="flex flex-wrap gap-1">
                            <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.training.editSafetyRule('${item.id}')">${t('common.edit')}</button>
                            <button class="${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.training.deleteSafetyRule('${item.id}', '${escapeHtml(item.title).replace(/'/g, "\\'")}')">${t('common.delete')}</button>
                        </div>
                    </td>
                </tr>
            `).join('')}
            </tbody></table></div>`;
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
    const id = document.getElementById('editSafetyRuleId').value;
    const title = document.getElementById('safetyRuleTitle').value.trim();
    const ruleType = document.getElementById('safetyRuleType').value;
    const severity = document.getElementById('safetyRuleSeverity').value;
    const triggerInput = document.getElementById('safetyTriggerInput').value.trim();
    const expectedBehavior = document.getElementById('safetyExpectedBehavior').value.trim();
    if (!title || !triggerInput || !expectedBehavior) { showToast(t('training.fieldsRequired'), 'error'); return; }

    const body = { title, rule_type: ruleType, severity, trigger_input: triggerInput, expected_behavior: expectedBehavior };
    try {
        if (id) {
            await api(`/training/safety-rules/${id}`, { method: 'PATCH', body: JSON.stringify(body) });
        } else {
            await api('/training/safety-rules', { method: 'POST', body: JSON.stringify(body) });
        }
        closeModal('safetyRuleModal');
        showToast(t('training.safetyRuleSaved'));
        loadSafetyRules();
    } catch (e) { showToast(t('training.saveFailed', {error: e.message}), 'error'); }
}

async function deleteSafetyRule(id, title) {
    if (!confirm(t('training.deleteConfirm', {title}))) return;
    try {
        await api(`/training/safety-rules/${id}`, { method: 'DELETE' });
        showToast(t('training.safetyRuleDeleted'));
        loadSafetyRules();
    } catch (e) { showToast(t('training.deleteFailed', {error: e.message}), 'error'); }
}

// ═══════════════════════════════════════════════════════════
//  TAB 6: Инструменты
// ═══════════════════════════════════════════════════════════
async function loadTools() {
    const container = document.getElementById('trainingContent-tools');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;
    try {
        const data = await api('/training/tools');
        const items = data.items || [];
        container.innerHTML = `
            <div class="overflow-x-auto"><table class="${tw.table}"><thead><tr><th class="${tw.th}">${t('training.toolName')}</th><th class="${tw.th}">${t('training.description')}</th><th class="${tw.th}">${t('training.override')}</th><th class="${tw.th}">${t('training.actions')}</th></tr></thead><tbody>
            ${items.map(item => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}"><span class="${tw.badgeBlue}">${escapeHtml(item.name)}</span></td>
                    <td class="${tw.td}"><span class="${tw.mutedText}">${escapeHtml((item.effective_description || '').substring(0, 100))}${(item.effective_description || '').length > 100 ? '...' : ''}</span></td>
                    <td class="${tw.td}">${item.has_override ? `<span class="${tw.badgeYellow}">${t('training.overridden')}</span>` : `<span class="${tw.badge}">${t('training.default')}</span>`}</td>
                    <td class="${tw.td}">
                        <div class="flex flex-wrap gap-1">
                            <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.training.editToolOverride('${escapeHtml(item.name)}')">${t('common.edit')}</button>
                            ${item.has_override ? `<button class="${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.training.resetToolOverride('${escapeHtml(item.name)}')">${t('training.reset')}</button>` : ''}
                        </div>
                    </td>
                </tr>
            `).join('')}
            </tbody></table></div>`;
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('training.loadFailed', {error: escapeHtml(e.message)})}</div>`;
    }
}

async function editToolOverride(toolName) {
    try {
        const data = await api('/training/tools');
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
    registerPageLoader('training', () => showTab(_activeTab));

    // Backward-compat redirects for old page names
    registerPageLoader('prompts', () => { _activeTab = 'prompts'; window._app.showPage('training'); });
    registerPageLoader('knowledge', () => { _activeTab = 'knowledge'; window._app.showPage('training'); });
}

window._pages = window._pages || {};
window._pages.training = {
    showTab,
    // Prompts
    loadPromptVersions, showCreatePrompt, createPrompt, activatePrompt,
    // Knowledge
    loadArticles, showCreateArticle, editArticle, saveArticle,
    toggleArticle, deleteArticle, reindexArticle, reindexAll,
    showImportModal, importDocuments,
    // Templates
    loadTemplates, showCreateTemplate, editTemplate, saveTemplate,
    // Dialogues
    loadDialogues, showCreateDialogue, editDialogue, saveDialogue, deleteDialogue,
    // Safety
    loadSafetyRules, showCreateSafetyRule, editSafetyRule, saveSafetyRule, deleteSafetyRule,
    // Tools
    loadTools, editToolOverride, saveToolOverride, resetToolOverride,
};
