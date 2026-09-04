import { api } from '../api.js';
import { escapeHtml, formatDate, closeModal } from '../utils.js';
import { showToast } from '../notifications.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import { makeSortable } from '../sorting.js';
import * as tw from '../tw.js';

// ==================== State ====================

let currentTab = 'browse';           // 'browse' | 'aliases' | 'import'
let currentLevel = 'brands';         // browse sub: 'brands' | 'models' | 'kits'
let currentBrandId = null;
let currentBrandName = '';
let currentModelId = null;
let currentModelName = '';
let brandsOffset = 0;
let modelsOffset = 0;
let kitsOffset = 0;

let aliasesOffset = 0;
let aliasesFilters = { search: '', brand_id: '', model_id: '', source: '' };

let historyOffset = 0;

let currentStagedImport = null;      // {history_id, diff_report, staging_dir, counts}
let applyPollHandle = null;

const PAGE_SIZE = 50;
const ALIAS_PAGE_SIZE = 30;

// ==================== Tab switching ====================

function switchTab(tab) {
    currentTab = tab;
    const ACTIVE = ['border-blue-600', 'text-blue-600', '-mb-px'];
    const INACTIVE = ['border-transparent', 'text-neutral-500', 'hover:text-neutral-800', 'dark:hover:text-neutral-200'];
    for (const key of ['browse', 'aliases', 'import']) {
        const tabBtn = document.getElementById(`vehiclesTab-${key}`);
        const pane = document.getElementById(`vehiclesPane-${key}`);
        if (tabBtn) {
            const isActive = (key === tab);
            for (const c of ACTIVE) tabBtn.classList.toggle(c, isActive);
            for (const c of INACTIVE) tabBtn.classList.toggle(c, !isActive);
        }
        if (pane) pane.style.display = (key === tab) ? '' : 'none';
    }
    // Trigger load for the switched-to tab
    if (tab === 'browse') loadBrands();
    else if (tab === 'aliases') loadAliases();
    else if (tab === 'import') loadImportHistory();
}

// ==================== Stats (shared header) ====================

async function loadStats() {
    const el = document.getElementById('vehiclesStats');
    if (!el) return;
    el.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;
    try {
        const data = await api('/admin/vehicles/stats');
        el.innerHTML = `
            <div class="${tw.card}"><div class="${tw.statValue}">${data.brand_count ?? 0}</div><div class="${tw.statLabel}">${t('vehicles.brands')}</div></div>
            <div class="${tw.card}"><div class="${tw.statValue}">${data.model_count ?? 0}</div><div class="${tw.statLabel}">${t('vehicles.models')}</div></div>
            <div class="${tw.card}"><div class="${tw.statValue}">${data.kit_count ?? 0}</div><div class="${tw.statLabel}">${t('vehicles.kits')}</div></div>
            <div class="${tw.card}"><div class="${tw.statValue}">${data.tire_size_count ?? 0}</div><div class="${tw.statLabel}">${t('vehicles.tireSizes')}</div></div>
            <div class="${tw.card}"><div class="${tw.statValue}">${data.imported_at ? formatDate(data.imported_at) : t('vehicles.neverImported')}</div><div class="${tw.statLabel}">${t('vehicles.lastImport')}</div></div>
        `;
    } catch (e) {
        el.innerHTML = `<div class="${tw.emptyState}">${t('vehicles.failedToLoad', { error: escapeHtml(e.message) })}</div>`;
    }
}

// ==================== Browse: Breadcrumb ====================

function renderBreadcrumb() {
    const el = document.getElementById('vehiclesBreadcrumb');
    if (!el) return;
    if (currentLevel === 'brands') {
        el.style.display = 'none';
        return;
    }
    el.style.display = '';
    let html = `<a href="#" class="${tw.breadcrumbLink}" onclick="window._pages.vehicles.goToBrands()">${t('vehicles.brands')}</a>`;
    if (currentLevel === 'models' || currentLevel === 'kits') {
        html += ` / <span>${escapeHtml(currentBrandName)}</span>`;
    }
    if (currentLevel === 'kits') {
        html += ` / <a href="#" class="${tw.breadcrumbLink}" onclick="window._pages.vehicles.goToModels()">${t('vehicles.models')}</a>`;
        html += ` / <span>${escapeHtml(currentModelName)}</span>`;
    }
    el.innerHTML = html;
}

// ==================== Browse: Brands ====================

