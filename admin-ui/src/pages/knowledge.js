import { api, apiUpload } from '../api.js';
import { showToast } from '../notifications.js';
import { formatDate, escapeHtml, closeModal } from '../utils.js';
import { registerPageLoader } from '../router.js';
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
        opt.textContent = 'All categories';
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
            container.innerHTML = `<div class="${tw.emptyState}">No articles found</div>`;
            return;
        }
        container.innerHTML = `
            <div class="overflow-x-auto"><table class="${tw.table}"><thead><tr><th class="${tw.th}">Title</th><th class="${tw.th}">Category</th><th class="${tw.th}">Embedding</th><th class="${tw.th}">Active</th><th class="${tw.th}">Updated</th><th class="${tw.th}">Actions</th></tr></thead><tbody>
            ${articles.map(a => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}">${escapeHtml(a.title)}</td>
                    <td class="${tw.td}"><span class="${tw.badgeBlue}">${escapeHtml(a.category)}</span></td>
                    <td class="${tw.td}">${embeddingBadge(a.embedding_status)}</td>
                    <td class="${tw.td}">${a.active !== false ? `<span class="${tw.badgeGreen}">Yes</span>` : `<span class="${tw.badgeRed}">No</span>`}</td>
                    <td class="${tw.td}">${formatDate(a.updated_at || a.created_at)}</td>
                    <td class="${tw.td}">
                        <div class="flex flex-wrap gap-1">
                            <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.knowledge.editArticle('${a.id}')">Edit</button>
                            <button class="${tw.btnGreen} ${tw.btnSm}" onclick="window._pages.knowledge.reindexArticle('${a.id}')">Reindex</button>
                            <button class="${tw.btnSecondary} ${tw.btnSm}" onclick="window._pages.knowledge.toggleArticle('${a.id}', ${a.active !== false})">${a.active !== false ? 'Deactivate' : 'Activate'}</button>
                            <button class="${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.knowledge.deleteArticle('${a.id}', '${escapeHtml(a.title).replace(/'/g, "\\'")}')">Delete</button>
                        </div>
                    </td>
                </tr>
            `).join('')}
            </tbody></table></div>
            <p class="${tw.mutedText} mt-2">Total: ${data.total} articles</p>
        `;
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">Failed to load articles: ${escapeHtml(e.message)}
            <br><button class="${tw.btnPrimary} ${tw.btnSm} mt-2" onclick="window._pages.knowledge.loadArticles()">Retry</button></div>`;
    }
}

function showCreateArticle() {
    document.getElementById('articleModalTitle').textContent = 'New Article';
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
        document.getElementById('articleModalTitle').textContent = 'Edit Article';
        document.getElementById('editArticleId').value = id;
        document.getElementById('articleTitle').value = a.title || '';
        document.getElementById('articleCategory').value = a.category || 'faq';
        document.getElementById('articleContent').value = a.content || '';
        document.getElementById('createArticleModal').classList.add('show');
    } catch (e) { showToast('Failed to load article: ' + e.message, 'error'); }
}

async function saveArticle() {
    const id = document.getElementById('editArticleId').value;
    const title = document.getElementById('articleTitle').value.trim();
    const category = document.getElementById('articleCategory').value;
    const content = document.getElementById('articleContent').value.trim();
    if (!title || !content) { showToast('Title and content are required', 'error'); return; }
    try {
        if (id) {
            await api(`/knowledge/articles/${id}`, { method: 'PATCH', body: JSON.stringify({ title, category, content }) });
            showToast('Article updated');
        } else {
            await api('/knowledge/articles', { method: 'POST', body: JSON.stringify({ title, category, content }) });
            showToast('Article created');
        }
        closeModal('createArticleModal');
        loadArticles();
    } catch (e) { showToast('Failed to save article: ' + e.message, 'error'); }
}

async function toggleArticle(id, currentlyActive) {
    try {
        await api(`/knowledge/articles/${id}`, { method: 'PATCH', body: JSON.stringify({ active: !currentlyActive }) });
        showToast(currentlyActive ? 'Article deactivated' : 'Article activated');
        loadArticles();
    } catch (e) { showToast('Failed to update article: ' + e.message, 'error'); }
}

async function deleteArticle(id, title) {
    if (!confirm(`Delete article "${title}"?`)) return;
    try {
        await api(`/knowledge/articles/${id}`, { method: 'DELETE' });
        showToast('Article deleted');
        loadArticles();
    } catch (e) { showToast('Failed to delete article: ' + e.message, 'error'); }
}

async function reindexArticle(id) {
    try {
        const data = await api(`/knowledge/articles/${id}/reindex`, { method: 'POST' });
        showToast(data.message || 'Reindex queued');
        loadArticles();
    } catch (e) { showToast('Reindex failed: ' + e.message, 'error'); }
}

async function reindexAll() {
    if (!confirm('Reindex all articles? This may take a while.')) return;
    try {
        const data = await api('/knowledge/reindex-all', { method: 'POST' });
        showToast(data.message || 'Reindex-all dispatched');
        loadArticles();
    } catch (e) { showToast('Reindex-all failed: ' + e.message, 'error'); }
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
        showToast('Please select files to import', 'error');
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
        const msgs = [`Imported: ${data.imported}`];
        if (data.errors > 0) msgs.push(`Errors: ${data.errors}`);
        showToast(msgs.join(', '), data.errors > 0 ? 'warning' : 'success');
        if (data.error_details && data.error_details.length > 0) {
            for (const err of data.error_details) {
                showToast(`${err.filename}: ${err.error}`, 'error');
            }
        }
        closeModal('importDocumentsModal');
        loadArticles();
    } catch (e) { showToast('Import failed: ' + e.message, 'error'); }
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
