import { api, apiUpload, fetchWithAuth } from '../api.js';
import { showToast } from '../notifications.js';
import { qualityBadge, formatDate, escapeHtml, closeModal, downloadBlob } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import { makeSortable } from '../sorting.js';
import * as tw from '../tw.js';

// ─── State ───────────────────────────────────────────────────
let _categories = [];
let _activeTab = 'prompts';

// ─── Tab switching ───────────────────────────────────────────
function showTab(tab) {
    _activeTab = tab;
    const tabs = ['prompts', 'knowledge', 'templates', 'dialogues', 'safety', 'tools', 'sources'];
    tabs.forEach(t => {
        const el = document.getElementById(`trainingContent-${t}`);
        if (el) el.style.display = t === tab ? 'block' : 'none';
    });
    document.querySelectorAll('#page-training .tab-bar button').forEach(b => b.classList.remove('active'));
    const activeBtn = document.querySelector(`#page-training .tab-bar button[data-tab="${tab}"]`);
    if (activeBtn) activeBtn.classList.add('active');

    // Load data for the tab
    const loaders = { prompts: loadPromptVersions, knowledge: loadKnowledge, templates: loadTemplates, dialogues: loadDialogues, safety: loadSafetyRules, tools: loadTools, sources: loadSourcesAndWatched };
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
        _promptVersionsCache = versions;
        if (versions.length === 0) {
            container.innerHTML = `
                <div class="mb-4">
                    <button class="${tw.btnPrimary}" onclick="window._pages.training.showCreatePrompt()">${t('prompts.newVersion')}</button>
                    <button class="${tw.btnSecondary} ml-2" onclick="window._pages.training.resetToDefault()">${t('prompts.resetToDefault')}</button>
                </div>
                <div class="${tw.emptyState}">${t('prompts.noVersions')}</div>`;
            loadABTests();
            return;
        }
        container.innerHTML = `
            <div class="mb-4">
                <button class="${tw.btnPrimary}" onclick="window._pages.training.showCreatePrompt()">${t('prompts.newVersion')}</button>
                <button class="${tw.btnSecondary} ml-2" onclick="window._pages.training.resetToDefault()">${t('prompts.resetToDefault')}</button>
            </div>
            <div class="overflow-x-auto"><table class="${tw.table}" id="promptsTable"><thead><tr><th class="${tw.thSortable}" data-sortable>${t('prompts.name')}</th><th class="${tw.thSortable}" data-sortable>${t('prompts.active')}</th><th class="${tw.thSortable}" data-sortable>${t('prompts.created')}</th><th class="${tw.th}">${t('prompts.action')}</th></tr></thead><tbody>
            ${versions.map(v => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}"><a href="#" class="text-blue-600 dark:text-blue-400 hover:underline" onclick="event.preventDefault(); window._pages.training.viewPrompt('${v.id}')">${escapeHtml(v.name)}</a></td>
                    <td class="${tw.td}">${v.is_active ? `<span class="${tw.badgeGreen}">${t('prompts.activeLabel')}</span>` : ''}</td>
                    <td class="${tw.td}" data-sort-value="${v.created_at || ''}">${formatDate(v.created_at)}</td>
                    <td class="${tw.td}">
                        <div class="flex flex-wrap gap-1">
                            ${!v.is_active ? `<button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.training.activatePrompt('${v.id}')">${t('prompts.activateBtn')}</button>` : ''}
                            ${!v.is_active ? `<button class="${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.training.deletePrompt('${v.id}', '${escapeHtml(v.name).replace(/'/g, "\\'")}')">${t('common.delete')}</button>` : ''}
                        </div>
                    </td>
                </tr>
            `).join('')}
            </tbody></table></div>`;

        makeSortable('promptsTable');
        loadABTests();
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('prompts.failedToLoad', {error: escapeHtml(e.message)})}
            <br><button class="${tw.btnPrimary} ${tw.btnSm} mt-2" onclick="window._pages.training.loadPromptVersions()">${t('common.retry')}</button></div>`;
    }
}

function showCreatePrompt() {
    const modalTitle = document.getElementById('createPromptModalTitle');
    if (modalTitle) modalTitle.textContent = t('prompts.createTitle');
    document.getElementById('promptName').value = '';
    document.getElementById('promptName').disabled = false;
    document.getElementById('promptSystemPrompt').value = '';
    document.getElementById('promptSystemPrompt').disabled = false;
    document.getElementById('createPromptBtn').style.display = '';
    document.getElementById('loadDefaultBtn').style.display = '';
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

async function viewPrompt(id) {
    try {
        const data = await api(`/prompts/${id}`);
        const v = data.version;
        const modalTitle = document.getElementById('createPromptModalTitle');
        if (modalTitle) modalTitle.textContent = t('prompts.viewTitle');
        document.getElementById('promptName').value = v.name || '';
        document.getElementById('promptName').disabled = true;
        document.getElementById('promptSystemPrompt').value = v.system_prompt || '';
        document.getElementById('promptSystemPrompt').disabled = true;
        document.getElementById('createPromptBtn').style.display = 'none';
        document.getElementById('loadDefaultBtn').style.display = 'none';
        document.getElementById('createPromptModal').classList.add('show');
    } catch (e) { showToast(t('prompts.failedToLoad', {error: e.message}), 'error'); }
}

async function loadDefaultPrompt() {
    try {
        const data = await api('/prompts/default');
        document.getElementById('promptSystemPrompt').value = data.system_prompt || '';
    } catch (e) { showToast(t('prompts.failedToLoad', {error: e.message}), 'error'); }
}

async function resetToDefault() {
    if (!confirm(t('prompts.resetConfirm'))) return;
    try {
        const data = await api('/prompts/default');
        const created = await api('/prompts', {
            method: 'POST',
            body: JSON.stringify({ name: `${data.name} (reset)`, system_prompt: data.system_prompt }),
        });
        const newId = created.version?.id;
        if (newId) {
            await api(`/prompts/${newId}/activate`, { method: 'PATCH' });
        }
        showToast(t('prompts.resetDone'));
        loadPromptVersions();
    } catch (e) { showToast(t('prompts.resetFailed', {error: e.message}), 'error'); }
}

async function deletePrompt(id, name) {
    if (!confirm(t('prompts.deleteConfirm', {name}))) return;
    try {
        await api(`/prompts/${id}`, { method: 'DELETE' });
        showToast(t('prompts.deleted'));
        loadPromptVersions();
    } catch (e) { showToast(t('prompts.deleteFailed', {error: e.message}), 'error'); }
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
            <div class="overflow-x-auto"><table class="${tw.table}" id="articlesTable"><thead><tr><th class="${tw.thSortable}" data-sortable>${t('knowledge.articleTitle')}</th><th class="${tw.thSortable}" data-sortable>${t('knowledge.category')}</th><th class="${tw.thSortable}" data-sortable>${t('knowledge.embedding')}</th><th class="${tw.thSortable}" data-sortable>${t('knowledge.activeCol')}</th><th class="${tw.thSortable}" data-sortable>${t('knowledge.updated')}</th><th class="${tw.th}">${t('knowledge.actions')}</th></tr></thead><tbody>
            ${articles.map(a => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}">${escapeHtml(a.title)}</td>
                    <td class="${tw.td}"><span class="${tw.badgeBlue}">${escapeHtml(a.category)}</span></td>
                    <td class="${tw.td}">${embeddingBadge(a.embedding_status)}</td>
                    <td class="${tw.td}">${a.active !== false ? `<span class="${tw.badgeGreen}">${t('common.yes')}</span>` : `<span class="${tw.badgeRed}">${t('common.no')}</span>`}</td>
                    <td class="${tw.td}" data-sort-value="${a.updated_at || a.created_at || ''}">${formatDate(a.updated_at || a.created_at)}</td>
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

        makeSortable('articlesTable');
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
//  TAB 3: Шаблоны ответов (with variant support)
// ═══════════════════════════════════════════════════════════
async function loadTemplates() {
    const container = document.getElementById('trainingContent-templates');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;
    try {
        const data = await api('/training/templates/');
        const items = data.items || [];
        if (items.length === 0) {
            container.innerHTML = `
                <div class="mb-4"><button class="${tw.btnPrimary}" onclick="window._pages.training.showCreateTemplate()">${t('training.newTemplate')}</button></div>
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
                    ${isFirst ? `<td class="${tw.td}" rowspan="${variantCount}"><span class="${tw.badgeBlue}">${escapeHtml(key)}</span><br><span class="${tw.mutedText} text-xs">${variantCount} ${t('training.variantCount', {count: variantCount})}</span><br><button class="${tw.btnPrimary} ${tw.btnSm} mt-1" onclick="window._pages.training.addVariant('${escapeHtml(key)}')">${t('training.addVariant')}</button></td>` : ''}
                    <td class="${tw.td}"><span class="${tw.badge}">#${item.variant_number}</span></td>
                    <td class="${tw.td}">${escapeHtml(item.title)}</td>
                    <td class="${tw.td}"><span class="${tw.mutedText}">${escapeHtml((item.content || '').substring(0, 80))}${(item.content || '').length > 80 ? '...' : ''}</span></td>
                    <td class="${tw.td}">${item.is_active !== false ? `<span class="${tw.badgeGreen}">${t('common.yes')}</span>` : `<span class="${tw.badgeRed}">${t('common.no')}</span>`}</td>
                    <td class="${tw.td}">
                        <div class="flex flex-wrap gap-1">
                            <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.training.editTemplate('${item.id}')">${t('common.edit')}</button>
                            ${variantCount > 1 ? `<button class="${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.training.deleteTemplate('${item.id}', '${escapeHtml(item.title).replace(/'/g, "\\'")}')">${t('common.delete')}</button>` : ''}
                        </div>
                    </td>
                </tr>`;
            }
        }

        container.innerHTML = `
            <div class="mb-4">
                <button class="${tw.btnPrimary}" onclick="window._pages.training.showCreateTemplate()">${t('training.newTemplate')}</button>
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
            await api('/training/templates/', { method: 'POST', body: JSON.stringify({ template_key: templateKey, title, content, description: description || null }) });
        }
        closeModal('responseTemplateModal');
        showToast(t('training.templateSaved'));
        loadTemplates();
    } catch (e) { showToast(t('training.saveFailed', {error: e.message}), 'error'); }
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
//  TAB 4: Сценарии диалогов
// ═══════════════════════════════════════════════════════════
async function loadDialogues() {
    const container = document.getElementById('trainingContent-dialogues');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;
    try {
        const data = await api('/training/dialogues/');
        const items = data.items || [];
        if (items.length === 0) {
            container.innerHTML = `
                <div class="mb-4"><button class="${tw.btnPrimary}" onclick="window._pages.training.showCreateDialogue()">${t('training.newDialogue')}</button></div>
                <div class="${tw.emptyState}">${t('training.noDialogues')}</div>`;
            return;
        }
        container.innerHTML = `
            <div class="mb-4"><button class="${tw.btnPrimary}" onclick="window._pages.training.showCreateDialogue()">${t('training.newDialogue')}</button></div>
            <div class="overflow-x-auto"><table class="${tw.table}" id="dialoguesTable"><thead><tr><th class="${tw.thSortable}" data-sortable>${t('training.dialogueTitle')}</th><th class="${tw.thSortable}" data-sortable>${t('training.scenario')}</th><th class="${tw.thSortable}" data-sortable>${t('training.phase')}</th><th class="${tw.th}">${t('training.tools')}</th><th class="${tw.thSortable}" data-sortable>${t('training.activeCol')}</th><th class="${tw.th}">${t('training.actions')}</th></tr></thead><tbody>
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

        makeSortable('dialoguesTable');
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
            await api('/training/dialogues/', { method: 'POST', body: JSON.stringify(body) });
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
const SEVERITY_WEIGHT = { critical: 4, high: 3, medium: 2, low: 1 };

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
        const data = await api('/training/safety-rules/');
        const items = data.items || [];
        if (items.length === 0) {
            container.innerHTML = `
                <div class="mb-4"><button class="${tw.btnPrimary}" onclick="window._pages.training.showCreateSafetyRule()">${t('training.newSafetyRule')}</button></div>
                <div class="${tw.emptyState}">${t('training.noSafetyRules')}</div>`;
            return;
        }
        container.innerHTML = `
            <div class="mb-4"><button class="${tw.btnPrimary}" onclick="window._pages.training.showCreateSafetyRule()">${t('training.newSafetyRule')}</button></div>
            <div class="overflow-x-auto"><table class="${tw.table}" id="safetyRulesTable"><thead><tr><th class="${tw.thSortable}" data-sortable>${t('training.ruleTitle')}</th><th class="${tw.thSortable}" data-sortable>${t('training.ruleType')}</th><th class="${tw.thSortable}" data-sortable>${t('training.severity')}</th><th class="${tw.th}">${t('training.triggerInput')}</th><th class="${tw.thSortable}" data-sortable>${t('training.activeCol')}</th><th class="${tw.th}">${t('training.actions')}</th></tr></thead><tbody>
            ${items.map(item => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}">${escapeHtml(item.title)}</td>
                    <td class="${tw.td}"><span class="${tw.badgeBlue}">${escapeHtml(item.rule_type)}</span></td>
                    <td class="${tw.td}" data-sort-value="${SEVERITY_WEIGHT[item.severity] || 0}">${severityBadge(item.severity)}</td>
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

        makeSortable('safetyRulesTable');
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
            await api('/training/safety-rules/', { method: 'POST', body: JSON.stringify(body) });
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
        const data = await api('/training/tools/');
        const items = data.items || [];
        container.innerHTML = `
            <div class="overflow-x-auto"><table class="${tw.table}" id="toolsTable"><thead><tr><th class="${tw.thSortable}" data-sortable>${t('training.toolName')}</th><th class="${tw.th}">${t('training.description')}</th><th class="${tw.thSortable}" data-sortable>${t('training.override')}</th><th class="${tw.th}">${t('training.actions')}</th></tr></thead><tbody>
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
//  TAB 7: Источники (scraper sources)
// ═══════════════════════════════════════════════════════════
function sourceStatusBadge(status) {
    switch (status) {
        case 'processed': return `<span class="${tw.badgeGreen}">${escapeHtml(status)}</span>`;
        case 'processing': return `<span class="${tw.badgeBlue}">${escapeHtml(status)}</span>`;
        case 'new': return `<span class="${tw.badgeYellow}">${escapeHtml(status)}</span>`;
        case 'skipped': return `<span class="${tw.badge}">${escapeHtml(status)}</span>`;
        case 'error': return `<span class="${tw.badgeRed}">${escapeHtml(status)}</span>`;
        case 'duplicate': return `<span class="${tw.badge}">${t('sources.duplicate')}</span>`;
        case 'duplicate_suspect': return `<span class="${tw.badgeYellow}">${t('sources.duplicateSuspect')}</span>`;
        default: return `<span class="${tw.badge}">${escapeHtml(status || 'unknown')}</span>`;
    }
}

function loadSourcesAndWatched() {
    loadSourceConfigs();
    loadSources();
    loadWatchedPages();
}

// ─── Source Configs ───────────────────────────────────────
function sourceTypeBadge(type) {
    switch (type) {
        case 'prokoleso': return `<span class="${tw.badgeBlue}">ProKoleso</span>`;
        case 'rss': return `<span class="${tw.badgeYellow}">RSS</span>`;
        case 'generic_html': return `<span class="${tw.badge}">HTML</span>`;
        default: return `<span class="${tw.badge}">${escapeHtml(type)}</span>`;
    }
}

function langBadge(lang) {
    const labels = { uk: '🇺🇦 UK', de: '🇩🇪 DE', en: '🇬🇧 EN', fr: '🇫🇷 FR' };
    return `<span class="${tw.badge}">${labels[lang] || lang}</span>`;
}

function runStatusBadge(status) {
    if (!status) return `<span class="${tw.mutedText} text-xs">—</span>`;
    switch (status) {
        case 'ok': return `<span class="${tw.badgeGreen}">ok</span>`;
        case 'error': return `<span class="${tw.badgeRed}">error</span>`;
        case 'disabled': return `<span class="${tw.badge}">disabled</span>`;
        default: return `<span class="${tw.badge}">${escapeHtml(status)}</span>`;
    }
}

async function loadSourceConfigs() {
    const container = document.getElementById('sourceConfigsContainer');
    if (!container) return;
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    try {
        const data = await api('/admin/scraper/source-configs');
        const configs = data.configs || [];

        let html = `
            <div class="mb-4">
                <button class="${tw.btnPrimary}" onclick="window._pages.training.showAddSourceConfig()">${t('sources.addSource')}</button>
            </div>`;

        if (configs.length === 0) {
            html += `<div class="${tw.emptyState}">${t('sources.noSourceConfigs')}</div>`;
        } else {
            const days = { monday: t('sources.scheduleDays.monday'), tuesday: t('sources.scheduleDays.tuesday'), wednesday: t('sources.scheduleDays.wednesday'), thursday: t('sources.scheduleDays.thursday'), friday: t('sources.scheduleDays.friday'), saturday: t('sources.scheduleDays.saturday'), sunday: t('sources.scheduleDays.sunday') };

            html += `
            <div class="overflow-x-auto"><table class="${tw.table}" id="sourceConfigsTable"><thead><tr>
                <th class="${tw.thSortable}" data-sortable>${t('sources.sourceName')}</th>
                <th class="${tw.th}">${t('sources.sourceType')}</th>
                <th class="${tw.th}">${t('sources.sourceLanguage')}</th>
                <th class="${tw.thSortable}" data-sortable>${t('sources.enabled')}</th>
                <th class="${tw.th}">${t('sources.schedule')}</th>
                <th class="${tw.thSortable}" data-sortable>${t('sources.lastRun')}</th>
                <th class="${tw.th}">${t('sources.actions')}</th>
            </tr></thead><tbody>
            ${configs.map(c => {
                const schedText = c.schedule_enabled ? `${days[c.schedule_day_of_week] || c.schedule_day_of_week} ${String(c.schedule_hour).padStart(2,'0')}:00` : t('common.no');
                const statsText = c.last_run_stats ? `${t('sources.processed')}: ${c.last_run_stats.processed || 0}` : '';
                return `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}">
                        <div>${escapeHtml(c.name)}</div>
                        <div class="${tw.mutedText} text-xs">${escapeHtml((c.source_url || '').replace(/^https?:\/\//, '').substring(0, 40))}</div>
                    </td>
                    <td class="${tw.td}">${sourceTypeBadge(c.source_type)}</td>
                    <td class="${tw.td}">${langBadge(c.language)}</td>
                    <td class="${tw.td}" data-sort-value="${c.enabled ? 1 : 0}">
                        <label class="inline-flex items-center cursor-pointer">
                            <input type="checkbox" ${c.enabled ? 'checked' : ''} onchange="window._pages.training.toggleSourceConfigEnabled('${c.id}', this.checked)">
                        </label>
                    </td>
                    <td class="${tw.td}"><span class="text-xs">${escapeHtml(schedText)}</span></td>
                    <td class="${tw.td}" data-sort-value="${c.last_run_at || ''}">
                        ${runStatusBadge(c.last_run_status)}
                        ${c.last_run_at ? `<br><span class="${tw.mutedText} text-xs">${formatDate(c.last_run_at)}</span>` : ''}
                        ${statsText ? `<br><span class="${tw.mutedText} text-xs">${statsText}</span>` : ''}
                    </td>
                    <td class="${tw.td}">
                        <div class="flex flex-wrap gap-1">
                            <button class="${tw.btnGreen} ${tw.btnSm}" onclick="window._pages.training.runSourceConfig('${c.id}')">${t('sources.runSource')}</button>
                            <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.training.editSourceConfig('${c.id}')">${t('common.edit')}</button>
                            <button class="${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.training.deleteSourceConfig('${c.id}', '${escapeHtml(c.name).replace(/'/g, "\\'")}')">${t('common.delete')}</button>
                        </div>
                    </td>
                </tr>`;
            }).join('')}
            </tbody></table></div>`;
        }

        container.innerHTML = html;
        makeSortable('sourceConfigsTable');
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('sources.failedToLoad', {error: escapeHtml(e.message)})}
            <br><button class="${tw.btnPrimary} ${tw.btnSm} mt-2" onclick="window._pages.training.loadSourceConfigs()">${t('common.retry')}</button></div>`;
    }
}

function showAddSourceConfig() {
    document.getElementById('sourceConfigModalTitle').textContent = t('sources.addSource');
    document.getElementById('editSourceConfigId').value = '';
    document.getElementById('sourceConfigName').value = '';
    document.getElementById('sourceConfigType').value = 'generic_html';
    document.getElementById('sourceConfigLanguage').value = 'de';
    document.getElementById('sourceConfigUrl').value = '';
    document.getElementById('sourceConfigMaxArticles').value = '20';
    document.getElementById('sourceConfigDay').value = 'monday';
    document.getElementById('sourceConfigHour').value = '6';
    document.getElementById('sourceConfigEnabled').checked = false;
    document.getElementById('sourceConfigAutoApprove').checked = false;
    document.getElementById('sourceConfigSettings').value = '{}';
    document.getElementById('sourceConfigModal').classList.add('show');
}

async function editSourceConfig(id) {
    try {
        const data = await api('/admin/scraper/source-configs');
        const cfg = (data.configs || []).find(c => c.id === id);
        if (!cfg) { showToast(t('sources.configLoadFailed', {error: 'Not found'}), 'error'); return; }

        document.getElementById('sourceConfigModalTitle').textContent = t('sources.editSource');
        document.getElementById('editSourceConfigId').value = id;
        document.getElementById('sourceConfigName').value = cfg.name || '';
        document.getElementById('sourceConfigType').value = cfg.source_type || 'generic_html';
        document.getElementById('sourceConfigLanguage').value = cfg.language || 'uk';
        document.getElementById('sourceConfigUrl').value = cfg.source_url || '';
        document.getElementById('sourceConfigMaxArticles').value = String(cfg.max_articles_per_run || 20);
        document.getElementById('sourceConfigDay').value = cfg.schedule_day_of_week || 'monday';
        document.getElementById('sourceConfigHour').value = String(cfg.schedule_hour ?? 6);
        document.getElementById('sourceConfigEnabled').checked = !!cfg.enabled;
        document.getElementById('sourceConfigAutoApprove').checked = !!cfg.auto_approve;
        document.getElementById('sourceConfigSettings').value = JSON.stringify(cfg.settings || {}, null, 2);
        document.getElementById('sourceConfigModal').classList.add('show');
    } catch (e) { showToast(t('sources.configLoadFailed', {error: e.message}), 'error'); }
}

async function saveSourceConfig() {
    const id = document.getElementById('editSourceConfigId').value;
    const name = document.getElementById('sourceConfigName').value.trim();
    const source_type = document.getElementById('sourceConfigType').value;
    const language = document.getElementById('sourceConfigLanguage').value;
    const source_url = document.getElementById('sourceConfigUrl').value.trim();
    const max_articles_per_run = parseInt(document.getElementById('sourceConfigMaxArticles').value, 10);
    const schedule_day_of_week = document.getElementById('sourceConfigDay').value;
    const schedule_hour = parseInt(document.getElementById('sourceConfigHour').value, 10);
    const enabled = document.getElementById('sourceConfigEnabled').checked;
    const auto_approve = document.getElementById('sourceConfigAutoApprove').checked;
    let settings;
    try { settings = JSON.parse(document.getElementById('sourceConfigSettings').value); } catch { showToast(t('sources.invalidSettingsJson'), 'error'); return; }

    if (!name || !source_url) { showToast(t('sources.nameUrlRequired'), 'error'); return; }

    const body = { name, source_type, source_url, language, max_articles_per_run, schedule_day_of_week, schedule_hour, enabled, auto_approve, settings };

    try {
        if (id) {
            await api(`/admin/scraper/source-configs/${id}`, { method: 'PATCH', body: JSON.stringify(body) });
        } else {
            await api('/admin/scraper/source-configs', { method: 'POST', body: JSON.stringify(body) });
        }
        closeModal('sourceConfigModal');
        showToast(t('sources.sourceConfigSaved'));
        loadSourceConfigs();
    } catch (e) { showToast(t('sources.sourceConfigSaveFailed', {error: e.message}), 'error'); }
}

async function toggleSourceConfigEnabled(id, enabled) {
    try {
        await api(`/admin/scraper/source-configs/${id}`, { method: 'PATCH', body: JSON.stringify({ enabled }) });
        showToast(t('sources.configUpdated'));
    } catch (e) { showToast(t('sources.configUpdateFailed', {error: e.message}), 'error'); loadSourceConfigs(); }
}

async function runSourceConfig(id) {
    try {
        await api(`/admin/scraper/source-configs/${id}/run`, { method: 'POST' });
        showToast(t('sources.runDispatched'));
    } catch (e) { showToast(t('sources.runFailed', {error: e.message}), 'error'); }
}

async function deleteSourceConfig(id, name) {
    if (!confirm(t('sources.deleteSourceConfirm', {name}))) return;
    try {
        await api(`/admin/scraper/source-configs/${id}`, { method: 'DELETE' });
        showToast(t('sources.sourceConfigDeleted'));
        loadSourceConfigs();
    } catch (e) { showToast(t('sources.sourceConfigDeleteFailed', {error: e.message}), 'error'); }
}

async function loadSources() {
    const configCard = document.getElementById('scraperConfigCard');
    const container = document.getElementById('sourcesContainer');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    try {
        const configData = await api('/admin/scraper/config');
        const cfg = configData.config || {};

        const days = ['monday','tuesday','wednesday','thursday','friday','saturday','sunday'];
        const dayOptions = days.map(d => `<option value="${d}" ${(cfg.schedule_day_of_week || 'monday') === d ? 'selected' : ''}>${t('sources.scheduleDays.' + d)}</option>`).join('');
        const hourOptions = Array.from({length: 24}, (_, i) => `<option value="${i}" ${(cfg.schedule_hour ?? 6) === i ? 'selected' : ''}>${String(i).padStart(2, '0')}:00</option>`).join('');

        configCard.innerHTML = `
            <div class="flex flex-wrap items-center gap-4 mb-2">
                <label class="flex items-center gap-2 text-sm cursor-pointer">
                    <input type="checkbox" id="scraperEnabled" ${cfg.enabled ? 'checked' : ''} onchange="window._pages.training.toggleScraperEnabled()">
                    <span>${t('sources.enabled')}</span>
                </label>
                <label class="flex items-center gap-2 text-sm cursor-pointer">
                    <input type="checkbox" id="scraperAutoApprove" ${cfg.auto_approve ? 'checked' : ''} onchange="window._pages.training.toggleAutoApprove()">
                    <span>${t('sources.autoApprove')}</span>
                </label>
                <label class="flex items-center gap-2 text-sm cursor-pointer">
                    <input type="checkbox" id="scraperDedupLlm" ${cfg.dedup_llm_check ? 'checked' : ''} onchange="window._pages.training.toggleDedupLlm()">
                    <span title="${t('sources.dedupLlmCheckHint')}">${t('sources.dedupLlmCheck')}</span>
                </label>
                <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.training.runScraperNow()">${t('sources.runNow')}</button>
            </div>
            <div class="flex flex-wrap items-center gap-4 mb-2">
                <label class="flex items-center gap-2 text-sm cursor-pointer">
                    <input type="checkbox" id="scraperScheduleEnabled" ${cfg.schedule_enabled !== false ? 'checked' : ''} onchange="window._pages.training.updateSchedule()">
                    <span>${t('sources.scheduleEnabled')}</span>
                </label>
                <label class="flex items-center gap-2 text-sm">
                    <span>${t('sources.scheduleDay')}</span>
                    <select id="scraperScheduleDay" class="border rounded px-2 py-1 text-sm dark:bg-gray-700 dark:border-gray-600" onchange="window._pages.training.updateSchedule()">${dayOptions}</select>
                </label>
                <label class="flex items-center gap-2 text-sm">
                    <span>${t('sources.scheduleHour')}</span>
                    <select id="scraperScheduleHour" class="border rounded px-2 py-1 text-sm dark:bg-gray-700 dark:border-gray-600" onchange="window._pages.training.updateSchedule()">${hourOptions}</select>
                </label>
            </div>
            <div class="flex flex-wrap items-center gap-4">
                <label class="flex items-center gap-2 text-sm">
                    <span>${t('sources.minDate')}</span>
                    <input type="date" id="scraperMinDate" value="${escapeHtml(cfg.min_date || '')}" class="border rounded px-2 py-1 text-sm dark:bg-gray-700 dark:border-gray-600" onchange="window._pages.training.updateMinDate()">
                    <span class="${tw.mutedText} text-xs" title="${t('sources.minDateHint')}">?</span>
                </label>
                <label class="flex items-center gap-2 text-sm">
                    <span>${t('sources.maxDate')}</span>
                    <input type="date" id="scraperMaxDate" value="${escapeHtml(cfg.max_date || '')}" class="border rounded px-2 py-1 text-sm dark:bg-gray-700 dark:border-gray-600" onchange="window._pages.training.updateMaxDate()">
                    <span class="${tw.mutedText} text-xs" title="${t('sources.maxDateHint')}">?</span>
                </label>
            </div>`;
    } catch (e) {
        configCard.innerHTML = `<div class="${tw.mutedText}">${t('sources.configLoadFailed', {error: escapeHtml(e.message)})}</div>`;
    }

    // Load sources list
    const params = new URLSearchParams();
    const statusFilter = document.getElementById('sourcesStatusFilter')?.value;
    if (statusFilter) params.set('status', statusFilter);

    try {
        const data = await api(`/admin/scraper/sources?${params}`);
        const sources = data.sources || [];
        if (sources.length === 0) {
            container.innerHTML = `<div class="${tw.emptyState}">${t('sources.noSources')}</div>`;
            return;
        }
        container.innerHTML = `
            <div class="overflow-x-auto"><table class="${tw.table}" id="sourcesTable"><thead><tr><th class="${tw.thSortable}" data-sortable>${t('sources.url')}</th><th class="${tw.thSortable}" data-sortable>${t('sources.originalTitle')}</th><th class="${tw.thSortable}" data-sortable>${t('sources.status')}</th><th class="${tw.thSortable}" data-sortable>${t('sources.date')}</th><th class="${tw.th}">${t('sources.actions')}</th></tr></thead><tbody>
            ${sources.map(s => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}"><a href="${escapeHtml(s.url)}" target="_blank" class="text-blue-600 dark:text-blue-400 hover:underline text-xs">${escapeHtml((s.url || '').replace(/^https?:\/\//, '').substring(0, 50))}${(s.url || '').length > 60 ? '...' : ''}</a></td>
                    <td class="${tw.td}">${escapeHtml(s.original_title || '-')}</td>
                    <td class="${tw.td}" data-sort-value="${escapeHtml(s.status || '')}">${sourceStatusBadge(s.status)}${s.skip_reason ? `<br><span class="${tw.mutedText} text-xs">${escapeHtml(s.skip_reason)}</span>` : ''}</td>
                    <td class="${tw.td}" data-sort-value="${s.created_at || ''}">${formatDate(s.created_at)}</td>
                    <td class="${tw.td}">
                        <div class="flex flex-wrap gap-1">
                            ${s.status === 'processed' && s.article_id ? `
                                <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.training.viewSourceArticle('${s.article_id}')">${t('sources.viewArticle')}</button>
                                ${s.article_active === false ? `<button class="${tw.btnGreen} ${tw.btnSm}" onclick="window._pages.training.approveSource('${s.id}')">${t('sources.approve')}</button>` : ''}
                                <button class="${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.training.rejectSource('${s.id}')">${t('sources.reject')}</button>
                            ` : ''}
                        </div>
                    </td>
                </tr>
            `).join('')}
            </tbody></table></div>
            <p class="${tw.mutedText} mt-2">${t('sources.total', {count: data.total})}</p>`;

        makeSortable('sourcesTable');
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('sources.failedToLoad', {error: escapeHtml(e.message)})}
            <br><button class="${tw.btnPrimary} ${tw.btnSm} mt-2" onclick="window._pages.training.loadSources()">${t('common.retry')}</button></div>`;
    }
}

