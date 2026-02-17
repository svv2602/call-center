import { api } from '../api.js';
import { escapeHtml, formatDate } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import * as tw from '../tw.js';

// State
let currentLevel = 'brands'; // brands | models | kits
let currentBrandId = null;
let currentBrandName = '';
let currentModelId = null;
let currentModelName = '';
let brandsOffset = 0;
let modelsOffset = 0;
let kitsOffset = 0;

const PAGE_SIZE = 50;

// --- Stats ---

async function loadStats() {
    const el = document.getElementById('vehiclesStats');
    el.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;
    try {
        const data = await api('/admin/vehicles/stats/');
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

// --- Breadcrumb ---

function renderBreadcrumb() {
    const el = document.getElementById('vehiclesBreadcrumb');
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

// --- Brands ---

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
        const data = await api(`/admin/vehicles/brands/?${params}`);
        const items = data.items || [];

        if (items.length === 0) {
            container.innerHTML = `<div class="${tw.emptyState}">${t('vehicles.noData')}</div>`;
            document.getElementById('vehiclesPagination').innerHTML = '';
            return;
        }

        container.innerHTML = `
            <div class="overflow-x-auto"><table class="${tw.table}">
            <thead><tr>
                <th class="${tw.th}">${t('vehicles.brand')}</th>
                <th class="${tw.th}">${t('vehicles.models')}</th>
            </tr></thead>
            <tbody>
            ${items.map(b => `
                <tr class="${tw.trHover} cursor-pointer" onclick="window._pages.vehicles.selectBrand(${b.id}, '${escapeHtml(b.name).replace(/'/g, "\\'")}')">
                    <td class="${tw.td}">${escapeHtml(b.name)}</td>
                    <td class="${tw.td}">${b.model_count}</td>
                </tr>
            `).join('')}
            </tbody></table></div>
        `;

        renderPagination(data.total, offset, 'loadBrands');
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('vehicles.failedToLoad', { error: escapeHtml(e.message) })}</div>`;
    }
}

// --- Models ---

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
        const data = await api(`/admin/vehicles/brands/${brandId}/models/?${params}`);
        const items = data.items || [];

        if (items.length === 0) {
            container.innerHTML = `
                <button class="${tw.btnSecondary} mb-3" onclick="window._pages.vehicles.goToBrands()">${t('vehicles.back')}</button>
                <div class="${tw.emptyState}">${t('vehicles.noData')}</div>
            `;
            document.getElementById('vehiclesPagination').innerHTML = '';
            return;
        }

        container.innerHTML = `
            <button class="${tw.btnSecondary} mb-3" onclick="window._pages.vehicles.goToBrands()">${t('vehicles.back')}</button>
            <div class="overflow-x-auto"><table class="${tw.table}">
            <thead><tr>
                <th class="${tw.th}">${t('vehicles.model')}</th>
                <th class="${tw.th}">${t('vehicles.kits')}</th>
            </tr></thead>
            <tbody>
            ${items.map(m => `
                <tr class="${tw.trHover} cursor-pointer" onclick="window._pages.vehicles.selectModel(${m.id}, '${escapeHtml(m.name).replace(/'/g, "\\'")}')">
                    <td class="${tw.td}">${escapeHtml(m.name)}</td>
                    <td class="${tw.td}">${m.kit_count}</td>
                </tr>
            `).join('')}
            </tbody></table></div>
        `;

        renderPagination(data.total, offset, 'loadModelsPage');
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('vehicles.failedToLoad', { error: escapeHtml(e.message) })}</div>`;
    }
}

// --- Kits + Tire Sizes ---

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
        const data = await api(`/admin/vehicles/models/${modelId}/kits/?${params}`);
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
            <div class="overflow-x-auto"><table class="${tw.table}">
            <thead><tr>
                <th class="${tw.th}">${t('vehicles.year')}</th>
                <th class="${tw.th}">${t('vehicles.trim')}</th>
                <th class="${tw.th}">${t('vehicles.pcd')}</th>
                <th class="${tw.th}">${t('vehicles.bolts')}</th>
                <th class="${tw.th}">${t('vehicles.dia')}</th>
                <th class="${tw.th}">${t('vehicles.tireSizes')}</th>
                <th class="${tw.th}"></th>
            </tr></thead>
            <tbody>
            ${items.map(k => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}">${k.year}</td>
                    <td class="${tw.td}">${escapeHtml(k.name || '-')}</td>
                    <td class="${tw.td}">${k.pcd ?? '-'}</td>
                    <td class="${tw.td}">${k.bolt_count ?? '-'}${k.bolt_size ? ' / ' + escapeHtml(k.bolt_size) : ''}</td>
                    <td class="${tw.td}">${k.dia ?? '-'}</td>
                    <td class="${tw.td}">${k.tire_size_count}</td>
                    <td class="${tw.td}">
                        <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.vehicles.toggleTireSizes(this, ${k.id})">${t('vehicles.tireSizes')}</button>
                    </td>
                </tr>
                <tr class="tire-sizes-row" id="tire-sizes-${k.id}" style="display:none">
                    <td colspan="7" class="${tw.td} bg-neutral-50 dark:bg-neutral-800/50"></td>
                </tr>
            `).join('')}
            </tbody></table></div>
        `;

        renderPagination(data.total, offset, 'loadKitsPage');
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('vehicles.failedToLoad', { error: escapeHtml(e.message) })}</div>`;
    }
}

// --- Tire sizes expand ---

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
        const data = await api(`/admin/vehicles/kits/${kitId}/tire-sizes/`);
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
                    <td class="${tw.td}">${ts.width}/${ts.height} R${ts.diameter}</td>
                    <td class="${tw.td}"><span class="${typeBadge(ts.type)}">${typeLabel(ts.type)}</span></td>
                    <td class="${tw.td}">${axleLabel(ts.axle)}</td>
                </tr>
            `).join('')}
            </tbody></table>
        `;
    } catch (e) {
        cell.innerHTML = `<div class="${tw.emptyState}">${t('vehicles.failedToLoad', { error: escapeHtml(e.message) })}</div>`;
    }
}

// --- Pagination ---

function renderPagination(total, offset, fnName) {
    const pages = Math.ceil(total / PAGE_SIZE);
    const current = Math.floor(offset / PAGE_SIZE);
    document.getElementById('vehiclesPagination').innerHTML = Array.from(
        { length: Math.min(pages, 10) },
        (_, i) => `<button class="${tw.pageBtn}${i === current ? ' active' : ''}" onclick="window._pages.vehicles.${fnName}(${i * PAGE_SIZE})">${i + 1}</button>`
    ).join('');
}

// --- Navigation helpers ---

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

// --- Init ---

export function init() {
    registerPageLoader('vehicles', () => {
        loadStats();
        loadBrands();
    });
}

window._pages = window._pages || {};
window._pages.vehicles = {
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
};