async function loadBrands(offset = 0) {
    brandsOffset = offset;
    currentLevel = 'brands';
    renderBreadcrumb();

    const container = document.getElementById('vehiclesContainer');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    const params = new URLSearchParams({ limit: PAGE_SIZE, offset });
    const search = document.getElementById('vehiclesSearch').value.trim();
    if (search) params.set('search', search);

    try {
        const data = await api(`/admin/vehicles/brands?${params}`);
        const items = data.items || [];

        const toolbarHtml = `
            <div class="flex flex-wrap gap-2 mb-3">
                <button class="admin-only ${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.vehicles.openBrandModal()">${t('vehicles.addBrand')}</button>
            </div>
        `;

        if (items.length === 0) {
            container.innerHTML = toolbarHtml + `<div class="${tw.emptyState}">${t('vehicles.noData')}</div>`;
            document.getElementById('vehiclesPagination').innerHTML = '';
            return;
        }

        container.innerHTML = toolbarHtml + `
            <div class="overflow-x-auto min-h-[480px]"><table class="${tw.table}" id="brandsTable">
            <thead><tr>
                <th class="${tw.thSortable}" data-sortable>${t('vehicles.brand')}</th>
                <th class="${tw.thSortable}" data-sortable>${t('vehicles.models')}</th>
                <th class="${tw.thSortable}" data-sortable>${t('vehicles.source')}</th>
                <th class="${tw.th}">${t('common.actions')}</th>
            </tr></thead>
            <tbody>
            ${items.map(b => {
                const isManual = (b.source || '') === 'manual';
                const badge = isManual ? `<span class="${tw.badgeBlue}">manual</span>` : `<span class="${tw.badgeGray}">auto</span>`;
                return `
                <tr class="${tw.trHover}" data-id="${b.id}" data-name="${escapeHtml(b.name)}">
                    <td class="${tw.td} cursor-pointer" data-label="${t('vehicles.brand')}" onclick="window._pages.vehicles.selectBrand(${b.id}, this.parentElement.dataset.name)">${escapeHtml(b.name)}</td>
                    <td class="${tw.td}" data-label="${t('vehicles.models')}">${b.model_count}</td>
                    <td class="${tw.td}" data-label="${t('vehicles.source')}">${badge}</td>
                    <td class="${tw.tdActions}">
                        <button class="admin-only ${tw.btnSecondary} ${tw.btnSm}" onclick="window._pages.vehicles.openBrandModal(${b.id}, '${escapeHtml(b.name).replace(/'/g, "\\'")}')">${t('common.edit')}</button>
                        ${isManual ? `<button class="admin-only ${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.vehicles.deleteBrand(${b.id}, '${escapeHtml(b.name).replace(/'/g, "\\'")}')">${t('common.delete')}</button>` : ''}
                    </td>
                </tr>`;
            }).join('')}
            </tbody></table></div>
        `;

        makeSortable('brandsTable');
        renderPagination(data.total, offset, 'loadBrands');
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('vehicles.failedToLoad', { error: escapeHtml(e.message) })}</div>`;
    }
}

// ==================== Browse: Models ====================

async function loadModels(brandId, brandName, offset = 0) {
    currentBrandId = brandId;
    currentBrandName = brandName || currentBrandName;
    modelsOffset = offset;
    currentLevel = 'models';
    renderBreadcrumb();

    const container = document.getElementById('vehiclesContainer');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    const params = new URLSearchParams({ limit: PAGE_SIZE, offset });
    const search = document.getElementById('vehiclesSearch').value.trim();
    if (search) params.set('search', search);

    try {
        const data = await api(`/admin/vehicles/brands/${brandId}/models?${params}`);
        const items = data.items || [];

        const toolbarHtml = `
            <div class="flex flex-wrap gap-2 mb-3">
                <button class="${tw.btnSecondary} ${tw.btnSm}" onclick="window._pages.vehicles.goToBrands()">${t('vehicles.back')}</button>
                <button class="admin-only ${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.vehicles.openModelModal(null, ${brandId})">${t('vehicles.addModel')}</button>
            </div>
        `;

        if (items.length === 0) {
            container.innerHTML = toolbarHtml + `<div class="${tw.emptyState}">${t('vehicles.noData')}</div>`;
            document.getElementById('vehiclesPagination').innerHTML = '';
            return;
        }

        container.innerHTML = toolbarHtml + `
            <div class="overflow-x-auto"><table class="${tw.table}" id="modelsTable">
            <thead><tr>
                <th class="${tw.thSortable}" data-sortable>${t('vehicles.model')}</th>
                <th class="${tw.thSortable}" data-sortable>${t('vehicles.kits')}</th>
                <th class="${tw.thSortable}" data-sortable>${t('vehicles.source')}</th>
                <th class="${tw.th}">${t('common.actions')}</th>
            </tr></thead>
            <tbody>
            ${items.map(m => {
                const isManual = (m.source || '') === 'manual';
                const badge = isManual ? `<span class="${tw.badgeBlue}">manual</span>` : `<span class="${tw.badgeGray}">auto</span>`;
                return `
                <tr class="${tw.trHover}" data-id="${m.id}" data-name="${escapeHtml(m.name)}">
                    <td class="${tw.td} cursor-pointer" data-label="${t('vehicles.model')}" onclick="window._pages.vehicles.selectModel(${m.id}, this.parentElement.dataset.name)">${escapeHtml(m.name)}</td>
                    <td class="${tw.td}" data-label="${t('vehicles.kits')}">${m.kit_count}</td>
                    <td class="${tw.td}" data-label="${t('vehicles.source')}">${badge}</td>
                    <td class="${tw.tdActions}">
                        <button class="admin-only ${tw.btnSecondary} ${tw.btnSm}" onclick="window._pages.vehicles.openModelModal(${m.id}, ${brandId}, '${escapeHtml(m.name).replace(/'/g, "\\'")}')">${t('common.edit')}</button>
                        ${isManual ? `<button class="admin-only ${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.vehicles.deleteModel(${m.id}, '${escapeHtml(m.name).replace(/'/g, "\\'")}')">${t('common.delete')}</button>` : ''}
                    </td>
                </tr>`;
            }).join('')}
            </tbody></table></div>
        `;

        makeSortable('modelsTable');
        renderPagination(data.total, offset, 'loadModelsPage');
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('vehicles.failedToLoad', { error: escapeHtml(e.message) })}</div>`;
    }
}

// ==================== Browse: Kits + Tire Sizes ====================

async function loadKits(modelId, modelName, offset = 0) {
    currentModelId = modelId;
    currentModelName = modelName || currentModelName;
    kitsOffset = offset;
    currentLevel = 'kits';
    renderBreadcrumb();

    const container = document.getElementById('vehiclesContainer');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    const params = new URLSearchParams({ limit: PAGE_SIZE, offset });

    try {
        const data = await api(`/admin/vehicles/models/${modelId}/kits?${params}`);
        const items = data.items || [];

        if (items.length === 0) {
            container.innerHTML = `
                <button class="${tw.btnSecondary} mb-3" onclick="window._pages.vehicles.goToModels()">${t('vehicles.back')}</button>
                <div class="${tw.emptyState}">${t('vehicles.noData')}</div>
            `;
            document.getElementById('vehiclesPagination').innerHTML = '';
            return;
        }

        container.innerHTML = `
            <button class="${tw.btnSecondary} mb-3" onclick="window._pages.vehicles.goToModels()">${t('vehicles.back')}</button>
            <div class="overflow-x-auto"><table class="${tw.table}" id="kitsTable">
            <thead><tr>
                <th class="${tw.thSortable}" data-sortable>${t('vehicles.year')}</th>
                <th class="${tw.thSortable}" data-sortable>${t('vehicles.trim')}</th>
                <th class="${tw.thSortable}" data-sortable>${t('vehicles.pcd')}</th>
                <th class="${tw.thSortable}" data-sortable>${t('vehicles.bolts')}</th>
                <th class="${tw.thSortable}" data-sortable>${t('vehicles.dia')}</th>
                <th class="${tw.thSortable}" data-sortable>${t('vehicles.tireSizes')}</th>
                <th class="${tw.th}"></th>
            </tr></thead>
            <tbody>
            ${items.map(k => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}" data-label="${t('vehicles.year')}">${k.year}</td>
                    <td class="${tw.td}" data-label="${t('vehicles.trim')}">${escapeHtml(k.name || '-')}</td>
                    <td class="${tw.td}" data-label="${t('vehicles.pcd')}">${k.pcd ?? '-'}</td>
                    <td class="${tw.td}" data-label="${t('vehicles.bolts')}" data-sort-value="${k.bolt_count ?? 0}">${k.bolt_count ?? '-'}${k.bolt_size ? ' / ' + escapeHtml(k.bolt_size) : ''}</td>
                    <td class="${tw.td}" data-label="${t('vehicles.dia')}">${k.dia ?? '-'}</td>
                    <td class="${tw.td}" data-label="${t('vehicles.tireSizes')}">${k.tire_size_count}</td>
                    <td class="${tw.tdActions}">
                        <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.vehicles.toggleTireSizes(this, ${k.id})">${t('vehicles.tireSizes')}</button>
                    </td>
                </tr>
                <tr class="tire-sizes-row" id="tire-sizes-${k.id}" style="display:none">
                    <td colspan="7" class="${tw.td} bg-neutral-50 dark:bg-neutral-800/50"></td>
                </tr>
            `).join('')}
            </tbody></table></div>
        `;

        makeSortable('kitsTable');
        renderPagination(data.total, offset, 'loadKitsPage');
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('vehicles.failedToLoad', { error: escapeHtml(e.message) })}</div>`;
    }
}

async function toggleTireSizes(btn, kitId) {
    const row = document.getElementById(`tire-sizes-${kitId}`);
    if (!row) return;

    if (row.style.display !== 'none') {
        row.style.display = 'none';
        return;
    }

    row.style.display = '';
    const cell = row.querySelector('td');
    cell.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    try {
        const data = await api(`/admin/vehicles/kits/${kitId}/tire-sizes`);
        const items = data.items || [];

        if (items.length === 0) {
            cell.innerHTML = `<div class="${tw.emptyState}">${t('vehicles.noData')}</div>`;
            return;
        }

        const typeLabel = (v) => v === 1 ? t('vehicles.stock') : t('vehicles.tuning');
        const typeBadge = (v) => v === 1 ? tw.badgeGreen : tw.badgeBlue;
        const axleLabel = (v) => {
            if (v === 1) return t('vehicles.front');
            if (v === 2) return t('vehicles.rear');
            return t('vehicles.all');
        };

        cell.innerHTML = `
            <table class="${tw.table}">
            <thead><tr>
                <th class="${tw.th}">${t('vehicles.size')}</th>
                <th class="${tw.th}">${t('vehicles.type')}</th>
                <th class="${tw.th}">${t('vehicles.axle')}</th>
            </tr></thead>
            <tbody>
            ${items.map(ts => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}" data-label="${t('vehicles.size')}">${ts.width}/${ts.height} R${ts.diameter}</td>
                    <td class="${tw.td}" data-label="${t('vehicles.type')}"><span class="${typeBadge(ts.type)}">${typeLabel(ts.type)}</span></td>
                    <td class="${tw.td}" data-label="${t('vehicles.axle')}">${axleLabel(ts.axle)}</td>
                </tr>
            `).join('')}
            </tbody></table>
        `;
    } catch (e) {
        cell.innerHTML = `<div class="${tw.emptyState}">${t('vehicles.failedToLoad', { error: escapeHtml(e.message) })}</div>`;
    }
}

