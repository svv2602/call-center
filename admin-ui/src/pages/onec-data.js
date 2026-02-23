import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { escapeHtml } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import * as tw from '../tw.js';

let _tenants = [];
let _pickupLoaded = false;

async function _loadTenants() {
    try {
        const data = await api('/admin/tenants?is_active=true&limit=100');
        _tenants = data.tenants || [];
    } catch {
        _tenants = [];
    }
}

function _populateNetworkSelects() {
    ['onecPickupNetwork', 'onecStockNetwork'].forEach(id => {
        const sel = document.getElementById(id);
        if (!sel || _tenants.length === 0) return;
        const current = sel.value;
        sel.innerHTML = '';
        for (const ten of _tenants) {
            if (!ten.network_id) continue;
            const opt = document.createElement('option');
            opt.value = ten.network_id;
            opt.textContent = `${ten.name} (${ten.network_id})`;
            if (ten.network_id === current) opt.selected = true;
            sel.appendChild(opt);
        }
        if (sel.options.length === 0) {
            const opt = document.createElement('option');
            opt.value = 'ProKoleso';
            opt.textContent = 'ProKoleso';
            sel.appendChild(opt);
        }
    });
}

function toggleSection(btn) {
    // Delegate to shared accordion toggle
    if (window._app?.toggleAccordion) window._app.toggleAccordion(btn);

    // Load pickup data on first expand
    const card = btn.closest('.acc-section');
    if (card && card.dataset.open === 'true') {
        const body = card.querySelector('.acc-body');
        if (body?.querySelector('#onecPickupContainer') && !_pickupLoaded) {
            _pickupLoaded = true;
            loadPickupPoints();
        }
    }
}

async function loadOnecData() {
    _pickupLoaded = false;
    await _loadTenants();
    _populateNetworkSelects();
    await loadStatus();
}

