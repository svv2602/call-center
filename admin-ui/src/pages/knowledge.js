import { api, apiUpload } from '../api.js';
import { showToast } from '../notifications.js';
import { formatDate, escapeHtml, closeModal } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import { makeSortable } from '../sorting.js';
import { renderPagination, buildParams } from '../pagination.js';
import * as tw from '../tw.js';

// ─── State ───────────────────────────────────────────────────
let _categories = [];
let _activeTab = 'articles';
let _articlesOffset = 0;
let _sourcesOffset = 0;
let _sourceConfigsOffset = 0;
let _watchedOffset = 0;
let _watchedTenantId = '';  // tenant filter for watched pages
let _tenantsList = [];      // cached tenants list

// ─── Internal tab switching ──────────────────────────────────
function showTab(tab) {
    _activeTab = tab;
    const tabs = ['articles', 'source-configs', 'scraper', 'watched'];
    tabs.forEach(t => {
        const el = document.getElementById(`knowledgeContent-${t}`);
        if (el) el.style.display = t === tab ? 'block' : 'none';
    });
    document.querySelectorAll('#page-knowledge .tab-bar button').forEach(b => b.classList.remove('active'));
    const activeBtn = document.querySelector(`#page-knowledge .tab-bar button[data-tab="${tab}"]`);
    if (activeBtn) activeBtn.classList.add('active');

    _articlesOffset = 0;
    _sourcesOffset = 0;
    _sourceConfigsOffset = 0;
    _watchedOffset = 0;

    const loaders = {
        articles: loadKnowledge,
        'source-configs': loadSourceConfigs,
        scraper: () => { loadScraperConfig(); loadSources(); },
        watched: loadWatchedPages,
    };
    if (loaders[tab]) loaders[tab]();
}

// ═══════════════════════════════════════════════════════════
//  Статьи
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