// ==================== Browse: CRUD modals (Brand) ====================

function openBrandModal(brandId, currentName) {
    document.getElementById('vehicleBrandModalTitle').textContent = brandId
        ? t('vehicles.editBrand')
        : t('vehicles.addBrand');
    document.getElementById('vehicleBrandId').value = brandId || '';
    document.getElementById('vehicleBrandName').value = currentName || '';
    document.getElementById('vehicleBrandModal').classList.add('show');
    setTimeout(() => document.getElementById('vehicleBrandName').focus(), 100);
}

async function saveBrand() {
    const id = document.getElementById('vehicleBrandId').value;
    const name = document.getElementById('vehicleBrandName').value.trim();
    if (!name) { showToast(t('vehicles.nameRequired'), 'error'); return; }

    try {
        if (id) {
            await api(`/admin/vehicles/brands/${id}`, {
                method: 'PUT',
                body: JSON.stringify({ name }),
            });
            showToast(t('vehicles.brandUpdated'));
        } else {
            await api('/admin/vehicles/brands', {
                method: 'POST',
                body: JSON.stringify({ name }),
            });
            showToast(t('vehicles.brandCreated'));
        }
        closeModal('vehicleBrandModal');
        loadBrands(brandsOffset);
        loadStats();
    } catch (e) {
        showToast(t('vehicles.saveFailed', { error: e.message }), 'error');
    }
}