async function loadStatus() {
    const container = document.getElementById('onecStatusContainer');
    if (!container) return;
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    try {
        const data = await api('/admin/onec/status');
        let statusBadge;
        if (data.status === 'reachable') {
            statusBadge = `<span class="${tw.badgeGreen}">${t('onec.connected')}</span>`;
        } else if (data.status === 'not_configured') {
            statusBadge = `<span class="${tw.badge}">${t('onec.notConfigured')}</span>`;
        } else {
            statusBadge = `<span class="${tw.badgeRed}">${t('onec.disconnected')}</span>`;
        }

        let cacheInfo = '';
        const networks = Object.keys(data.pickup_cache || {});
        if (networks.length > 0) {
            const rows = networks.map(net => {
                const pc = data.pickup_cache[net];
                const sc = (data.stock_cache || {})[net];
                const age = pc ? _formatAge(pc.cache_age_seconds) : '—';
                const pts = pc ? pc.count : 0;
                const skus = sc ? sc.count : 0;
                return `<tr class="${tw.trHover}">
                    <td class="${tw.td} font-medium">${escapeHtml(net)}</td>
                    <td class="${tw.td}">${pts}</td>
                    <td class="${tw.td}">${skus}</td>
                    <td class="${tw.td}">${age}</td>
                </tr>`;
            }).join('');
            cacheInfo = `
                <table class="${tw.table} mt-3">
                    <thead><tr>
                        <th class="${tw.th}">${t('onec.network')}</th>
                        <th class="${tw.th}">${t('onec.pickupPoints')}</th>
                        <th class="${tw.th}">SKU</th>
                        <th class="${tw.th}">${t('onec.cacheAge')}</th>
                    </tr></thead>
                    <tbody>${rows}</tbody>
                </table>`;
        }

        container.innerHTML = `
            <div class="flex items-center gap-3 mb-2">
                <span class="text-sm text-neutral-500 dark:text-neutral-400">${t('onec.status')}:</span>
                ${statusBadge}
                ${data.error ? `<span class="text-xs text-red-500">${escapeHtml(data.error)}</span>` : ''}
            </div>
            ${cacheInfo}
        `;

        // SOAP / REST / AI orders cards
        _renderSoapStatus(data);
    } catch (err) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('common.failedToLoad', { error: err.message })}</div>`;
    }
}

function _soapBadge(status) {
    if (status === 'reachable') return `<span class="${tw.badgeGreen}">${t('onec.connected')}</span>`;
    if (status === 'not_configured') return `<span class="${tw.badge}">${t('onec.notConfigured')}</span>`;
    if (status === 'unreachable') return `<span class="${tw.badgeRed}">${t('onec.disconnected')}</span>`;
    return `<span class="${tw.badgeYellow}">${escapeHtml(status)}</span>`;
}

function _renderSoapStatus(data) {
    const container = document.getElementById('onecSoapStatusContainer');
    if (!container) return;

    // Only show if we have SOAP data
    if (!data.soap_status && data.ai_orders_total == null) {
        container.innerHTML = '';
        return;
    }

    const aiOrders = data.ai_orders_total != null ? data.ai_orders_total : '—';

    let html = `<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">`;

    // SOAP Status
    if (data.soap_status) {
        html += `<div class="rounded-lg border border-neutral-200 dark:border-neutral-700 p-3">
            <div class="text-xs text-neutral-500 dark:text-neutral-400 mb-1">${t('onec.soapStatus')}</div>
            <div class="text-sm font-medium mb-1">${_soapBadge(data.soap_status)}</div>
            ${data.soap_endpoint ? `<div class="text-xs text-neutral-400 dark:text-neutral-500 font-mono truncate" title="${escapeHtml(data.soap_endpoint)}">${escapeHtml(data.soap_endpoint)}</div>` : ''}
        </div>`;
    }

    // REST Status (same as main status)
    if (data.rest_endpoint) {
        html += `<div class="rounded-lg border border-neutral-200 dark:border-neutral-700 p-3">
            <div class="text-xs text-neutral-500 dark:text-neutral-400 mb-1">${t('onec.restStatus')}</div>
            <div class="text-sm font-medium mb-1">${_soapBadge(data.status || 'not_configured')}</div>
            <div class="text-xs text-neutral-400 dark:text-neutral-500 font-mono truncate" title="${escapeHtml(data.rest_endpoint)}">${escapeHtml(data.rest_endpoint)}</div>
        </div>`;
    }

    // AI Orders
    html += `<div class="rounded-lg border border-neutral-200 dark:border-neutral-700 p-3">
        <div class="text-xs text-neutral-500 dark:text-neutral-400 mb-1">${t('onec.aiOrders')}</div>
        <div class="text-2xl font-bold text-neutral-900 dark:text-neutral-50">${escapeHtml(String(aiOrders))}</div>
        <div class="text-xs text-neutral-400 dark:text-neutral-500">${t('onec.aiOrdersHint')}</div>
    </div>`;

    // SOAP Timeout
    if (data.soap_timeout) {
        html += `<div class="rounded-lg border border-neutral-200 dark:border-neutral-700 p-3">
            <div class="text-xs text-neutral-500 dark:text-neutral-400 mb-1">${t('onec.soapTimeout')}</div>
            <div class="text-2xl font-bold text-neutral-900 dark:text-neutral-50">${data.soap_timeout}<span class="text-sm font-normal text-neutral-400 ml-1">${t('common.seconds')}</span></div>
        </div>`;
    }

    html += `</div>`;
    container.innerHTML = html;
}