function expiresAtBadge(expiresAt) {
    if (!expiresAt) return `<span class="${tw.mutedText} text-xs">—</span>`;
    const d = new Date(expiresAt);
    const now = new Date();
    const formatted = formatDate(expiresAt);
    if (d < now) return `<span class="${tw.badgeRed}" title="${formatted}">${t('knowledge.expired')}</span>`;
    const daysLeft = Math.ceil((d - now) / 86400000);
    if (daysLeft <= 7) return `<span class="${tw.badgeYellow}" title="${formatted}">${formatted}</span>`;
    return `<span class="text-xs" title="${formatted}">${formatted}</span>`;
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

async function loadArticles(offset) {
    if (offset !== undefined) _articlesOffset = offset;
    const container = document.getElementById('articlesContainer');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    const params = buildParams({
        offset: _articlesOffset,
        filters: { category: 'kbCategory', search: 'kbSearch', active: 'kbActive' },
    });

    try {
        const data = await api(`/knowledge/articles?${params}`);
        const articles = data.articles || [];
        if (articles.length === 0) {
            container.innerHTML = `<div class="${tw.emptyState}">${t('knowledge.noArticles')}</div>`;
            renderPagination({ containerId: 'articlesPagination', total: 0, offset: 0 });
            return;
        }
        container.innerHTML = `
            <div class="overflow-x-auto min-h-[480px]"><table class="${tw.table}" id="articlesTable"><thead><tr><th class="${tw.thSortable}" data-sortable>${t('knowledge.articleTitle')}</th><th class="${tw.thSortable}" data-sortable>${t('knowledge.category')}</th><th class="${tw.thSortable}" data-sortable>${t('knowledge.embedding')}</th><th class="${tw.thSortable}" data-sortable>${t('knowledge.activeCol')}</th><th class="${tw.thSortable}" data-sortable>${t('knowledge.expiresAt')}</th><th class="${tw.thSortable}" data-sortable>${t('knowledge.updated')}</th><th class="${tw.th}">${t('knowledge.actions')}</th></tr></thead><tbody>
            ${articles.map(a => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}" data-label="${t('knowledge.articleTitle')}">${escapeHtml(a.title)}</td>
                    <td class="${tw.td}" data-label="${t('knowledge.category')}"><span class="${tw.badgeBlue}">${escapeHtml(a.category)}</span></td>
                    <td class="${tw.td}" data-label="${t('knowledge.embedding')}">${embeddingBadge(a.embedding_status)}</td>
                    <td class="${tw.td}" data-label="${t('knowledge.activeCol')}">${a.active !== false ? `<span class="${tw.badgeGreen}">${t('common.yes')}</span>` : `<span class="${tw.badgeRed}">${t('common.no')}</span>`}</td>
                    <td class="${tw.td}" data-label="${t('knowledge.expiresAt')}" data-sort-value="${a.expires_at || ''}">${expiresAtBadge(a.expires_at)}</td>
                    <td class="${tw.td}" data-label="${t('knowledge.updated')}" data-sort-value="${a.updated_at || a.created_at || ''}">${formatDate(a.updated_at || a.created_at)}</td>
                    <td class="${tw.tdActions}">
                        <div class="relative inline-block">
                            <button class="px-1.5 py-0.5 text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 text-sm cursor-pointer" onclick="this.nextElementSibling.classList.toggle('hidden')">&hellip;</button>
                            <div class="hidden absolute right-0 z-20 mt-1 w-40 bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-md shadow-lg py-1">
                                <button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" onclick="window._pages.knowledge.editArticle('${a.id}')">${t('knowledge.editBtn')}</button>
                                <button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" onclick="window._pages.knowledge.reindexArticle('${a.id}')">${t('knowledge.reindexBtn')}</button>
                                <button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" onclick="window._pages.knowledge.toggleArticle('${a.id}', ${a.active !== false})">${a.active !== false ? t('common.deactivate') : t('common.activate')}</button>
                                <button class="w-full text-left px-3 py-1.5 text-xs text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/30 cursor-pointer" data-id="${escapeHtml(a.id)}" data-name="${escapeHtml(a.title)}" onclick="window._pages.knowledge.deleteArticle(this.dataset.id, this.dataset.name)">${t('common.delete')}</button>
                            </div>
                        </div>
                    </td>
                </tr>
            `).join('')}
            </tbody></table></div>
            <p class="${tw.mutedText} mt-2">${t('knowledge.total', {count: data.total})}</p>`;

        makeSortable('articlesTable');
        renderPagination({
            containerId: 'articlesPagination',
            total: data.total,
            offset: _articlesOffset,
            onPage: (newOffset) => loadArticles(newOffset),
        });
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
    document.getElementById('articleExpiresAt').value = '';
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
        // Format expires_at for datetime-local input (YYYY-MM-DDTHH:MM)
        const expiresEl = document.getElementById('articleExpiresAt');
        if (a.expires_at) {
            const d = new Date(a.expires_at);
            expiresEl.value = d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0') + 'T' + String(d.getHours()).padStart(2,'0') + ':' + String(d.getMinutes()).padStart(2,'0');
        } else {
            expiresEl.value = '';
        }
        document.getElementById('createArticleModal').classList.add('show');
    } catch (e) { showToast(t('knowledge.loadFailed', {error: e.message}), 'error'); }
}

async function saveArticle() {
    const id = document.getElementById('editArticleId').value;
    const title = document.getElementById('articleTitle').value.trim();
    const category = document.getElementById('articleCategory').value;
    const content = document.getElementById('articleContent').value.trim();
    const expiresAtRaw = document.getElementById('articleExpiresAt').value;
    if (!title || !content) { showToast(t('knowledge.titleRequired'), 'error'); return; }
    // Convert datetime-local to ISO string, or empty string to clear
    const expires_at = expiresAtRaw ? new Date(expiresAtRaw).toISOString() : '';
    try {
        if (id) {
            await api(`/knowledge/articles/${id}`, { method: 'PATCH', body: JSON.stringify({ title, category, content, expires_at }) });
            showToast(t('knowledge.articleUpdated'));
        } else {
            const body = { title, category, content };
            if (expires_at) body.expires_at = expires_at;
            await api('/knowledge/articles', { method: 'POST', body: JSON.stringify(body) });
            showToast(t('knowledge.articleCreated'));
        }
        closeModal('createArticleModal');
        loadArticles(_articlesOffset);
    } catch (e) { showToast(t('knowledge.saveFailed', {error: e.message}), 'error'); }
}

async function toggleArticle(id, currentlyActive) {
    try {
        await api(`/knowledge/articles/${id}`, { method: 'PATCH', body: JSON.stringify({ active: !currentlyActive }) });
        showToast(currentlyActive ? t('knowledge.articleDeactivated') : t('knowledge.articleActivated'));
        loadArticles(_articlesOffset);
    } catch (e) { showToast(t('knowledge.toggleFailed', {error: e.message}), 'error'); }
}

async function deleteArticle(id, title) {
    if (!confirm(t('knowledge.deleteConfirm', {title}))) return;
    try {
        await api(`/knowledge/articles/${id}`, { method: 'DELETE' });
        showToast(t('knowledge.articleDeleted'));
        loadArticles(_articlesOffset);
    } catch (e) { showToast(t('knowledge.deleteFailed', {error: e.message}), 'error'); }
}

async function reindexArticle(id) {
    try {
        const data = await api(`/knowledge/articles/${id}/reindex`, { method: 'POST' });
        showToast(data.message || t('knowledge.reindexQueued'));
        loadArticles(_articlesOffset);
    } catch (e) { showToast(t('knowledge.reindexFailed', {error: e.message}), 'error'); }
}

async function reindexAll() {
    if (!confirm(t('knowledge.reindexAllConfirm'))) return;
    try {
        const data = await api('/knowledge/reindex-all', { method: 'POST' });
        showToast(data.message || t('knowledge.reindexAllDispatched'));
        loadArticles(_articlesOffset);
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
        loadArticles(_articlesOffset);
    } catch (e) { showToast(t('knowledge.importFailed', {error: e.message}), 'error'); }
}

// ═══════════════════════════════════════════════════════════
//  Источники (Sources tab)
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

async function loadScraperConfig() {
    const configCard = document.getElementById('scraperConfigCard');
    if (!configCard) return;
    try {
        const configData = await api('/admin/scraper/config');
        const cfg = configData.config || {};

        configCard.innerHTML = `
            <div class="flex flex-wrap items-center gap-4 mb-2">
                <label class="flex items-center gap-2 text-sm cursor-pointer">
                    <input type="checkbox" id="scraperEnabled" ${cfg.enabled ? 'checked' : ''} onchange="window._pages.knowledge.toggleScraperEnabled()">
                    <span>${t('sources.enabled')}</span>
                </label>
                <label class="flex items-center gap-2 text-sm cursor-pointer">
                    <input type="checkbox" id="scraperAutoApprove" ${cfg.auto_approve ? 'checked' : ''} onchange="window._pages.knowledge.toggleAutoApprove()">
                    <span>${t('sources.autoApprove')}</span>
                </label>
                <label class="flex items-center gap-2 text-sm cursor-pointer">
                    <input type="checkbox" id="scraperDedupLlm" ${cfg.dedup_llm_check ? 'checked' : ''} onchange="window._pages.knowledge.toggleDedupLlm()">
                    <span title="${t('sources.dedupLlmCheckHint')}">${t('sources.dedupLlmCheck')}</span>
                </label>
                <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.knowledge.runScraperNow()">${t('sources.runNow')}</button>
            </div>
            <div class="flex flex-wrap items-center gap-4">
                <label class="flex items-center gap-2 text-sm">
                    <span>${t('sources.minDate')}</span>
                    <input type="date" id="scraperMinDate" value="${escapeHtml(cfg.min_date || '')}" class="border rounded px-2 py-1 text-sm dark:bg-gray-700 dark:border-gray-600" onchange="window._pages.knowledge.updateMinDate()">
                    <span class="${tw.mutedText} text-xs" title="${t('sources.minDateHint')}">?</span>
                </label>
                <label class="flex items-center gap-2 text-sm">
                    <span>${t('sources.maxDate')}</span>
                    <input type="date" id="scraperMaxDate" value="${escapeHtml(cfg.max_date || '')}" class="border rounded px-2 py-1 text-sm dark:bg-gray-700 dark:border-gray-600" onchange="window._pages.knowledge.updateMaxDate()">
                    <span class="${tw.mutedText} text-xs" title="${t('sources.maxDateHint')}">?</span>
                </label>
            </div>`;
    } catch (e) {
        configCard.innerHTML = `<div class="${tw.mutedText}">${t('sources.configLoadFailed', {error: escapeHtml(e.message)})}</div>`;
    }
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

async function loadSourceConfigs(offset) {
    if (offset !== undefined) _sourceConfigsOffset = offset;
    const container = document.getElementById('sourceConfigsContainer');
    if (!container) return;
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;
    const params = buildParams({ offset: _sourceConfigsOffset });
    try {
        const data = await api(`/admin/scraper/source-configs?${params}`);
        const configs = data.configs || [];

        let html = `
            <div class="mb-4">
                <button class="${tw.btnPrimary}" onclick="window._pages.knowledge.showAddSourceConfig()">${t('sources.addSource')}</button>
            </div>`;

        if (configs.length === 0) {
            html += `<div class="${tw.emptyState}">${t('sources.noSourceConfigs')}</div>`;
        } else {
            const days = { monday: t('sources.scheduleDays.monday'), tuesday: t('sources.scheduleDays.tuesday'), wednesday: t('sources.scheduleDays.wednesday'), thursday: t('sources.scheduleDays.thursday'), friday: t('sources.scheduleDays.friday'), saturday: t('sources.scheduleDays.saturday'), sunday: t('sources.scheduleDays.sunday') };

            html += `
            <div class="overflow-x-auto min-h-[480px]"><table class="${tw.table}" id="sourceConfigsTable"><thead><tr>
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
                    <td class="${tw.td}" data-label="${t('sources.sourceName')}">
                        <div>${escapeHtml(c.name)}</div>
                        <div class="${tw.mutedText} text-xs">${escapeHtml((c.source_url || '').replace(/^https?:\/\//, '').substring(0, 40))}</div>
                    </td>
                    <td class="${tw.td}" data-label="${t('sources.sourceType')}">${sourceTypeBadge(c.source_type)}</td>
                    <td class="${tw.td}" data-label="${t('sources.sourceLanguage')}">${langBadge(c.language)}</td>
                    <td class="${tw.td}" data-label="${t('sources.enabled')}" data-sort-value="${c.enabled ? 1 : 0}">
                        <label class="inline-flex items-center cursor-pointer">
                            <input type="checkbox" ${c.enabled ? 'checked' : ''} onchange="window._pages.knowledge.toggleSourceConfigEnabled('${c.id}', this.checked)">
                        </label>
                    </td>
                    <td class="${tw.td}" data-label="${t('sources.schedule')}"><span class="text-xs">${escapeHtml(schedText)}</span></td>
                    <td class="${tw.td}" data-label="${t('sources.lastRun')}" data-sort-value="${c.last_run_at || ''}">
                        ${runStatusBadge(c.last_run_status)}
                        ${c.last_run_at ? `<br><span class="${tw.mutedText} text-xs">${formatDate(c.last_run_at)}</span>` : ''}
                        ${statsText ? `<br><span class="${tw.mutedText} text-xs">${statsText}</span>` : ''}
                    </td>
                    <td class="${tw.tdActions}">
                        <div class="relative inline-block">
                            <button class="px-1.5 py-0.5 text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 text-sm cursor-pointer" onclick="this.nextElementSibling.classList.toggle('hidden')">&hellip;</button>
                            <div class="hidden absolute right-0 z-20 mt-1 w-36 bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-md shadow-lg py-1">
                                <button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" onclick="window._pages.knowledge.runSourceConfig('${c.id}')">${t('sources.runSource')}</button>
                                <button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" onclick="window._pages.knowledge.editSourceConfig('${c.id}')">${t('common.edit')}</button>
                                <button class="w-full text-left px-3 py-1.5 text-xs text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/30 cursor-pointer" data-id="${escapeHtml(c.id)}" data-name="${escapeHtml(c.name)}" onclick="window._pages.knowledge.deleteSourceConfig(this.dataset.id, this.dataset.name)">${t('common.delete')}</button>
                            </div>
                        </div>
                    </td>
                </tr>`;
            }).join('')}
            </tbody></table></div>`;
        }

        container.innerHTML = html;
        makeSortable('sourceConfigsTable');
        renderPagination({
            containerId: 'sourceConfigsPagination',
            total: data.total,
            offset: _sourceConfigsOffset,
            onPage: (newOffset) => loadSourceConfigs(newOffset),
        });
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('sources.failedToLoad', {error: escapeHtml(e.message)})}
            <br><button class="${tw.btnPrimary} ${tw.btnSm} mt-2" onclick="window._pages.knowledge.loadSourceConfigs()">${t('common.retry')}</button></div>`;
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

async function loadSources(offset) {
    if (offset !== undefined) _sourcesOffset = offset;
    const container = document.getElementById('sourcesContainer');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    const params = buildParams({
        offset: _sourcesOffset,
        filters: { status: 'sourcesStatusFilter' },
    });

    try {
        const data = await api(`/admin/scraper/sources?${params}`);
        const sources = data.sources || [];
        if (sources.length === 0) {
            container.innerHTML = `<div class="${tw.emptyState}">${t('sources.noSources')}</div>`;
            renderPagination({ containerId: 'sourcesPagination', total: 0, offset: 0 });
            return;
        }
        container.innerHTML = `
            <div class="overflow-x-auto min-h-[480px]"><table class="${tw.table}" id="sourcesTable"><thead><tr><th class="${tw.thSortable}" data-sortable>${t('sources.url')}</th><th class="${tw.thSortable}" data-sortable>${t('sources.originalTitle')}</th><th class="${tw.thSortable}" data-sortable>${t('sources.status')}</th><th class="${tw.thSortable}" data-sortable>${t('sources.date')}</th><th class="${tw.th}">${t('sources.actions')}</th></tr></thead><tbody>
            ${sources.map(s => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}" data-label="${t('sources.url')}"><a href="${escapeHtml(s.url)}" target="_blank" class="text-blue-600 dark:text-blue-400 hover:underline text-xs">${escapeHtml((s.url || '').replace(/^https?:\/\//, '').substring(0, 50))}${(s.url || '').length > 60 ? '...' : ''}</a></td>
                    <td class="${tw.td}" data-label="${t('sources.originalTitle')}">${escapeHtml(s.original_title || '-')}</td>
                    <td class="${tw.td}" data-label="${t('sources.status')}" data-sort-value="${escapeHtml(s.status || '')}">${sourceStatusBadge(s.status)}${s.skip_reason ? `<br><span class="${tw.mutedText} text-xs">${escapeHtml(s.skip_reason)}</span>` : ''}</td>
                    <td class="${tw.td}" data-label="${t('sources.date')}" data-sort-value="${s.created_at || ''}">${formatDate(s.created_at)}</td>
                    <td class="${tw.tdActions}">
                        ${s.status === 'processed' && s.article_id ? `
                        <div class="relative inline-block">
                            <button class="px-1.5 py-0.5 text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 text-sm cursor-pointer" onclick="this.nextElementSibling.classList.toggle('hidden')">&hellip;</button>
                            <div class="hidden absolute right-0 z-20 mt-1 w-36 bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-md shadow-lg py-1">
                                <button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" onclick="window._pages.knowledge.viewSourceArticle('${s.article_id}')">${t('sources.viewArticle')}</button>
                                ${s.article_active === false ? `<button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" onclick="window._pages.knowledge.approveSource('${s.id}')">${t('sources.approve')}</button>` : ''}
                                <button class="w-full text-left px-3 py-1.5 text-xs text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/30 cursor-pointer" onclick="window._pages.knowledge.rejectSource('${s.id}')">${t('sources.reject')}</button>
                            </div>
                        </div>` : ''}
                    </td>
                </tr>
            `).join('')}
            </tbody></table></div>
            <p class="${tw.mutedText} mt-2">${t('sources.total', {count: data.total})}</p>`;

        makeSortable('sourcesTable');
        renderPagination({
            containerId: 'sourcesPagination',
            total: data.total,
            offset: _sourcesOffset,
            onPage: (newOffset) => loadSources(newOffset),
        });
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('sources.failedToLoad', {error: escapeHtml(e.message)})}
            <br><button class="${tw.btnPrimary} ${tw.btnSm} mt-2" onclick="window._pages.knowledge.loadSources()">${t('common.retry')}</button></div>`;
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
        loadSources(_sourcesOffset);
    } catch (e) { showToast(t('sources.approveFailed', {error: e.message}), 'error'); }
}