async function deleteBrand(id, name) {
    if (!confirm(t('vehicles.deleteBrandConfirm', { name }))) return;
    try {
        await api(`/admin/vehicles/brands/${id}`, { method: 'DELETE' });
        showToast(t('vehicles.brandDeleted'));
        loadBrands(brandsOffset);
        loadStats();
    } catch (e) {
        showToast(t('vehicles.deleteFailed', { error: e.message }), 'error');
    }
}

// ==================== Browse: CRUD modals (Model) ====================

function openModelModal(modelId, brandId, currentName) {
    document.getElementById('vehicleModelModalTitle').textContent = modelId
        ? t('vehicles.editModel')
        : t('vehicles.addModel');
    document.getElementById('vehicleModelId').value = modelId || '';
    document.getElementById('vehicleModelBrandId').value = brandId || '';
    document.getElementById('vehicleModelName').value = currentName || '';
    document.getElementById('vehicleModelModal').classList.add('show');
    setTimeout(() => document.getElementById('vehicleModelName').focus(), 100);
}

async function saveModel() {
    const id = document.getElementById('vehicleModelId').value;
    const brandId = parseInt(document.getElementById('vehicleModelBrandId').value, 10);
    const name = document.getElementById('vehicleModelName').value.trim();
    if (!name) { showToast(t('vehicles.nameRequired'), 'error'); return; }

    try {
        if (id) {
            await api(`/admin/vehicles/models/${id}`, {
                method: 'PUT',
                body: JSON.stringify({ name, brand_id: brandId }),
            });
            showToast(t('vehicles.modelUpdated'));
        } else {
            await api(`/admin/vehicles/brands/${brandId}/models`, {
                method: 'POST',
                body: JSON.stringify({ name }),
            });
            showToast(t('vehicles.modelCreated'));
        }
        closeModal('vehicleModelModal');
        loadModels(currentBrandId, currentBrandName, modelsOffset);
        loadStats();
    } catch (e) {
        showToast(t('vehicles.saveFailed', { error: e.message }), 'error');
    }
}

async function deleteModel(id, name) {
    if (!confirm(t('vehicles.deleteModelConfirm', { name }))) return;
    try {
        await api(`/admin/vehicles/models/${id}`, { method: 'DELETE' });
        showToast(t('vehicles.modelDeleted'));
        loadModels(currentBrandId, currentBrandName, modelsOffset);
        loadStats();
    } catch (e) {
        showToast(t('vehicles.deleteFailed', { error: e.message }), 'error');
    }
}

// ==================== Aliases tab ====================

