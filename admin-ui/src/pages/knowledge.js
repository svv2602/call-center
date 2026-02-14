import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { formatDate, escapeHtml, closeModal } from '../utils.js';
import { registerPageLoader } from '../router.js';

async function loadArticles() {
    const container = document.getElementById('articlesContainer');
    container.innerHTML = '<div class="loading-overlay"><div class="spinner"></div></div>';

    const params = new URLSearchParams();
    const cat = document.getElementById('kbCategory').value;
    const search = document.getElementById('kbSearch').value;
    if (cat) params.set('category', cat);
    if (search) params.set('search', search);

    try {
        const data = await api(`/knowledge/articles?${params}`);
        const articles = data.articles || [];
        if (articles.length === 0) {
            container.innerHTML = '<div class="empty-state">No articles found</div>';
            return;
        }
        container.innerHTML = `
            <table><thead><tr><th>Title</th><th>Category</th><th>Active</th><th>Updated</th><th>Actions</th></tr></thead><tbody>
            ${articles.map(a => `
                <tr>
                    <td>${escapeHtml(a.title)}</td>
                    <td><span class="badge badge-blue">${escapeHtml(a.category)}</span></td>
                    <td>${a.active !== false ? '<span class="badge badge-green">Yes</span>' : '<span class="badge badge-red">No</span>'}</td>
                    <td>${formatDate(a.updated_at || a.created_at)}</td>
                    <td>
                        <button class="btn btn-primary btn-sm" onclick="window._pages.knowledge.editArticle('${a.id}')">Edit</button>
                        <button class="btn btn-sm" style="background:#64748b;color:#fff" onclick="window._pages.knowledge.toggleArticle('${a.id}', ${a.active !== false})">${a.active !== false ? 'Deactivate' : 'Activate'}</button>
                        <button class="btn btn-danger btn-sm" onclick="window._pages.knowledge.deleteArticle('${a.id}', '${escapeHtml(a.title).replace(/'/g, "\\'")}')">Delete</button>
                    </td>
                </tr>
            `).join('')}
            </tbody></table>
            <p style="margin-top:.5rem;font-size:.8rem;color:#64748b">Total: ${data.total} articles</p>
        `;
    } catch (e) {
        container.innerHTML = `<div class="empty-state">Failed to load articles: ${escapeHtml(e.message)}
            <br><button class="btn btn-primary btn-sm" onclick="window._pages.knowledge.loadArticles()" style="margin-top:.5rem">Retry</button></div>`;
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

export function init() {
    registerPageLoader('knowledge', () => loadArticles());
}

window._pages = window._pages || {};
window._pages.knowledge = { loadArticles, showCreateArticle, editArticle, saveArticle, toggleArticle, deleteArticle };