async function toggleScraperEnabled() {
    const enabled = document.getElementById('scraperEnabled').checked;
    try {
        await api('/admin/scraper/config', { method: 'PATCH', body: JSON.stringify({ enabled }) });
        showToast(t('sources.configUpdated'));
    } catch (e) { showToast(t('sources.configUpdateFailed', {error: e.message}), 'error'); }
}

async function toggleAutoApprove() {
    const auto_approve = document.getElementById('scraperAutoApprove').checked;
    try {
        await api('/admin/scraper/config', { method: 'PATCH', body: JSON.stringify({ auto_approve }) });
        showToast(t('sources.configUpdated'));
    } catch (e) { showToast(t('sources.configUpdateFailed', {error: e.message}), 'error'); }
}

async function updateSchedule() {
    const schedule_enabled = document.getElementById('scraperScheduleEnabled').checked;
    const schedule_day_of_week = document.getElementById('scraperScheduleDay').value;
    const schedule_hour = parseInt(document.getElementById('scraperScheduleHour').value, 10);
    try {
        await api('/admin/scraper/config', { method: 'PATCH', body: JSON.stringify({ schedule_enabled, schedule_day_of_week, schedule_hour }) });
        showToast(t('sources.configUpdated'));
    } catch (e) { showToast(t('sources.configUpdateFailed', {error: e.message}), 'error'); }
}