async function loadAliases(offset = 0) {
    aliasesOffset = offset;
    const container = document.getElementById('vehiclesAliasesContainer');
    if (!container) return;
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    const params = new URLSearchParams({ limit: ALIAS_PAGE_SIZE, offset });
    if (aliasesFilters.search) params.set('search', aliasesFilters.search);
    if (aliasesFilters.brand_id) params.set('brand_id', aliasesFilters.brand_id);
    if (aliasesFilters.source) params.set('source', aliasesFilters.source);

    try {
        const data = await api(`/admin/vehicles/aliases?${params}`);
        const items = data.items || [];

        if (items.length === 0) {
            container.innerHTML = `<div class="${tw.emptyState}">${t('vehicles.noData')}</div>`;
            document.getElementById('vehiclesAliasesPagination').innerHTML = '';
            return;
        }

        const sourceBadge = (s) => {
            const cls = {
                'manual': tw.badgeBlue,
                'auto_import': tw.badgeGray,
                'auto_translit': tw.badgeGreen,
                'auto_model_name': tw.badgeYellow || tw.badgeGray,
            }[s] || tw.badgeGray;
            return `<span class="${cls}">${escapeHtml(s || '')}</span>`;
        };

        container.innerHTML = `
            <div class="overflow-x-auto"><table class="${tw.table}" id="aliasesTable">
            <thead><tr>
                <th class="${tw.th}">${t('vehicles.aliasText')}</th>
                <th class="${tw.th}">${t('vehicles.brand')}</th>
                <th class="${tw.th}">${t('vehicles.model')}</th>
                <th class="${tw.th}">${t('vehicles.source')}</th>
                <th class="${tw.th}">${t('vehicles.confidence')}</th>
                <th class="${tw.th}">${t('common.actions')}</th>
            </tr></thead>
            <tbody>
            ${items.map(a => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}"><strong>${escapeHtml(a.alias)}</strong><br><span class="text-xs text-neutral-500">${escapeHtml(a.alias_normalized)}</span></td>
                    <td class="${tw.td}">${escapeHtml(a.brand_name || '-')}</td>
                    <td class="${tw.td}">${escapeHtml(a.model_name || '-')}</td>
                    <td class="${tw.td}">${sourceBadge(a.source)}</td>
                    <td class="${tw.td}">${a.confidence != null ? Number(a.confidence).toFixed(2) : '-'}</td>
                    <td class="${tw.tdActions}">
                        <button class="admin-only ${tw.btnSecondary} ${tw.btnSm}" onclick='window._pages.vehicles.openAliasModal(${JSON.stringify(a).replace(/'/g, "&#39;")})'>${t('common.edit')}</button>
                        <button class="admin-only ${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.vehicles.deleteAlias(${a.id}, '${escapeHtml(a.alias).replace(/'/g, "\\'")}')">${t('common.delete')}</button>
                    </td>
                </tr>
            `).join('')}
            </tbody></table></div>
        `;

        renderAliasesPagination(data.total, offset);
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('vehicles.failedToLoad', { error: escapeHtml(e.message) })}</div>`;
    }
}

function renderAliasesPagination(total, offset) {
    const pages = Math.ceil(total / ALIAS_PAGE_SIZE);
    const current = Math.floor(offset / ALIAS_PAGE_SIZE);
    document.getElementById('vehiclesAliasesPagination').innerHTML = Array.from(
        { length: Math.min(pages, 20) },
        (_, i) => `<button class="${tw.pageBtn}${i === current ? ' active' : ''}" onclick="window._pages.vehicles.loadAliases(${i * ALIAS_PAGE_SIZE})">${i + 1}</button>`
    ).join('');
}

function applyAliasFilters() {
    aliasesFilters.search = document.getElementById('aliasSearchInput').value.trim();
    aliasesFilters.source = document.getElementById('aliasSourceFilter').value;
    aliasesFilters.brand_id = document.getElementById('aliasBrandFilter').value.trim();
    loadAliases(0);
}

function openAliasModal(existing) {
    const isEdit = !!existing;
    document.getElementById('vehicleAliasModalTitle').textContent = isEdit
        ? t('vehicles.editAlias')
        : t('vehicles.addAlias');
    document.getElementById('vehicleAliasId').value = existing?.id || '';
    document.getElementById('vehicleAliasText').value = existing?.alias || '';
    document.getElementById('vehicleAliasBrandId').value = existing?.brand_id || '';
    document.getElementById('vehicleAliasModelId').value = existing?.model_id ?? '';
    document.getElementById('vehicleAliasConfidence').value = existing?.confidence != null ? existing.confidence : '';
    document.getElementById('vehicleAliasModal').classList.add('show');
    setTimeout(() => document.getElementById('vehicleAliasText').focus(), 100);
}

async function saveAlias() {
    const id = document.getElementById('vehicleAliasId').value;
    const alias = document.getElementById('vehicleAliasText').value.trim();
    const brandId = parseInt(document.getElementById('vehicleAliasBrandId').value, 10);
    const modelIdRaw = document.getElementById('vehicleAliasModelId').value.trim();
    const modelId = modelIdRaw ? parseInt(modelIdRaw, 10) : null;
    const confRaw = document.getElementById('vehicleAliasConfidence').value.trim();
    const confidence = confRaw ? parseFloat(confRaw) : null;

    if (!alias) { showToast(t('vehicles.aliasRequired'), 'error'); return; }
    if (!brandId || isNaN(brandId)) { showToast(t('vehicles.brandIdRequired'), 'error'); return; }

    const payload = { alias, brand_id: brandId, model_id: modelId, confidence };

    try {
        if (id) {
            await api(`/admin/vehicles/aliases/${id}`, {
                method: 'PUT',
                body: JSON.stringify(payload),
            });
            showToast(t('vehicles.aliasUpdated'));
        } else {
            await api('/admin/vehicles/aliases', {
                method: 'POST',
                body: JSON.stringify(payload),
            });
            showToast(t('vehicles.aliasCreated'));
        }
        closeModal('vehicleAliasModal');
        loadAliases(aliasesOffset);
    } catch (e) {
        showToast(t('vehicles.saveFailed', { error: e.message }), 'error');
    }
}

async function deleteAlias(id, alias) {
    if (!confirm(t('vehicles.deleteAliasConfirm', { alias }))) return;
    try {
        await api(`/admin/vehicles/aliases/${id}`, { method: 'DELETE' });
        showToast(t('vehicles.aliasDeleted'));
        loadAliases(aliasesOffset);
    } catch (e) {
        showToast(t('vehicles.deleteFailed', { error: e.message }), 'error');
    }
}

