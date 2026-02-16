import { api, apiUpload } from '../api.js';
import { showToast } from '../notifications.js';
import { formatDate, escapeHtml, closeModal } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import * as tw from '../tw.js';

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

let _categories = [];

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

async function loadArticles() {
    const container = document.getElementById('articlesContainer');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    const params = new URLSearchParams();
    const cat = document.getElementById('kbCategory').value;
    const search = document.getElementById('kbSearch').value;
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
                            <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.knowledge.editArticle('${a.id}')">${t('knowledge.editBtn')}</button>
                            <button class="${tw.btnGreen} ${tw.btnSm}" onclick="window._pages.knowledge.reindexArticle('${a.id}')">${t('knowledge.reindexBtn')}</button>
                            <button class="${tw.btnSecondary} ${tw.btnSm}" onclick="window._pages.knowledge.toggleArticle('${a.id}', ${a.active !== false})">${a.active !== false ? t('common.deactivate') : t('common.activate')}</button>
                            <button class="${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.knowledge.deleteArticle('${a.id}', '${escapeHtml(a.title).replace(/'/g, "\\'")}')">×</button>
                        </div>
                    </td>
                </tr>
            `).join('')}
            </tbody></table></div>
            <p class="${tw.mutedText} mt-2">${t('knowledge.total', {count: data.total})}</p>
        `;
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('knowledge.failedToLoad', {error: escapeHtml(e.message)})}
            <br><button class="${tw.btnPrimary} ${tw.btnSm} mt-2" onclick="window._pages.knowledge.loadArticles()">${t('common.retry')}</button></div>`;
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

    if (!files || files.length === 0) {
        showToast(t('knowledge.importNoFiles'), 'error');
        return;
    }

    const formData = new FormData();
    for (const file of files) {
        formData.append('files', file);
    }

    let url = '/knowledge/articles/import';
    const cat = categorySelect ? categorySelect.value : '';
    if (cat) url += `?category=${encodeURIComponent(cat)}`;

    try {
        const data = await apiUpload(url, formData);
        const msgs = [t('knowledge.importResult', {imported: data.imported})];
        if (data.errors > 0) msgs.push(t('knowledge.importErrors', {errors: data.errors}));
        showToast(msgs.join(', '), data.errors > 0 ? 'warning' : 'success');
        if (data.error_details && data.error_details.length > 0) {
            for (const err of data.error_details) {
                showToast(`${err.filename}: ${err.error}`, 'error');
            }
        }
        closeModal('importDocumentsModal');
        loadArticles();
    } catch (e) { showToast(t('knowledge.importFailed', {error: e.message}), 'error'); }
}

export function init() {
    registerPageLoader('knowledge', async () => {
        await loadCategories();
        await loadArticles();
    });
}

window._pages = window._pages || {};
window._pages.knowledge = {
    loadArticles, showCreateArticle, editArticle, saveArticle,
    toggleArticle, deleteArticle, reindexArticle, reindexAll,
    showImportModal, importDocuments,
};