async function toggleDedupLlm() {
    const dedup_llm_check = document.getElementById('scraperDedupLlm').checked;
    try {
        await api('/admin/scraper/config', { method: 'PATCH', body: JSON.stringify({ dedup_llm_check }) });
        showToast(t('sources.configUpdated'));
    } catch (e) { showToast(t('sources.configUpdateFailed', {error: e.message}), 'error'); }
}

async function updateMinDate() {
    const min_date = document.getElementById('scraperMinDate').value || '';
    try {
        await api('/admin/scraper/config', { method: 'PATCH', body: JSON.stringify({ min_date }) });
        showToast(t('sources.configUpdated'));
    } catch (e) { showToast(t('sources.configUpdateFailed', {error: e.message}), 'error'); }
}

async function updateMaxDate() {
    const max_date = document.getElementById('scraperMaxDate').value || '';
    try {
        await api('/admin/scraper/config', { method: 'PATCH', body: JSON.stringify({ max_date }) });
        showToast(t('sources.configUpdated'));
    } catch (e) { showToast(t('sources.configUpdateFailed', {error: e.message}), 'error'); }
}

async function runScraperNow() {
    try {
        await api('/admin/scraper/run', { method: 'POST' });
        showToast(t('sources.runDispatched'));
    } catch (e) { showToast(t('sources.runFailed', {error: e.message}), 'error'); }
}