async function regenerateAliases() {
    if (!confirm(t('vehicles.regenerateConfirm'))) return;
    try {
        await api('/admin/vehicles/aliases/regenerate', { method: 'POST' });
        showToast(t('vehicles.regenerateStarted'));
        setTimeout(() => loadAliases(0), 3000);  // reload after brief wait
    } catch (e) {
        showToast(t('vehicles.regenerateFailed', { error: e.message }), 'error');
    }
}

// ==================== Lookup tester ====================

async function testLookup() {
    const utterance = document.getElementById('lookupInput').value.trim();
    const resultEl = document.getElementById('lookupResult');
    if (!utterance) { resultEl.innerHTML = ''; return; }
    resultEl.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    try {
        const res = await api('/admin/vehicles/lookup', {
            method: 'POST',
            body: JSON.stringify({ utterance }),
        });

        if (res.ambiguous) {
            const matches = (res.ambiguous_matches || []).map(m =>
                `<li>${escapeHtml(m.brand_name)} — ${escapeHtml(m.model_name || '(brand only)')}</li>`
            ).join('');
            resultEl.innerHTML = `
                <div class="p-3 rounded-md bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800">
                    <div class="font-medium text-yellow-800 dark:text-yellow-200">${t('vehicles.lookupAmbiguous')}</div>
                    ${res.brand_name ? `<div class="text-sm mt-1">${t('vehicles.brand')}: <strong>${escapeHtml(res.brand_name)}</strong></div>` : ''}
                    <ul class="text-sm mt-2 ml-4 list-disc">${matches}</ul>
                </div>`;
        } else if (res.brand_id) {
            resultEl.innerHTML = `
                <div class="p-3 rounded-md bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800">
                    <div class="font-medium text-green-800 dark:text-green-200">${t('vehicles.lookupFound')}</div>
                    <div class="text-sm mt-1">${t('vehicles.brand')}: <strong>${escapeHtml(res.brand_name)}</strong></div>
                    ${res.model_name ? `<div class="text-sm">${t('vehicles.model')}: <strong>${escapeHtml(res.model_name)}</strong></div>` : ''}
                    <div class="text-xs text-neutral-500 mt-1">${t('vehicles.source')}: ${escapeHtml(res.source || '-')}</div>
                </div>`;
        } else {
            resultEl.innerHTML = `
                <div class="p-3 rounded-md bg-neutral-50 dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700">
                    <div class="text-neutral-600 dark:text-neutral-400">${t('vehicles.lookupNotFound')}</div>
                </div>`;
        }
    } catch (e) {
        resultEl.innerHTML = `<div class="${tw.emptyState}">${escapeHtml(e.message)}</div>`;
    }
}

// ==================== Import tab ====================