function _formatAge(seconds) {
    if (seconds == null) return '—';
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

async function loadPickupPoints() {
    const container = document.getElementById('onecPickupContainer');
    if (!container) return;
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    const network = document.getElementById('onecPickupNetwork')?.value || 'ProKoleso';
    const city = document.getElementById('onecPickupCity')?.value || '';

    try {
        const params = new URLSearchParams({ network });
        if (city) params.set('city', city);
        const data = await api(`/admin/onec/pickup-points?${params}`);

        if (!data.points || data.points.length === 0) {
            container.innerHTML = `<div class="${tw.emptyState}">${t('onec.noData')}</div>`;
            return;
        }

        const sourceLabel = data.source === 'cache'
            ? `<span class="${tw.badge}">cache</span>`
            : data.source === 'live'
                ? `<span class="${tw.badgeGreen}">live</span>`
                : `<span class="${tw.badgeRed}">${data.source}</span>`;

        const ageLabel = data.cache_age_seconds != null
            ? `<span class="text-xs text-neutral-400 ml-2">${t('onec.cacheAge')}: ${_formatAge(data.cache_age_seconds)}</span>`
            : '';

        const rows = data.points.map(p => `
            <tr class="${tw.trHover}">
                <td class="${tw.td} font-mono text-xs">${escapeHtml(p.id || '')}</td>
                <td class="${tw.td}">${escapeHtml(p.address || '')}</td>
                <td class="${tw.td}">${escapeHtml(p.city || '')}</td>
                <td class="${tw.td}">${escapeHtml(p.type || '')}</td>
            </tr>
        `).join('');

        container.innerHTML = `
            <div class="flex items-center gap-2 mb-2">
                <span class="text-sm text-neutral-500 dark:text-neutral-400">${t('common.showing', { shown: data.points.length, total: data.total })}</span>
                ${sourceLabel}
                ${ageLabel}
            </div>
            <div class="overflow-x-auto">
                <table class="${tw.table}">
                    <thead><tr>
                        <th class="${tw.th}">ID</th>
                        <th class="${tw.th}">${t('onec.address')}</th>
                        <th class="${tw.th}">${t('onec.city')}</th>
                        <th class="${tw.th}">${t('onec.type')}</th>
                    </tr></thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        `;
    } catch (err) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('common.failedToLoad', { error: err.message })}</div>`;
    }
}

async function refreshPickupFromOnec() {
    const network = document.getElementById('onecPickupNetwork')?.value || 'ProKoleso';
    const city = document.getElementById('onecPickupCity')?.value || '';
    const container = document.getElementById('onecPickupContainer');
    if (container) container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    try {
        const params = new URLSearchParams({ network });
        if (city) params.set('city', city);
        const data = await api(`/admin/onec/pickup-points?${params}`);
        showToast(`${t('onec.pickupPoints')}: ${data.total} (${data.source})`, 'success');
    } catch (err) {
        showToast(t('common.failedToLoad', { error: err.message }), 'error');
    }
    await loadPickupPoints();
}

async function lookupStock() {
    const container = document.getElementById('onecStockResult');
    if (!container) return;

    const network = document.getElementById('onecStockNetwork')?.value || 'ProKoleso';
    const sku = document.getElementById('onecStockSku')?.value?.trim();

    if (!sku) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('onec.skuPlaceholder')}</div>`;
        return;
    }

    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    try {
        const params = new URLSearchParams({ network, sku });
        const data = await api(`/admin/onec/stock-lookup?${params}`);

        if (!data.found) {
            container.innerHTML = `<div class="${tw.emptyState}">${t('onec.notFound')} — ${escapeHtml(sku)}</div>`;
            return;
        }

        const stock = data.data || {};
        const fields = [
            ['SKU', sku],
            [t('onec.price'), stock.price != null ? `${stock.price} грн` : '—'],
            [t('onec.quantity'), stock.quantity != null ? stock.quantity : (stock.qty != null ? stock.qty : '—')],
            [t('onec.country'), stock.country || stock.Country || '—'],
            [t('onec.year'), stock.year || stock.Year || '—'],
        ];

        const knownKeys = new Set(['price', 'quantity', 'qty', 'country', 'Country', 'year', 'Year']);
        for (const [key, val] of Object.entries(stock)) {
            if (!knownKeys.has(key) && val != null && val !== '') {
                fields.push([key, typeof val === 'object' ? JSON.stringify(val) : String(val)]);
            }
        }

        const rows = fields.map(([label, val]) => `
            <div class="flex items-center gap-3 py-1.5 border-b border-neutral-100 dark:border-neutral-800">
                <span class="text-xs text-neutral-500 dark:text-neutral-400 w-24 shrink-0">${escapeHtml(label)}</span>
                <span class="text-sm text-neutral-800 dark:text-neutral-200 font-medium">${escapeHtml(String(val))}</span>
            </div>
        `).join('');

        container.innerHTML = `<div class="mt-2">${rows}</div>`;
    } catch (err) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('common.failedToLoad', { error: err.message })}</div>`;
    }
}

export function init() {
    registerPageLoader('onec-data', loadOnecData);
    window._pages = window._pages || {};
    window._pages['onec-data'] = {
        loadOnecData,
        loadPickupPoints,
        refreshPickupFromOnec,
        lookupStock,
        toggleSection,
    };
}