async function approveSource(id) {
    try {
        await api(`/admin/scraper/sources/${id}/approve`, { method: 'POST' });
        showToast(t('sources.approved'));
        loadSources();
    } catch (e) { showToast(t('sources.approveFailed', {error: e.message}), 'error'); }
}

async function rejectSource(id) {
    if (!confirm(t('sources.rejectConfirm'))) return;
    try {
        await api(`/admin/scraper/sources/${id}/reject`, { method: 'POST' });
        showToast(t('sources.rejected'));
        loadSources();
    } catch (e) { showToast(t('sources.rejectFailed', {error: e.message}), 'error'); }
}

function viewSourceArticle(articleId) {
    _activeTab = 'knowledge';
    showTab('knowledge');
    // After knowledge tab loads, open the article editor
    setTimeout(() => editArticle(articleId), 500);
}

// ═══════════════════════════════════════════════════════════
//  Watched Pages (part of Sources tab)
// ═══════════════════════════════════════════════════════════

const INTERVAL_OPTIONS = [
    { value: 6, label: () => t('sources.intervalHours.6') },
    { value: 12, label: () => t('sources.intervalHours.12') },
    { value: 24, label: () => t('sources.intervalHours.24') },
    { value: 168, label: () => t('sources.intervalHours.168') },
];