async function loadImportHistory(offset = 0) {
    historyOffset = offset;
    const container = document.getElementById('vehiclesHistoryContainer');
    if (!container) return;
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    try {
        const data = await api(`/admin/vehicles/import/history?limit=20&offset=${offset}`);
        const items = data.items || [];

        if (items.length === 0) {
            container.innerHTML = `<div class="${tw.emptyState}">${t('vehicles.noImportHistory')}</div>`;
            return;
        }

        const statusBadge = (s) => {
            const cls = {
                'completed': tw.badgeGreen,
                'running': tw.badgeBlue,
                'dryrun': tw.badgeGray,
                'failed': tw.badgeRed || 'text-red-800 bg-red-100',
            }[s] || tw.badgeGray;
            return `<span class="${cls}">${escapeHtml(s || '')}</span>`;
        };

        container.innerHTML = `
            <div class="overflow-x-auto"><table class="${tw.table}">
            <thead><tr>
                <th class="${tw.th}">${t('vehicles.historyId')}</th>
                <th class="${tw.th}">${t('vehicles.historyMode')}</th>
                <th class="${tw.th}">${t('vehicles.status')}</th>
                <th class="${tw.th}">${t('vehicles.historyArchive')}</th>
                <th class="${tw.th}">${t('vehicles.historyBrands')}</th>
                <th class="${tw.th}">${t('vehicles.historyModels')}</th>
                <th class="${tw.th}">${t('vehicles.historyAliases')}</th>
                <th class="${tw.th}">${t('vehicles.historyStarted')}</th>
                <th class="${tw.th}">${t('vehicles.historyBy')}</th>
                <th class="${tw.th}">${t('common.actions')}</th>
            </tr></thead>
            <tbody>
            ${items.map(h => {
                const bDelta = `+${h.brand_added ?? 0} / ~${h.brand_updated ?? 0} / skip ${h.brand_skipped_manual ?? 0}`;
                const mDelta = `+${h.model_added ?? 0} / ~${h.model_updated ?? 0} / skip ${h.model_skipped_manual ?? 0}`;
                const canApply = h.status === 'dryrun';
                return `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}">${h.id}</td>
                    <td class="${tw.td}">${escapeHtml(h.mode || '')}</td>
                    <td class="${tw.td}">${statusBadge(h.status)}</td>
                    <td class="${tw.td}"><span class="text-xs">${escapeHtml(h.archive_name || '-')}</span></td>
                    <td class="${tw.td}"><span class="text-xs">${bDelta}</span></td>
                    <td class="${tw.td}"><span class="text-xs">${mDelta}</span></td>
                    <td class="${tw.td}">${h.aliases_regenerated ?? 0}</td>
                    <td class="${tw.td}"><span class="text-xs">${h.started_at ? formatDate(h.started_at) : '-'}</span></td>
                    <td class="${tw.td}"><span class="text-xs">${escapeHtml(h.triggered_by_username || '-')}</span></td>
                    <td class="${tw.tdActions}">
                        ${canApply ? `<button class="admin-only ${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.vehicles.applyImport(${h.id})">${t('vehicles.apply')}</button>` : ''}
                        ${canApply ? `<button class="admin-only ${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.vehicles.discardImport(${h.id})">${t('vehicles.discard')}</button>` : ''}
                        ${h.error_message ? `<button class="${tw.btnSecondary} ${tw.btnSm}" onclick="alert('${escapeHtml(h.error_message).replace(/'/g, "\\'")}')">${t('vehicles.error')}</button>` : ''}
                    </td>
                </tr>`;
            }).join('')}
            </tbody></table></div>
        `;
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('vehicles.failedToLoad', { error: escapeHtml(e.message) })}</div>`;
    }
}

function openImportModal() {
    currentStagedImport = null;
    document.getElementById('vehicleImportZipInput').value = '';
    document.getElementById('vehicleImportPreview').innerHTML = '';
    document.getElementById('vehicleImportApplyBtn').style.display = 'none';
    document.getElementById('vehicleImportDiscardBtn').style.display = 'none';
    document.getElementById('vehicleImportUploadBtn').style.display = '';
    document.getElementById('vehicleImportModal').classList.add('show');
}

async function uploadImportZip() {
    const fileInput = document.getElementById('vehicleImportZipInput');
    const file = fileInput.files[0];
    if (!file) { showToast(t('vehicles.selectFileFirst'), 'error'); return; }
    if (!file.name.toLowerCase().endsWith('.zip')) {
        showToast(t('vehicles.zipOnly'), 'error');
        return;
    }

    const btn = document.getElementById('vehicleImportUploadBtn');
    btn.disabled = true;
    const origText = btn.textContent;
    btn.textContent = t('common.loading');

    const previewEl = document.getElementById('vehicleImportPreview');
    previewEl.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    try {
        const formData = new FormData();
        formData.append('file', file);
        const res = await api('/admin/vehicles/import/upload', {
            method: 'POST',
            body: formData,
        });

        currentStagedImport = res;
        const dr = res.diff_report || {};
        const b = dr.brands || {};
        const m = dr.models || {};
        const k = dr.kits || {};
        const ts = dr.tire_sizes || {};

        previewEl.innerHTML = `
            <div class="p-3 bg-neutral-50 dark:bg-neutral-800 rounded-md text-sm mb-3">
                <div class="font-medium mb-2 text-neutral-800 dark:text-neutral-200">${t('vehicles.dryRunPreview')} (history_id=${res.history_id})</div>
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <div class="font-medium text-neutral-700 dark:text-neutral-300">${t('vehicles.brands')}</div>
                        <div class="text-xs mt-1">
                            <div>+ ${b.added_count || 0} ${t('vehicles.added')}</div>
                            <div>~ ${b.updated_count || 0} ${t('vehicles.updated')}</div>
                            <div>⏸ ${b.skipped_manual_count || 0} ${t('vehicles.skippedManual')}</div>
                            <div>? ${b.missing_in_source_count || 0} ${t('vehicles.missingInSource')}</div>
                        </div>
                    </div>
                    <div>
                        <div class="font-medium text-neutral-700 dark:text-neutral-300">${t('vehicles.models')}</div>
                        <div class="text-xs mt-1">
                            <div>+ ${m.added_count || 0} ${t('vehicles.added')}</div>
                            <div>~ ${m.updated_count || 0} ${t('vehicles.updated')}</div>
                            <div>⏸ ${m.skipped_manual_count || 0} ${t('vehicles.skippedManual')}</div>
                            <div>? ${m.missing_in_source_count || 0} ${t('vehicles.missingInSource')}</div>
                        </div>
                    </div>
                </div>
                <div class="text-xs text-neutral-500 mt-2">
                    ${t('vehicles.kits')}: ${k.csv_count || 0} · ${t('vehicles.tireSizes')}: ${ts.csv_count || 0}
                </div>
                ${(b.added_sample?.length || 0) > 0 ? `<div class="text-xs mt-2"><strong>${t('vehicles.newBrandsSample')}:</strong> ${b.added_sample.map(escapeHtml).join(', ')}</div>` : ''}
                ${(b.missing_in_source_sample?.length || 0) > 0 ? `<div class="text-xs mt-1 text-amber-600 dark:text-amber-400"><strong>${t('vehicles.missingBrandsSample')}:</strong> ${b.missing_in_source_sample.map(x => escapeHtml(x.name)).join(', ')}</div>` : ''}
            </div>
        `;

        document.getElementById('vehicleImportApplyBtn').style.display = '';
        document.getElementById('vehicleImportDiscardBtn').style.display = '';
        document.getElementById('vehicleImportUploadBtn').style.display = 'none';
    } catch (e) {
        previewEl.innerHTML = `<div class="${tw.emptyState} text-red-600">${escapeHtml(e.message)}</div>`;
        showToast(t('vehicles.uploadFailed', { error: e.message }), 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = origText;
    }
}

async function applyStagedImport() {
    if (!currentStagedImport?.history_id) return;
    const hid = currentStagedImport.history_id;
    if (!confirm(t('vehicles.applyConfirm'))) return;

    const btn = document.getElementById('vehicleImportApplyBtn');
    btn.disabled = true;

    try {
        await api(`/admin/vehicles/import/apply/${hid}`, { method: 'POST' });
        showToast(t('vehicles.applyStarted'));
        // Start polling
        pollImportStatus(hid);
    } catch (e) {
        showToast(t('vehicles.applyFailed', { error: e.message }), 'error');
        btn.disabled = false;
    }
}

async function applyImport(historyId) {
    if (!confirm(t('vehicles.applyConfirm'))) return;
    try {
        await api(`/admin/vehicles/import/apply/${historyId}`, { method: 'POST' });
        showToast(t('vehicles.applyStarted'));
        pollImportStatus(historyId);
    } catch (e) {
        showToast(t('vehicles.applyFailed', { error: e.message }), 'error');
    }
}

function pollImportStatus(historyId) {
    if (applyPollHandle) clearInterval(applyPollHandle);
    const previewEl = document.getElementById('vehicleImportPreview');
    let elapsed = 0;
    applyPollHandle = setInterval(async () => {
        elapsed += 3;
        try {
            const res = await api(`/admin/vehicles/import/history/${historyId}`);
            if (res.status === 'completed') {
                clearInterval(applyPollHandle);
                applyPollHandle = null;
                if (previewEl) previewEl.innerHTML = `<div class="p-3 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-md text-sm">${t('vehicles.applyDone')}</div>`;
                showToast(t('vehicles.applyDone'));
                closeModal('vehicleImportModal');
                loadStats();
                loadImportHistory();
            } else if (res.status === 'failed') {
                clearInterval(applyPollHandle);
                applyPollHandle = null;
                if (previewEl) previewEl.innerHTML = `<div class="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-md text-sm">${t('vehicles.applyFailedMsg', { error: res.error_message || '' })}</div>`;
                showToast(t('vehicles.applyFailedMsg', { error: res.error_message || '' }), 'error');
            } else {
                if (previewEl) previewEl.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div><div class="text-center text-xs text-neutral-500 mt-2">${t('vehicles.applyRunning')} (${elapsed}s)</div>`;
            }
        } catch (e) {
            // Keep polling on transient errors
        }
        if (elapsed > 300) {  // hard cap 5 min
            clearInterval(applyPollHandle);
            applyPollHandle = null;
        }
    }, 3000);
}