async function rejectSource(id) {
    if (!confirm(t('sources.rejectConfirm'))) return;
    try {
        await api(`/admin/scraper/sources/${id}/reject`, { method: 'POST' });
        showToast(t('sources.rejected'));
        loadSources(_sourcesOffset);
    } catch (e) { showToast(t('sources.rejectFailed', {error: e.message}), 'error'); }
}

function viewSourceArticle(articleId) {
    showTab('articles');
    setTimeout(() => editArticle(articleId), 500);
}

// ═══════════════════════════════════════════════════════════
//  Watched Pages
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

async function loadWatchedPages(offset) {
    if (offset !== undefined) _watchedOffset = offset;
    const container = document.getElementById('watchedPagesContainer');
    if (!container) return;
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    try {
        // Load tenants list (cached)
        if (_tenantsList.length === 0) {
            try {
                const td = await api('/admin/tenants');
                _tenantsList = (td.tenants || []).filter(t => t.is_active);
            } catch { _tenantsList = []; }
        }

        const params = buildParams({ offset: _watchedOffset });
        if (_watchedTenantId) params.set('tenant_id', _watchedTenantId);
        const data = await api(`/admin/scraper/watched-pages?${params}`);
        const pages = data.pages || [];

        const cats = _categories.length ? _categories : [
            { value: 'delivery', label: 'delivery' }, { value: 'warranty', label: 'warranty' },
            { value: 'returns', label: 'returns' }, { value: 'policies', label: 'policies' },
            { value: 'general', label: 'general' },
        ];
        const categoryOptions = `<option value="" disabled selected>${t('common.select')}</option>`
            + cats.map(c => `<option value="${c.value}">${escapeHtml(c.label)}</option>`).join('');

        const intervalOptions = `<option value="" disabled selected>${t('common.select')}</option>`
            + INTERVAL_OPTIONS.map(o => `<option value="${o.value}">${o.label()}</option>`).join('');

        const tenantOptions = `<option value="">${t('sources.allTenants')}</option>`
            + _tenantsList.map(t => `<option value="${t.id}" ${t.id === _watchedTenantId ? 'selected' : ''}>${escapeHtml(t.name)}</option>`).join('');

        const tenantCreateOptions = `<option value="">${t('sources.noTenant')}</option>`
            + _tenantsList.map(t => `<option value="${t.id}" ${t.id === _watchedTenantId ? 'selected' : ''}>${escapeHtml(t.name)}</option>`).join('');

        let html = `
            <div class="flex flex-wrap items-center gap-2 mb-3">
                <label class="text-xs ${tw.mutedText}">${t('sources.filterByTenant')}</label>
                <select id="watchedTenantFilter" class="border rounded px-2 py-1.5 text-sm dark:bg-gray-700 dark:border-gray-600"
                    onchange="window._pages.knowledge.filterWatchedByTenant(this.value)">${tenantOptions}</select>
            </div>
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
                    <label class="block text-xs ${tw.mutedText} mb-1">${t('sources.tenant')}</label>
                    <select id="watchedPageTenant" class="border rounded px-2 py-1.5 text-sm dark:bg-gray-700 dark:border-gray-600">${tenantCreateOptions}</select>
                </div>
                <div>
                    <label class="flex items-center gap-1.5 text-sm cursor-pointer pt-4">
                        <input type="checkbox" id="watchedPageDiscovery">
                        <span>${t('sources.discoveryLabel')}</span>
                        <span class="${tw.mutedText} text-xs cursor-help" title="${t('sources.discoveryHint')}">(?)</span>
                    </label>
                </div>
                <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.knowledge.addWatchedPage()">${t('sources.addWatchedPage')}</button>
                <button class="${tw.btnPrimary} ${tw.btnSm} ml-2" onclick="window._pages.knowledge.showBulkImportModal()">${t('sources.bulkImport')}</button>
            </div>`;

        if (pages.length === 0) {
            html += `<div class="${tw.emptyState}">${t('sources.noWatchedPages')}</div>`;
        } else {
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
                    <td class="${tw.td}" data-label="${t('sources.watchedUrl')}">
                        <a href="${escapeHtml(p.url)}" target="_blank" class="text-blue-600 dark:text-blue-400 hover:underline text-xs">${escapeHtml((p.url || '').replace(/^https?:\/\//, '').substring(0, 50))}</a>
                        ${isDiscovery ? `<br><span class="${tw.badgeYellow} text-xs">${t('sources.discoveryBadge')}</span> <span class="${tw.mutedText} text-xs">${t('sources.childCount', {count: p.child_count || 0})}</span>` : ''}
                    </td>
                    <td class="${tw.td}" data-label="${t('sources.watchedCategory')}"><span class="${tw.badgeBlue}">${escapeHtml(p.article_category || '-')}</span></td>
                    <td class="${tw.td}" data-label="${t('sources.watchedInterval')}">
                        <select class="border rounded px-1 py-0.5 text-xs dark:bg-gray-700 dark:border-gray-600"
                            onchange="window._pages.knowledge.updateWatchedInterval('${p.id}', parseInt(this.value))">
                            ${INTERVAL_OPTIONS.map(o => `<option value="${o.value}" ${o.value === p.rescrape_interval_hours ? 'selected' : ''}>${o.label()}</option>`).join('')}
                        </select>
                    </td>
                    <td class="${tw.td}" data-label="${t('sources.watchedStatus')}">${sourceStatusBadge(p.status)}</td>
                    <td class="${tw.td}" data-label="${t('sources.watchedLastScraped')}">${p.fetched_at ? formatDate(p.fetched_at) : `<span class="${tw.mutedText} text-xs">${t('sources.neverScraped')}</span>`}</td>
                    <td class="${tw.td}" data-label="${t('sources.watchedNextScrape')}">${p.next_scrape_at ? formatDate(p.next_scrape_at) : '-'}</td>
                    <td class="${tw.tdActions}">
                        <div class="relative inline-block">
                            <button class="px-1.5 py-0.5 text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 text-sm cursor-pointer" onclick="this.nextElementSibling.classList.toggle('hidden')">&hellip;</button>
                            <div class="hidden absolute right-0 z-20 mt-1 w-40 bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-md shadow-lg py-1">
                                ${!isDiscovery && p.article_id ? `<button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" onclick="window._pages.knowledge.viewSourceArticle('${p.article_id}')">${t('sources.watchedArticle')}</button>` : ''}
                                <button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" onclick="window._pages.knowledge.scrapeWatchedNow('${p.id}')">${t('sources.scrapeNow')}</button>
                                <button class="w-full text-left px-3 py-1.5 text-xs text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/30 cursor-pointer" onclick="window._pages.knowledge.deleteWatchedPage('${p.id}')">${t('sources.deleteWatched')}</button>
                            </div>
                        </div>
                    </td>
                </tr>`;
                for (const c of children) {
                    rows += `
                    <tr class="${tw.trHover} opacity-80">
                        <td class="${tw.td} pl-8" data-label="${t('sources.watchedUrl')}">
                            <span class="${tw.mutedText}">↳</span>
                            <a href="${escapeHtml(c.url)}" target="_blank" class="text-blue-600 dark:text-blue-400 hover:underline text-xs">${escapeHtml((c.url || '').replace(/^https?:\/\//, '').substring(0, 50))}</a>
                        </td>
                        <td class="${tw.td}" data-label="${t('sources.watchedCategory')}"><span class="${tw.badgeBlue}">${escapeHtml(c.article_category || '-')}</span></td>
                        <td class="${tw.td}" data-label="${t('sources.watchedInterval')}"><span class="text-xs">${intervalLabel(c.rescrape_interval_hours)}</span></td>
                        <td class="${tw.td}" data-label="${t('sources.watchedStatus')}">${sourceStatusBadge(c.status)}</td>
                        <td class="${tw.td}" data-label="${t('sources.watchedLastScraped')}">${c.fetched_at ? formatDate(c.fetched_at) : `<span class="${tw.mutedText} text-xs">${t('sources.neverScraped')}</span>`}</td>
                        <td class="${tw.td}" data-label="${t('sources.watchedNextScrape')}">${c.next_scrape_at ? formatDate(c.next_scrape_at) : '-'}</td>
                        <td class="${tw.tdActions}">
                            ${c.article_id ? `<button class="text-xs text-blue-600 dark:text-blue-400 hover:underline cursor-pointer" onclick="window._pages.knowledge.viewSourceArticle('${c.article_id}')">${t('sources.watchedArticle')}</button>` : ''}
                        </td>
                    </tr>`;
                }
            }

            html += `
            <div class="overflow-x-auto min-h-[480px]"><table class="${tw.table}" id="watchedPagesTable"><thead><tr>
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
        renderPagination({
            containerId: 'watchedPagesPagination',
            total: data.total,
            offset: _watchedOffset,
            onPage: (newOffset) => loadWatchedPages(newOffset),
        });
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('sources.watchedFailedToLoad', {error: escapeHtml(e.message)})}
            <br><button class="${tw.btnPrimary} ${tw.btnSm} mt-2" onclick="window._pages.knowledge.loadWatchedPages()">${t('common.retry')}</button></div>`;
    }
}

function filterWatchedByTenant(tenantId) {
    _watchedTenantId = tenantId;
    _watchedOffset = 0;
    loadWatchedPages();
}

async function addWatchedPage() {
    const url = document.getElementById('watchedPageUrl')?.value?.trim();
    const category = document.getElementById('watchedPageCategory')?.value;
    const intervalRaw = document.getElementById('watchedPageInterval')?.value;
    const is_discovery = document.getElementById('watchedPageDiscovery')?.checked || false;
    const tenant_id = document.getElementById('watchedPageTenant')?.value || null;
    if (!url) { showToast(t('sources.urlRequired'), 'error'); return; }
    if (!category) { showToast(t('sources.categoryRequired'), 'error'); return; }
    if (!intervalRaw) { showToast(t('sources.intervalRequired'), 'error'); return; }
    const interval = parseInt(intervalRaw, 10);
    if (isNaN(interval) || interval < 1) { showToast(t('sources.intervalRequired'), 'error'); return; }

    try {
        await api('/admin/scraper/watched-pages', {
            method: 'POST',
            body: JSON.stringify({ url, category, rescrape_interval_hours: interval, is_discovery, tenant_id }),
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
        if (result.status === 'unchanged') {
            showToast(t('sources.scrapeNowUnchanged'));
        } else if (result.status === 'ok' && result.discovered !== undefined) {
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
//  Bulk Import Modal
// ═══════════════════════════════════════════════════════════
function showBulkImportModal() {
    // Build tenant options
    let tenantOptions = '<option value="">—</option>';
    for (const ten of _tenantsList) {
        tenantOptions += `<option value="${escapeHtml(ten.id)}">${escapeHtml(ten.name)}</option>`;
    }

    const modalHtml = `
        <div id="bulkImportOverlay" class="fixed inset-0 bg-black/50 z-50 flex items-center justify-center" onclick="if(event.target===this)this.remove()">
            <div class="bg-white dark:bg-neutral-800 rounded-lg shadow-xl w-full max-w-lg mx-4 p-6">
                <h3 class="text-lg font-semibold text-neutral-900 dark:text-neutral-50 mb-4">${t('sources.bulkImportTitle')}</h3>
                <textarea id="bulkUrls" rows="8" class="w-full border rounded px-3 py-2 text-sm dark:bg-neutral-700 dark:border-neutral-600 mb-3" placeholder="${t('sources.bulkUrlsPlaceholder')}"></textarea>
                <div class="grid grid-cols-3 gap-3 mb-4">
                    <div>
                        <label class="block text-xs ${tw.mutedText} mb-1">${t('sources.watchedCategory')}</label>
                        <select id="bulkCategory" class="w-full border rounded px-2 py-1.5 text-sm dark:bg-neutral-700 dark:border-neutral-600">
                            <option value="general">general</option>
                            <option value="faq">faq</option>
                            <option value="guides">guides</option>
                            <option value="comparisons">comparisons</option>
                            <option value="brands">brands</option>
                            <option value="procedures">procedures</option>
                            <option value="delivery">delivery</option>
                            <option value="warranty">warranty</option>
                            <option value="returns">returns</option>
                            <option value="policies">policies</option>
                            <option value="promotions">promotions</option>
                            <option value="news">news</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs ${tw.mutedText} mb-1">${t('sources.watchedInterval')}</label>
                        <select id="bulkInterval" class="w-full border rounded px-2 py-1.5 text-sm dark:bg-neutral-700 dark:border-neutral-600">
                            <option value="6">${t('sources.intervalHours.6')}</option>
                            <option value="12">${t('sources.intervalHours.12')}</option>
                            <option value="24">${t('sources.intervalHours.24')}</option>
                            <option value="168" selected>${t('sources.intervalHours.168')}</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs ${tw.mutedText} mb-1">${t('sources.tenant')}</label>
                        <select id="bulkTenant" class="w-full border rounded px-2 py-1.5 text-sm dark:bg-neutral-700 dark:border-neutral-600">${tenantOptions}</select>
                    </div>
                </div>
                <div class="flex justify-end gap-2">
                    <button class="${tw.btnSecondary} ${tw.btnSm}" onclick="document.getElementById('bulkImportOverlay').remove()">${t('common.cancel')}</button>
                    <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.knowledge.submitBulkImport()">${t('sources.bulkImport')}</button>
                </div>
            </div>
        </div>`;
    document.body.insertAdjacentHTML('beforeend', modalHtml);
}

async function submitBulkImport() {
    const raw = document.getElementById('bulkUrls')?.value || '';
    const urls = raw.split('\n').map(u => u.trim()).filter(Boolean);
    if (urls.length === 0) return;

    const interval = parseInt(document.getElementById('bulkInterval')?.value || '168', 10);
    const tenant_id = document.getElementById('bulkTenant')?.value || null;

    try {
        const result = await api('/admin/scraper/watched-pages/bulk', {
            method: 'POST',
            body: JSON.stringify({
                urls,
                category: document.getElementById('bulkCategory')?.value || 'general',
                rescrape_interval_hours: interval,
                tenant_id,
            }),
        });
        showToast(t('sources.bulkImportResult', {added: result.added, skipped: result.skipped}));
        document.getElementById('bulkImportOverlay')?.remove();
        loadWatchedPages();
    } catch (e) {
        showToast(t('sources.watchedPageAddFailed', {error: e.message}), 'error');
    }
}

// ═══════════════════════════════════════════════════════════
//  Init & exports
// ═══════════════════════════════════════════════════════════
export function init() {
    registerPageLoader('knowledge', () => showTab(_activeTab));
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.relative')) {
            document.querySelectorAll('#page-knowledge .relative > div:not(.hidden)').forEach(m => m.classList.add('hidden'));
        }
    });
}

window._pages = window._pages || {};
window._pages.knowledge = {
    showTab,
    // Articles
    loadArticles, showCreateArticle, editArticle, saveArticle,
    toggleArticle, deleteArticle, reindexArticle, reindexAll,
    showImportModal, importDocuments,
    // Source configs
    loadSourceConfigs, showAddSourceConfig, editSourceConfig, saveSourceConfig,
    toggleSourceConfigEnabled, runSourceConfig, deleteSourceConfig,
    // Sources (scraper)
    loadScraperConfig, loadSources, toggleScraperEnabled, toggleAutoApprove, toggleDedupLlm, updateMinDate, updateMaxDate,
    runScraperNow, approveSource, rejectSource, viewSourceArticle,
    // Watched pages
    loadWatchedPages, addWatchedPage, updateWatchedInterval, scrapeWatchedNow, deleteWatchedPage, filterWatchedByTenant,
    showBulkImportModal, submitBulkImport,
};