function intervalLabel(hours) {
    const opt = INTERVAL_OPTIONS.find(o => o.value === hours);
    return opt ? opt.label() : `${hours}h`;
}

async function loadWatchedPages() {
    const container = document.getElementById('watchedPagesContainer');
    if (!container) return;
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    try {
        const data = await api('/admin/scraper/watched-pages');
        const pages = data.pages || [];

        // Build add form
        const cats = _categories.length ? _categories : [
            { value: 'delivery', label: 'delivery' }, { value: 'warranty', label: 'warranty' },
            { value: 'returns', label: 'returns' }, { value: 'policies', label: 'policies' },
            { value: 'general', label: 'general' },
        ];
        const categoryOptions = `<option value="" disabled selected>${t('common.select')}</option>`
            + cats.map(c => `<option value="${c.value}">${escapeHtml(c.label)}</option>`).join('');

        const intervalOptions = `<option value="" disabled selected>${t('common.select')}</option>`
            + INTERVAL_OPTIONS.map(o => `<option value="${o.value}">${o.label()}</option>`).join('');

        let html = `
            <div class="flex flex-wrap items-end gap-2 mb-4">
                <div class="flex-1 min-w-[250px]">
                    <label class="block text-xs ${tw.mutedText} mb-1">${t('sources.watchedUrl')}</label>
                    <input type="text" id="watchedPageUrl" placeholder="${t('sources.urlPlaceholder')}" class="w-full border rounded px-2 py-1.5 text-sm dark:bg-gray-700 dark:border-gray-600">
                </div>
                <div>
                    <label class="block text-xs ${tw.mutedText} mb-1">${t('sources.watchedCategory')}</label>
                    <select id="watchedPageCategory" class="border rounded px-2 py-1.5 text-sm dark:bg-gray-700 dark:border-gray-600">${categoryOptions}</select>
                </div>
                <div>
                    <label class="block text-xs ${tw.mutedText} mb-1">${t('sources.watchedInterval')}</label>
                    <select id="watchedPageInterval" class="border rounded px-2 py-1.5 text-sm dark:bg-gray-700 dark:border-gray-600">${intervalOptions}</select>
                </div>
                <div>
                    <label class="flex items-center gap-1.5 text-sm cursor-pointer pt-4">
                        <input type="checkbox" id="watchedPageDiscovery">
                        <span>${t('sources.discoveryLabel')}</span>
                        <span class="${tw.mutedText} text-xs cursor-help" title="${t('sources.discoveryHint')}">(?)</span>
                    </label>
                </div>
                <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.training.addWatchedPage()">${t('sources.addWatchedPage')}</button>
            </div>`;

        if (pages.length === 0) {
            html += `<div class="${tw.emptyState}">${t('sources.noWatchedPages')}</div>`;
        } else {
            // Separate parent and child pages
            const parentPages = pages.filter(p => !p.parent_id);
            const childPages = pages.filter(p => p.parent_id);
            const childrenByParent = {};
            for (const c of childPages) {
                const pid = c.parent_id;
                if (!childrenByParent[pid]) childrenByParent[pid] = [];
                childrenByParent[pid].push(c);
            }

            let rows = '';
            for (const p of parentPages) {
                const isDiscovery = p.is_discovery;
                const children = childrenByParent[p.id] || [];
                rows += `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}">
                        <a href="${escapeHtml(p.url)}" target="_blank" class="text-blue-600 dark:text-blue-400 hover:underline text-xs">${escapeHtml((p.url || '').replace(/^https?:\/\//, '').substring(0, 50))}</a>
                        ${isDiscovery ? `<br><span class="${tw.badgeYellow} text-xs">${t('sources.discoveryBadge')}</span> <span class="${tw.mutedText} text-xs">${t('sources.childCount', {count: p.child_count || 0})}</span>` : ''}
                    </td>
                    <td class="${tw.td}"><span class="${tw.badgeBlue}">${escapeHtml(p.article_category || '-')}</span></td>
                    <td class="${tw.td}">
                        <select class="border rounded px-1 py-0.5 text-xs dark:bg-gray-700 dark:border-gray-600"
                            onchange="window._pages.training.updateWatchedInterval('${p.id}', parseInt(this.value))">
                            ${INTERVAL_OPTIONS.map(o => `<option value="${o.value}" ${o.value === p.rescrape_interval_hours ? 'selected' : ''}>${o.label()}</option>`).join('')}
                        </select>
                    </td>
                    <td class="${tw.td}">${sourceStatusBadge(p.status)}</td>
                    <td class="${tw.td}">${p.fetched_at ? formatDate(p.fetched_at) : `<span class="${tw.mutedText} text-xs">${t('sources.neverScraped')}</span>`}</td>
                    <td class="${tw.td}">${p.next_scrape_at ? formatDate(p.next_scrape_at) : '-'}</td>
                    <td class="${tw.td}">
                        <div class="flex flex-wrap gap-1">
                            ${!isDiscovery && p.article_id ? `<button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.training.viewSourceArticle('${p.article_id}')">${t('sources.watchedArticle')}</button>` : ''}
                            <button class="${tw.btnGreen} ${tw.btnSm}" onclick="window._pages.training.scrapeWatchedNow('${p.id}')">${t('sources.scrapeNow')}</button>
                            <button class="${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.training.deleteWatchedPage('${p.id}')">${t('sources.deleteWatched')}</button>
                        </div>
                    </td>
                </tr>`;
                // Render children indented
                for (const c of children) {
                    rows += `
                    <tr class="${tw.trHover} opacity-80">
                        <td class="${tw.td} pl-8">
                            <span class="${tw.mutedText}">↳</span>
                            <a href="${escapeHtml(c.url)}" target="_blank" class="text-blue-600 dark:text-blue-400 hover:underline text-xs">${escapeHtml((c.url || '').replace(/^https?:\/\//, '').substring(0, 50))}</a>
                        </td>
                        <td class="${tw.td}"><span class="${tw.badgeBlue}">${escapeHtml(c.article_category || '-')}</span></td>
                        <td class="${tw.td}"><span class="text-xs">${intervalLabel(c.rescrape_interval_hours)}</span></td>
                        <td class="${tw.td}">${sourceStatusBadge(c.status)}</td>
                        <td class="${tw.td}">${c.fetched_at ? formatDate(c.fetched_at) : `<span class="${tw.mutedText} text-xs">${t('sources.neverScraped')}</span>`}</td>
                        <td class="${tw.td}">${c.next_scrape_at ? formatDate(c.next_scrape_at) : '-'}</td>
                        <td class="${tw.td}">
                            <div class="flex flex-wrap gap-1">
                                ${c.article_id ? `<button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.training.viewSourceArticle('${c.article_id}')">${t('sources.watchedArticle')}</button>` : ''}
                            </div>
                        </td>
                    </tr>`;
                }
            }

            html += `
            <div class="overflow-x-auto"><table class="${tw.table}" id="watchedPagesTable"><thead><tr>
                <th class="${tw.th}">${t('sources.watchedUrl')}</th>
                <th class="${tw.th}">${t('sources.watchedCategory')}</th>
                <th class="${tw.th}">${t('sources.watchedInterval')}</th>
                <th class="${tw.th}">${t('sources.watchedStatus')}</th>
                <th class="${tw.th}">${t('sources.watchedLastScraped')}</th>
                <th class="${tw.th}">${t('sources.watchedNextScrape')}</th>
                <th class="${tw.th}">${t('sources.watchedActions')}</th>
            </tr></thead><tbody>
            ${rows}
            </tbody></table></div>`;
        }

        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('sources.watchedFailedToLoad', {error: escapeHtml(e.message)})}
            <br><button class="${tw.btnPrimary} ${tw.btnSm} mt-2" onclick="window._pages.training.loadWatchedPages()">${t('common.retry')}</button></div>`;
    }
}

async function addWatchedPage() {
    const url = document.getElementById('watchedPageUrl')?.value?.trim();
    const category = document.getElementById('watchedPageCategory')?.value;
    const intervalRaw = document.getElementById('watchedPageInterval')?.value;
    const interval = parseInt(intervalRaw, 10);
    const is_discovery = document.getElementById('watchedPageDiscovery')?.checked || false;
    if (!url) { showToast(t('sources.urlRequired'), 'error'); return; }
    if (!category) { showToast(t('sources.categoryRequired'), 'error'); return; }
    if (!intervalRaw) { showToast(t('sources.intervalRequired'), 'error'); return; }

    try {
        await api('/admin/scraper/watched-pages', {
            method: 'POST',
            body: JSON.stringify({ url, category, rescrape_interval_hours: interval, is_discovery }),
        });
        showToast(t('sources.watchedPageAdded'));
        document.getElementById('watchedPageUrl').value = '';
        loadWatchedPages();
    } catch (e) { showToast(t('sources.watchedPageAddFailed', {error: e.message}), 'error'); }
}

async function updateWatchedInterval(pageId, interval) {
    try {
        await api(`/admin/scraper/watched-pages/${pageId}`, {
            method: 'PATCH',
            body: JSON.stringify({ rescrape_interval_hours: interval }),
        });
        showToast(t('sources.watchedPageUpdated'));
    } catch (e) { showToast(t('sources.watchedPageUpdateFailed', {error: e.message}), 'error'); }
}

async function scrapeWatchedNow(pageId) {
    try {
        showToast(t('sources.scrapeNowRunning'), 'info');
        const result = await api(`/admin/scraper/watched-pages/${pageId}/scrape-now`, { method: 'POST' });
        // Show result summary
        if (result.status === 'unchanged') {
            showToast(t('sources.scrapeNowUnchanged'));
        } else if (result.status === 'ok' && result.discovered !== undefined) {
            // Discovery page result
            showToast(t('sources.scrapeNowDiscoveryDone', {discovered: result.discovered, created: result.created || 0, updated: result.updated || 0}));
        } else {
            showToast(t('sources.scrapeNowDone', {status: result.status || 'ok'}));
        }
        loadWatchedPages();
    } catch (e) { showToast(t('sources.scrapeNowFailed', {error: e.message}), 'error'); }
}

async function deleteWatchedPage(pageId) {
    if (!confirm(t('sources.watchedPageDeleteConfirm'))) return;
    try {
        await api(`/admin/scraper/watched-pages/${pageId}`, { method: 'DELETE' });
        showToast(t('sources.watchedPageDeleted'));
        loadWatchedPages();
    } catch (e) { showToast(t('sources.watchedPageDeleteFailed', {error: e.message}), 'error'); }
}

// ═══════════════════════════════════════════════════════════
//  A/B Tests (under Prompts tab)
// ═══════════════════════════════════════════════════════════
function abStatusBadge(status) {
    switch (status) {
        case 'active': return `<span class="${tw.badgeGreen}">${escapeHtml(status)}</span>`;
        case 'completed': return `<span class="${tw.badgeBlue}">${escapeHtml(status)}</span>`;
        case 'stopped': return `<span class="${tw.badgeRed}">${escapeHtml(status)}</span>`;
        default: return `<span class="${tw.badge}">${escapeHtml(status || 'unknown')}</span>`;
    }
}

let _promptVersionsCache = [];

function _significanceBadge(sig) {
    if (!sig) return '';
    if (sig.min_samples_needed) {
        return `<span class="${tw.badge}">${t('prompts.notEnoughData')}</span>`;
    }
    if (sig.is_significant && sig.recommended_variant) {
        return `<span class="${tw.badgeGreen}">${t('prompts.recommended', {variant: sig.recommended_variant})}</span>`;
    }
    return `<span class="${tw.badge}">${t('prompts.noSignificance')}</span>`;
}

function _significanceText(sig) {
    if (!sig) return '';
    if (sig.min_samples_needed) return t('prompts.notEnoughData');
    if (sig.is_significant && sig.recommended_variant) {
        return t('prompts.recommended', {variant: sig.recommended_variant});
    }
    return t('prompts.noSignificance');
}

async function loadABTests() {
    const container = document.getElementById('trainingContent-prompts');
    const existing = document.getElementById('abTestsSection');
    if (existing) existing.remove();

    const section = document.createElement('div');
    section.id = 'abTestsSection';
    section.className = 'mt-6';
    container.appendChild(section);

    try {
        const data = await api('/prompts/ab-tests');
        const tests = data.tests || [];

        let html = `<h3 class="text-base font-semibold text-neutral-900 dark:text-neutral-50 mb-3">${t('prompts.abTests')}</h3>`;
        html += `<div class="mb-4"><button class="${tw.btnPrimary}" onclick="window._pages.training.showCreateABTest()">${t('prompts.newABTest')}</button></div>`;

        if (tests.length === 0) {
            html += `<div class="${tw.emptyState}">${t('prompts.noABTests')}</div>`;
        } else {
            html += `<div class="overflow-x-auto"><table class="${tw.table}" id="abTestsTable"><thead><tr>
                <th class="${tw.th}">${t('prompts.testName')}</th>
                <th class="${tw.th}">${t('prompts.variantA')}</th>
                <th class="${tw.th}">${t('prompts.variantB')}</th>
                <th class="${tw.th}">${t('prompts.callsAB')}</th>
                <th class="${tw.th}">${t('prompts.qualityAB')}</th>
                <th class="${tw.th}">${t('prompts.abStatus')}</th>
                <th class="${tw.th}">${t('prompts.abAction')}</th>
            </tr></thead><tbody>`;

            for (const test of tests) {
                const qualityA = test.quality_a != null ? Number(test.quality_a).toFixed(2) : '—';
                const qualityB = test.quality_b != null ? Number(test.quality_b).toFixed(2) : '—';

                let actionHtml = '';
                if (test.status === 'active') {
                    actionHtml = `<button class="${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.training.stopABTest('${test.id}')">${t('prompts.stopBtn')}</button>`;
                } else {
                    actionHtml = (test.significance ? _significanceBadge(test.significance) + ' ' : '')
                        + `<button class="${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.training.deleteABTest('${test.id}', '${escapeHtml(test.test_name).replace(/'/g, "\\'")}')">${t('common.delete')}</button>`;
                }

                html += `<tr class="${tw.trHover}">
                    <td class="${tw.td}"><a href="#" class="text-blue-600 dark:text-blue-400 hover:underline" onclick="event.preventDefault(); window._pages.training.showABReport('${test.id}')">${escapeHtml(test.test_name)}</a></td>
                    <td class="${tw.td}">${escapeHtml(test.variant_a_name || '')}</td>
                    <td class="${tw.td}">${escapeHtml(test.variant_b_name || '')}</td>
                    <td class="${tw.td}">${test.calls_a || 0} / ${test.calls_b || 0}</td>
                    <td class="${tw.td}">${qualityA} / ${qualityB}</td>
                    <td class="${tw.td}">${abStatusBadge(test.status)}</td>
                    <td class="${tw.td}">${actionHtml}</td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }

        section.innerHTML = html;
    } catch (e) {
        section.innerHTML = `<div class="${tw.emptyState}">${t('prompts.failedToLoadAB', {error: escapeHtml(e.message)})}</div>`;
    }
}

function showCreateABTest() {
    document.getElementById('abTestName').value = '';
    document.getElementById('abVariantA').value = '';
    document.getElementById('abVariantB').value = '';
    document.getElementById('abTrafficSplit').value = '50';
    document.getElementById('abTrafficSplitValue').textContent = '50%';

    // Populate selects with cached prompt versions
    const selA = document.getElementById('abVariantA');
    const selB = document.getElementById('abVariantB');
    selA.innerHTML = `<option value="">${t('common.select')}</option>`;
    selB.innerHTML = `<option value="">${t('common.select')}</option>`;
    for (const v of _promptVersionsCache) {
        const opt = `<option value="${v.id}">${escapeHtml(v.name)}</option>`;
        selA.innerHTML += opt;
        selB.innerHTML += opt;
    }

    document.getElementById('createABTestModal').classList.add('show');
}

async function createABTest() {
    const testName = document.getElementById('abTestName').value.trim();
    const variantAId = document.getElementById('abVariantA').value;
    const variantBId = document.getElementById('abVariantB').value;
    const trafficSplit = parseInt(document.getElementById('abTrafficSplit').value, 10) / 100;

    if (!testName) { showToast(t('prompts.testNameRequired'), 'error'); return; }
    if (!variantAId || !variantBId) { showToast(t('prompts.variantsRequired'), 'error'); return; }
    if (variantAId === variantBId) { showToast(t('prompts.variantsMustDiffer'), 'error'); return; }

    try {
        await api('/prompts/ab-tests', {
            method: 'POST',
            body: JSON.stringify({
                test_name: testName,
                variant_a_id: variantAId,
                variant_b_id: variantBId,
                traffic_split: trafficSplit,
            }),
        });
        closeModal('createABTestModal');
        showToast(t('prompts.abTestCreated'));
        loadABTests();
    } catch (e) { showToast(t('prompts.abTestCreateFailed', {error: e.message}), 'error'); }
}

async function stopABTest(id) {
    if (!confirm(t('prompts.stopConfirm'))) return;
    try {
        const data = await api(`/prompts/ab-tests/${id}/stop`, { method: 'PATCH' });
        const resultText = _significanceText(data.test?.significance);
        if (resultText) {
            showToast(t('prompts.stoppedWithResult', {result: resultText}));
        } else {
            showToast(t('prompts.stopped'));
        }
        loadABTests();
    } catch (e) { showToast(t('prompts.stopFailed', {error: e.message}), 'error'); }
}

async function deleteABTest(id, name) {
    if (!confirm(t('prompts.deleteABTestConfirm', {name}))) return;
    try {
        await api(`/prompts/ab-tests/${id}`, { method: 'DELETE' });
        showToast(t('prompts.abTestDeleted'));
        loadABTests();
    } catch (e) { showToast(t('prompts.abTestDeleteFailed', {error: e.message}), 'error'); }
}

// ═══════════════════════════════════════════════════════════
//  A/B Test Report
// ═══════════════════════════════════════════════════════════
const CRITERION_I18N = {
    accuracy: 'prompts.criterionAccuracy',
    completeness: 'prompts.criterionCompleteness',
    politeness: 'prompts.criterionPoliteness',
    response_time: 'prompts.criterionResponseTime',
    problem_resolution: 'prompts.criterionProblemResolution',
    language_quality: 'prompts.criterionLanguageQuality',
    tool_usage: 'prompts.criterionToolUsage',
    scenario_adherence: 'prompts.criterionScenarioAdherence',
};

async function showABReport(testId) {
    const container = document.getElementById('abReportContent');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;
    document.getElementById('abReportModal').classList.add('show');

    try {
        const report = await api(`/prompts/ab-tests/${testId}/report`);
        const test = report.test || {};
        const summary = report.summary || {};
        const perCriterion = report.per_criterion || [];
        const byScenario = report.by_scenario || [];
        const daily = report.daily || [];

        const titleEl = document.getElementById('abReportModalTitle');
        if (titleEl) titleEl.textContent = `${t('prompts.abReportTitle')}: ${test.test_name || ''}`;

        const fmtPct = (v) => v != null ? (v * 100).toFixed(1) + '%' : '—';
        const fmtSec = (v) => v != null ? v.toFixed(0) + 's' : '—';
        const sig = summary.significance || {};

        let html = '';

        // Summary cards
        html += `<h3 class="text-sm font-semibold text-neutral-700 dark:text-neutral-300 mb-2">${t('prompts.reportSummary')}</h3>`;
        html += `<div class="${tw.statsGrid}">
            <div class="${tw.card}">
                <div class="${tw.statValue}">${summary.calls_a || 0} / ${summary.calls_b || 0}</div>
                <div class="${tw.statLabel}">${t('prompts.reportCalls')} (A / B)</div>
            </div>
            <div class="${tw.card}">
                <div class="${tw.statValue}">${fmtPct(summary.quality_a)} / ${fmtPct(summary.quality_b)}</div>
                <div class="${tw.statLabel}">${t('prompts.reportQuality')} (A / B)</div>
            </div>
            <div class="${tw.card}">
                <div class="${tw.statValue}">${fmtSec(summary.avg_duration_a)} / ${fmtSec(summary.avg_duration_b)}</div>
                <div class="${tw.statLabel}">${t('prompts.reportAvgDuration')} (A / B)</div>
            </div>
            <div class="${tw.card}">
                <div class="${tw.statValue}">${fmtPct(summary.transfer_rate_a)} / ${fmtPct(summary.transfer_rate_b)}</div>
                <div class="${tw.statLabel}">${t('prompts.reportTransferRate')} (A / B)</div>
            </div>
        </div>`;

        // Significance badge
        if (sig.is_significant != null) {
            html += `<div class="mb-4">${_significanceBadge(sig)} <span class="${tw.mutedText} ml-2">p=${sig.p_value_approx ?? '—'}, z=${sig.z_score ?? '—'}</span></div>`;
        }

        // Per-criterion table
        if (perCriterion.length > 0) {
            html += `<h3 class="text-sm font-semibold text-neutral-700 dark:text-neutral-300 mb-2 mt-4">${t('prompts.reportCriteria')}</h3>`;
            html += `<div class="overflow-x-auto mb-4"><table class="${tw.table}"><thead><tr>
                <th class="${tw.th}">${t('prompts.reportCriterion')}</th>
                <th class="${tw.th}">${test.variant_a_name || 'A'}</th>
                <th class="${tw.th}">${test.variant_b_name || 'B'}</th>
                <th class="${tw.th}">${t('prompts.reportDelta')}</th>
            </tr></thead><tbody>`;
            for (const cr of perCriterion) {
                const delta = (cr.avg_a != null && cr.avg_b != null) ? cr.avg_a - cr.avg_b : null;
                const deltaStr = delta != null ? (delta >= 0 ? '+' : '') + (delta * 100).toFixed(1) + '%' : '—';
                const deltaClass = delta != null ? (delta > 0 ? 'text-emerald-600 dark:text-emerald-400' : delta < 0 ? 'text-red-600 dark:text-red-400' : '') : '';
                html += `<tr class="${tw.trHover}">
                    <td class="${tw.td}">${t(CRITERION_I18N[cr.criterion] || cr.criterion)}</td>
                    <td class="${tw.td}">${qualityBadge(cr.avg_a)}</td>
                    <td class="${tw.td}">${qualityBadge(cr.avg_b)}</td>
                    <td class="${tw.td}"><span class="${deltaClass} font-medium">${deltaStr}</span></td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }

        // By scenario table
        if (byScenario.length > 0) {
            html += `<h3 class="text-sm font-semibold text-neutral-700 dark:text-neutral-300 mb-2 mt-4">${t('prompts.reportScenarios')}</h3>`;
            html += `<div class="overflow-x-auto mb-4"><table class="${tw.table}"><thead><tr>
                <th class="${tw.th}">${t('prompts.reportScenario')}</th>
                <th class="${tw.th}">${t('prompts.reportCalls')} A</th>
                <th class="${tw.th}">${t('prompts.reportCalls')} B</th>
                <th class="${tw.th}">${t('prompts.reportQuality')} A</th>
                <th class="${tw.th}">${t('prompts.reportQuality')} B</th>
            </tr></thead><tbody>`;
            for (const sc of byScenario) {
                html += `<tr class="${tw.trHover}">
                    <td class="${tw.td}"><span class="${tw.badgeBlue}">${escapeHtml(sc.scenario)}</span></td>
                    <td class="${tw.td}">${sc.calls_a}</td>
                    <td class="${tw.td}">${sc.calls_b}</td>
                    <td class="${tw.td}">${qualityBadge(sc.quality_a)}</td>
                    <td class="${tw.td}">${qualityBadge(sc.quality_b)}</td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }

        // Daily dynamics with CSS bars
        if (daily.length > 0) {
            html += `<h3 class="text-sm font-semibold text-neutral-700 dark:text-neutral-300 mb-2 mt-4">${t('prompts.reportDaily')}</h3>`;
            html += '<div class="space-y-2 mb-4">';
            for (const d of daily) {
                const qa = d.quality_a != null ? d.quality_a : 0;
                const qb = d.quality_b != null ? d.quality_b : 0;
                html += `<div class="flex items-center gap-2 text-xs">
                    <span class="w-20 text-neutral-500 dark:text-neutral-400 shrink-0">${escapeHtml(d.date)}</span>
                    <div class="flex-1 flex flex-col gap-0.5">
                        <div class="flex items-center gap-1">
                            <span class="w-4 text-right text-neutral-400">A</span>
                            <div class="h-3 rounded bg-blue-500 dark:bg-blue-400" style="width:${Math.max(qa * 100, 2)}%"></div>
                            <span class="${tw.mutedText}">${fmtPct(d.quality_a)} (${d.calls_a})</span>
                        </div>
                        <div class="flex items-center gap-1">
                            <span class="w-4 text-right text-neutral-400">B</span>
                            <div class="h-3 rounded bg-violet-500 dark:bg-violet-400" style="width:${Math.max(qb * 100, 2)}%"></div>
                            <span class="${tw.mutedText}">${fmtPct(d.quality_b)} (${d.calls_b})</span>
                        </div>
                    </div>
                </div>`;
            }
            html += '</div>';
        }

        // Export button
        html += `<div class="mt-4"><button class="${tw.btnPrimary}" onclick="window._pages.training.exportABReportCSV('${testId}')">${t('prompts.exportCSV')}</button></div>`;

        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('prompts.reportFailedToLoad', {error: escapeHtml(e.message)})}</div>`;
    }
}

async function exportABReportCSV(testId) {
    try {
        const res = await fetchWithAuth(`/prompts/ab-tests/${testId}/report/csv`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        downloadBlob(blob, `ab_report_${testId}.csv`);
        showToast(t('prompts.csvExported'));
    } catch (e) {
        showToast(t('prompts.exportFailed', {error: e.message}), 'error');
    }
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
    viewPrompt, loadDefaultPrompt, resetToDefault, deletePrompt,
    // A/B Tests
    loadABTests, showCreateABTest, createABTest, stopABTest, deleteABTest, showABReport, exportABReportCSV,
    // Knowledge
    loadArticles, showCreateArticle, editArticle, saveArticle,
    toggleArticle, deleteArticle, reindexArticle, reindexAll,
    showImportModal, importDocuments,
    // Templates
    loadTemplates, showCreateTemplate, addVariant, editTemplate, saveTemplate, deleteTemplate,
    // Dialogues
    loadDialogues, showCreateDialogue, editDialogue, saveDialogue, deleteDialogue,
    // Safety
    loadSafetyRules, showCreateSafetyRule, editSafetyRule, saveSafetyRule, deleteSafetyRule,
    // Tools
    loadTools, editToolOverride, saveToolOverride, resetToolOverride,
    // Source configs (multi-source)
    loadSourceConfigs, showAddSourceConfig, editSourceConfig, saveSourceConfig,
    toggleSourceConfigEnabled, runSourceConfig, deleteSourceConfig,
    // Sources (scraper)
    loadSources, toggleScraperEnabled, toggleAutoApprove, toggleDedupLlm, updateSchedule, updateMinDate, updateMaxDate,
    runScraperNow, approveSource, rejectSource, viewSourceArticle,
    // Watched pages
    loadWatchedPages, addWatchedPage, updateWatchedInterval, scrapeWatchedNow, deleteWatchedPage,
};