async function discardStagedImport() {
    if (!currentStagedImport?.history_id) return;
    await discardImport(currentStagedImport.history_id);
    closeModal('vehicleImportModal');
}

async function discardImport(historyId) {
    if (!confirm(t('vehicles.discardConfirm'))) return;
    try {
        await api(`/admin/vehicles/import/staged/${historyId}`, { method: 'DELETE' });
        showToast(t('vehicles.discarded'));
        loadImportHistory(historyOffset);
    } catch (e) {
        showToast(t('vehicles.deleteFailed', { error: e.message }), 'error');
    }
}

// ==================== Pagination + Navigation ====================

function renderPagination(total, offset, fnName) {
    const pages = Math.ceil(total / PAGE_SIZE);
    const current = Math.floor(offset / PAGE_SIZE);
    document.getElementById('vehiclesPagination').innerHTML = Array.from(
        { length: Math.min(pages, 10) },
        (_, i) => `<button class="${tw.pageBtn}${i === current ? ' active' : ''}" onclick="window._pages.vehicles.${fnName}(${i * PAGE_SIZE})">${i + 1}</button>`
    ).join('');
}

function selectBrand(brandId, brandName) {
    document.getElementById('vehiclesSearch').value = '';
    loadModels(brandId, brandName);
}

function selectModel(modelId, modelName) {
    document.getElementById('vehiclesSearch').value = '';
    loadKits(modelId, modelName);
}

function goToBrands() {
    document.getElementById('vehiclesSearch').value = '';
    loadBrands();
}

function goToModels() {
    document.getElementById('vehiclesSearch').value = '';
    loadModels(currentBrandId, currentBrandName);
}

function loadModelsPage(offset) {
    loadModels(currentBrandId, currentBrandName, offset);
}

function loadKitsPage(offset) {
    loadKits(currentModelId, currentModelName, offset);
}

function search() {
    if (currentLevel === 'brands') loadBrands();
    else if (currentLevel === 'models') loadModels(currentBrandId, currentBrandName);
}

// ==================== Init ====================

export function init() {
    registerPageLoader('vehicles', () => {
        loadStats();
        switchTab(currentTab);
    });
}

window._pages = window._pages || {};
window._pages.vehicles = {
    // tabs
    switchTab,
    // browse
    loadBrands,
    loadModels,
    loadKits,
    loadModelsPage,
    loadKitsPage,
    selectBrand,
    selectModel,
    goToBrands,
    goToModels,
    toggleTireSizes,
    search,
    openBrandModal,
    saveBrand,
    deleteBrand,
    openModelModal,
    saveModel,
    deleteModel,
    // aliases
    loadAliases,
    applyAliasFilters,
    openAliasModal,
    saveAlias,
    deleteAlias,
    regenerateAliases,
    testLookup,
    // import
    loadImportHistory,
    openImportModal,
    uploadImportZip,
    applyStagedImport,
    applyImport,
    discardStagedImport,
    discardImport,
};
