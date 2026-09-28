// «Акции» — network promotions the bot speaks (table `promotions`, API
// src/api/promotions.py). A promotion beats the network's standard conditions
// only for what its «overrides» name; the bot mentions it only when the
// network has sales enabled.
//
// XSS: escapeHtml() does not escape `"`, so promotion/article/tenant data is
// never interpolated into attributes — rows carry an index into _items and
// form fields are filled through DOM properties.

import { api, getToken } from '../api.js';
import { showToast } from '../notifications.js';
import { escapeHtml, closeModal, showModal } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { hasPermission } from '../auth.js';
import { t } from '../i18n.js';
import * as tw from '../tw.js';

// Mirrors src/api/promotions.py (BOT_TEXT_MAX, STATUSES) and
// src/agent/network_policy.py SERVICE_LABELS — the API rejects anything else.
const BOT_TEXT_MAX = 600;
const STATUSES = ['active', 'upcoming', 'expired', 'all'];
const PARTNER_SERVICES = ['fitting', 'storage'];
const KB_CATEGORY = 'promotions';

// Input classes copied from the tenant modal — never removed at runtime, so
// the dark-theme contrast stays intact.
const _input = 'w-full text-sm bg-white dark:bg-neutral-800 text-neutral-800 dark:text-neutral-200 border border-neutral-300 dark:border-neutral-600 rounded px-2 py-1.5 focus:outline-none focus:border-blue-500';
const _label = 'block text-xs font-medium text-neutral-600 dark:text-neutral-400 mb-1';
const _hint = 'text-xs text-neutral-500 dark:text-neutral-400 mt-0.5';
const _section = 'border-t border-neutral-200 dark:border-neutral-700 pt-3 space-y-2';
const _warn = 'text-xs text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 rounded px-2 py-1.5';
const _error = 'text-xs text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 rounded px-2 py-1.5';
const _btnCancel = 'px-3 py-1.5 text-sm border border-neutral-300 dark:border-neutral-600 rounded-md text-neutral-700 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-800 cursor-pointer';
const _btnSave = 'px-3 py-1.5 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 cursor-pointer';
const _menuItem = 'w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer';

// ─── State ───────────────────────────────────────────────────
let _tenants = [];          // [{id, name, slug, config}]
let _tenantsLoaded = false;
let _items = [];            // current list, rows reference it by index
let _articles = [];         // promotions-category articles for «from article»
// mode: 'create' | 'edit' | 'article'; id: promotion id or article id
let _form = { mode: 'create', id: null };

// ─── Helpers ─────────────────────────────────────────────────

export function canWrite() {
    return hasPermission('promotions:write');
}

/** Today in Kyiv as YYYY-MM-DD — the API computes statuses the same way. */
export function todayKyiv(now = new Date()) {
    return new Intl.DateTimeFormat('en-CA', {
        timeZone: 'Europe/Kyiv', year: 'numeric', month: '2-digit', day: '2-digit',
    }).format(now);
}

/** Status of a promotion row, same rules as list_promotions in the API. */
export function promotionStatus(p, today = todayKyiv()) {
    if (p.valid_to < today) return 'expired';
    if (!p.active) return 'disabled';
    if (p.valid_from > today) return 'upcoming';
    return 'active';
}

/** "Michelin, BFGoodrich ,michelin" → ["michelin", "bfgoodrich"] */
export function splitBrands(value) {
    const out = [];
    for (const part of String(value || '').split(',')) {
        const item = part.trim().toLowerCase();
        if (item && !out.includes(item)) out.push(item);
    }
    return out;
}

/**
 * Build the `overrides` object from the form values. Only keys the admin
 * ticked are sent: an absent key means «the standard condition stands».
 */
export function buildOverrides({ freeDelivery, warrantyBrands, discount, partnerService, partnerLabel }) {
    const out = {};
    if (freeDelivery) out.free_delivery = true;
    const brands = splitBrands(warrantyBrands);
    if (brands.length > 0) out.extended_warranty_brands = brands;
    if (discount) out.discount = true;
    const label = String(partnerLabel || '').trim();
    if (partnerService && label) out.partner_service = { service: partnerService, network_label: label };
    return out;
}

/** FastAPI `detail` → readable text (a 422 from pydantic is a list of objects). */
export function formatDetail(detail) {
    if (!detail) return '';
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
        return detail.map(d => {
            if (typeof d === 'string') return d;
            const loc = Array.isArray(d.loc) ? d.loc.filter(x => x !== 'body').join('.') : '';
            const msg = String(d.msg || '').replace(/^Value error, /, '');
            return loc ? `${loc}: ${msg}` : msg;
        }).join('; ');
    }
    try { return JSON.stringify(detail); } catch { return String(detail); }
}

/**
 * Write request that keeps the API's error text. api() turns a pydantic 422
 * list into "[object Object]", and the admin must see which field failed.
 */
async function _write(path, method, body) {
    const headers = { 'Content-Type': 'application/json' };
    const token = getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;
    let res;
    try {
        res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    } catch {
        throw new Error(t('api.networkError'));
    }
    if (res.status === 401) throw new Error(t('api.unauthorized'));
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        const text = formatDetail(data.detail);
        throw new Error(text || `HTTP ${res.status}`);
    }
    return data;
}

function _tenantConfig(tn) {
    if (!tn) return {};
    if (tn.config && typeof tn.config === 'object') return tn.config;
    if (typeof tn.config === 'string') {
        try { return JSON.parse(tn.config) || {}; } catch { return {}; }
    }
    return {};
}

function _tenantName(id) {
    const tn = _tenants.find(x => x.id === id);
    return tn ? (tn.name || tn.slug || id) : (id || '—');
}

async function _loadTenants() {
    if (_tenantsLoaded) return;
    try {
        const data = await api('/admin/tenants?limit=200');
        _tenants = (data.tenants || []).map(tn => ({
            id: String(tn.id), name: tn.name, slug: tn.slug, config: tn.config,
        }));
        _tenantsLoaded = true;
    } catch {
        // No tenants:read — the list still shows ids; the form falls back to a text field.
        _tenants = [];
    }
}

function _fillTenantSelect(sel, withAll) {
    sel.innerHTML = '';
    if (withAll) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = t('promotions.allNetworks');
        sel.appendChild(opt);
    }
    for (const tn of _tenants) {
        const opt = document.createElement('option');
        opt.value = tn.id;
        opt.textContent = tn.name || tn.slug || tn.id;
        sel.appendChild(opt);
    }
}

// ─── Page shell ──────────────────────────────────────────────

function _renderShell() {
    const root = document.getElementById('promotionsRoot');
    if (!root || root.dataset.built === '1') return;
    root.dataset.built = '1';
    root.innerHTML = `
        <div class="${tw.filterBar}">
            <select id="promotionsTenantFilter" class="${tw.filterInput}"></select>
            <select id="promotionsStatusFilter" class="${tw.filterInput}">
                ${STATUSES.map(s => `<option value="${s}" data-i18n="promotions.status.${s}">${t(`promotions.status.${s}`)}</option>`).join('')}
            </select>
            <button id="promotionsCreateBtn" class="${tw.btnPrimary} max-md:w-full" data-i18n="promotions.create">${t('promotions.create')}</button>
            <button id="promotionsFromArticleBtn" class="${tw.btnSecondary} max-md:w-full" data-i18n="promotions.fromArticle">${t('promotions.fromArticle')}</button>
        </div>
        <div class="${tw.card}" id="promotionsContainer">
            <div class="${tw.loadingWrap}"><div class="spinner"></div></div>
        </div>`;
    root.querySelector('#promotionsTenantFilter').addEventListener('change', () => loadPromotions());
    root.querySelector('#promotionsStatusFilter').addEventListener('change', () => loadPromotions());
    root.querySelector('#promotionsCreateBtn').addEventListener('click', () => showCreate());
    root.querySelector('#promotionsFromArticleBtn').addEventListener('click', () => showFromArticle());
}

function _applyWriteVisibility() {
    const write = canWrite();
    for (const id of ['promotionsCreateBtn', 'promotionsFromArticleBtn']) {
        const el = document.getElementById(id);
        if (el) el.style.display = write ? '' : 'none';
    }
}

// ─── List ────────────────────────────────────────────────────

function _overrideBadges(ov) {
    const o = (ov && typeof ov === 'object') ? ov : {};
    const out = [];
    if (o.free_delivery) out.push(`<span class="${tw.badgeGreen}">${t('promotions.ov.freeDelivery')}</span>`);
    if (Array.isArray(o.extended_warranty_brands) && o.extended_warranty_brands.length > 0) {
        out.push(`<span class="${tw.badgeBlue}">${t('promotions.ov.warranty')}: ${escapeHtml(o.extended_warranty_brands.join(', '))}</span>`);
    }
    if (o.discount) out.push(`<span class="${tw.badgeYellow}">${t('promotions.ov.discount')}</span>`);
    if (o.partner_service && typeof o.partner_service === 'object') {
        const svc = PARTNER_SERVICES.includes(o.partner_service.service)
            ? t(`promotions.service.${o.partner_service.service}`) : escapeHtml(String(o.partner_service.service || ''));
        out.push(`<span class="${tw.badgePurple}">${t('promotions.ov.partner')}: ${svc} — ${escapeHtml(String(o.partner_service.network_label || ''))}</span>`);
    }
    return out.length > 0 ? `<div class="flex flex-wrap gap-1">${out.join('')}</div>` : `<span class="${tw.mutedText}">${t('promotions.ov.none')}</span>`;
}

function _statusBadge(status) {
    const cls = { active: tw.badgeGreen, upcoming: tw.badgeBlue, expired: tw.badgeGray, disabled: tw.badgeRed }[status] || tw.badgeGray;
    return `<span class="${cls}">${t(`promotions.rowStatus.${status}`)}</span>`;
}

async function loadPromotions() {
    _renderShell();
    _applyWriteVisibility();
    const container = document.getElementById('promotionsContainer');
    if (!container) return;
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    await _loadTenants();
    const tenantSel = document.getElementById('promotionsTenantFilter');
    const keep = tenantSel.value;
    _fillTenantSelect(tenantSel, true);
    tenantSel.value = _tenants.some(tn => tn.id === keep) ? keep : '';

    const params = new URLSearchParams();
    if (tenantSel.value) params.set('tenant_id', tenantSel.value);
    params.set('status', document.getElementById('promotionsStatusFilter').value || 'all');

    try {
        const data = await api(`/admin/promotions?${params}`);
        _items = data.items || [];
        if (_items.length === 0) {
            container.innerHTML = `<div class="${tw.emptyState}">${t('promotions.empty')}</div>`;
            return;
        }
        const today = todayKyiv();
        const write = canWrite();
        container.innerHTML = `
            <div class="overflow-x-auto min-h-[320px]"><table class="${tw.table}" id="promotionsTable"><thead><tr>
                <th class="${tw.th}">${t('promotions.col.network')}</th>
                <th class="${tw.th}">${t('promotions.col.title')}</th>
                <th class="${tw.th}">${t('promotions.col.period')}</th>
                <th class="${tw.th}">${t('promotions.col.overrides')}</th>
                <th class="${tw.th}">${t('promotions.col.brands')}</th>
                <th class="${tw.th}">${t('promotions.col.active')}</th>
                ${write ? `<th class="${tw.th}">${t('common.actions')}</th>` : ''}
            </tr></thead><tbody>
            ${_items.map((p, i) => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}">${escapeHtml(_tenantName(p.tenant_id))}</td>
                    <td class="${tw.td}"><div class="font-medium">${escapeHtml(p.title)}</div>
                        <div class="${tw.mutedText} line-clamp-2 max-w-md">${escapeHtml(p.bot_text)}</div></td>
                    <td class="${tw.td} whitespace-nowrap">${escapeHtml(p.valid_from)} — ${escapeHtml(p.valid_to)}</td>
                    <td class="${tw.td}">${_overrideBadges(p.overrides)}</td>
                    <td class="${tw.td}">${(p.mention_brands || []).length > 0 ? escapeHtml(p.mention_brands.join(', ')) : `<span class="${tw.mutedText}">—</span>`}</td>
                    <td class="${tw.td}">${_statusBadge(promotionStatus(p, today))}</td>
                    ${write ? `<td class="${tw.td}">
                        <div class="relative inline-block">
                            <button class="px-1.5 py-0.5 text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 text-sm cursor-pointer" onclick="this.nextElementSibling.classList.toggle('hidden')">&hellip;</button>
                            <div class="hidden absolute right-0 z-20 mt-1 w-40 bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-md shadow-lg py-1">
                                <button class="${_menuItem}" onclick="window._pages.promotions.editPromotion(${i})">${t('common.edit')}</button>
                                <button class="${_menuItem}" onclick="window._pages.promotions.toggleActive(${i})">${p.active ? t('common.deactivate') : t('common.activate')}</button>
                                <button class="${_menuItem} text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/30" onclick="window._pages.promotions.deletePromotion(${i})">${t('common.delete')}</button>
                            </div>
                        </div>
                    </td>` : ''}
                </tr>`).join('')}
            </tbody></table></div>
            <p class="${tw.mutedText} mt-2">${t('common.showing', { shown: _items.length, total: data.total ?? _items.length })}</p>`;
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('promotions.loadFailed', { error: escapeHtml(e.message) })}
            <br><button class="${tw.btnPrimary} ${tw.btnSm} mt-2" onclick="window._pages.promotions.loadPromotions()">${t('common.retry')}</button></div>`;
    }
}

// ─── Form modal ──────────────────────────────────────────────

function _ensureFormModal() {
    // Rebuilt on every open so the labels follow a language switch. Only
    // translated labels and fixed enum values are interpolated; data goes in
    // through DOM properties in _fillForm.
    const old = document.getElementById('promotionModal');
    if (old) old.remove();
    const el = document.createElement('div');
    el.id = 'promotionModal';
    el.className = 'modal-overlay fixed inset-0 bg-black/50 z-[100] justify-center items-center';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
    el.innerHTML = `
        <div class="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-xl shadow-2xl w-full max-w-lg max-h-[90vh] overflow-hidden flex flex-col">
            <div class="modal-fixed-header">
                <h2 class="text-base font-semibold text-neutral-900 dark:text-neutral-50" id="promoFormTitle"></h2>
            </div>
            <div class="modal-body">
            <div class="space-y-3">
                <p id="promoFormArticle" class="hidden ${_hint}"></p>
                <div>
                    <label class="${_label}" for="promoTenant">${t('promotions.f.network')} *</label>
                    <select id="promoTenant" class="${_input}"></select>
                    <input id="promoTenantText" type="text" class="${_input} hidden" placeholder="tenant_id (UUID)">
                    <p id="promoSalesOff" class="hidden mt-1 ${_warn}">${t('promotions.f.salesOff')}</p>
                </div>
                <div>
                    <label class="${_label}" for="promoTitle">${t('promotions.f.title')} *</label>
                    <input id="promoTitle" type="text" maxlength="300" class="${_input}">
                </div>
                <div>
                    <label class="${_label}" for="promoBotText">${t('promotions.f.botText')} *</label>
                    <textarea id="promoBotText" rows="4" class="${_input}"></textarea>
                    <div class="flex justify-between gap-2">
                        <p class="${_hint}">${t('promotions.f.botTextHint')}</p>
                        <p class="${_hint} whitespace-nowrap" id="promoBotTextCount"></p>
                    </div>
                </div>
                <div class="grid grid-cols-2 gap-2">
                    <div>
                        <label class="${_label}" for="promoFrom">${t('promotions.f.validFrom')} *</label>
                        <input id="promoFrom" type="date" class="${_input}">
                    </div>
                    <div>
                        <label class="${_label}" for="promoTo">${t('promotions.f.validTo')} *</label>
                        <input id="promoTo" type="date" class="${_input}">
                    </div>
                </div>
                <p id="promoPastWarning" class="hidden ${_warn}">${t('promotions.f.pastWarning')}</p>
                <div class="${_section}">
                    <label class="${_label}">${t('promotions.f.overrides')}</label>
                    <p class="${_hint}">${t('promotions.f.overridesHint')}</p>
                    <label class="flex items-center gap-1.5 text-xs cursor-pointer">
                        <input type="checkbox" id="promoFreeDelivery">
                        <span>${t('promotions.ov.freeDelivery')}</span>
                    </label>
                    <div>
                        <label class="${_label}" for="promoWarranty">${t('promotions.f.warrantyBrands')}</label>
                        <input id="promoWarranty" type="text" class="${_input}">
                        <p class="${_hint}">${t('promotions.f.commaHint')}</p>
                    </div>
                    <label class="flex items-center gap-1.5 text-xs cursor-pointer">
                        <input type="checkbox" id="promoDiscount">
                        <span>${t('promotions.ov.discount')}</span>
                    </label>
                    <div class="grid grid-cols-2 gap-2">
                        <div>
                            <label class="${_label}" for="promoPartnerService">${t('promotions.f.partnerService')}</label>
                            <select id="promoPartnerService" class="${_input}">
                                <option value="">${t('promotions.f.partnerNone')}</option>
                                ${PARTNER_SERVICES.map(s => `<option value="${s}">${t(`promotions.service.${s}`)}</option>`).join('')}
                            </select>
                        </div>
                        <div>
                            <label class="${_label}" for="promoPartnerLabel">${t('promotions.f.partnerLabel')}</label>
                            <input id="promoPartnerLabel" type="text" class="${_input}">
                        </div>
                    </div>
                </div>
                <div class="${_section}">
                    <div>
                        <label class="${_label}" for="promoMentionBrands">${t('promotions.f.mentionBrands')}</label>
                        <input id="promoMentionBrands" type="text" class="${_input}">
                        <p class="${_hint}">${t('promotions.f.mentionBrandsHint')}</p>
                    </div>
                    <label class="flex items-center gap-2 text-sm cursor-pointer">
                        <input type="checkbox" id="promoActive">
                        <span class="font-medium">${t('promotions.f.active')}</span>
                    </label>
                </div>
                <p id="promoFormError" class="hidden ${_error}"></p>
            </div>
            <div class="flex justify-end gap-2 mt-4">
                <button id="promoCancelBtn" class="${_btnCancel}">${t('common.cancel')}</button>
                <button id="promoSaveBtn" class="${_btnSave}">${t('common.save')}</button>
            </div>
            </div>
        </div>`;
    document.body.appendChild(el);

    const botText = el.querySelector('#promoBotText');
    botText.addEventListener('input', _updateCounter);
    el.querySelector('#promoTo').addEventListener('change', _updatePastWarning);
    el.querySelector('#promoTo').addEventListener('input', _updatePastWarning);
    el.querySelector('#promoTenant').addEventListener('change', _updateSalesWarning);
    el.querySelector('#promoCancelBtn').addEventListener('click', () => closeModal('promotionModal'));
    el.querySelector('#promoSaveBtn').addEventListener('click', () => savePromotion());
}

function _updateCounter() {
    const len = document.getElementById('promoBotText').value.trim().length;
    const el = document.getElementById('promoBotTextCount');
    el.textContent = t('promotions.f.counter', { n: len, max: BOT_TEXT_MAX });
    el.classList.toggle('text-red-600', len > BOT_TEXT_MAX);
}

function _updatePastWarning() {
    const to = document.getElementById('promoTo').value;
    document.getElementById('promoPastWarning').classList.toggle('hidden', !(to && to < todayKyiv()));
}

function _updateSalesWarning() {
    const sel = document.getElementById('promoTenant');
    const tn = _tenants.find(x => x.id === sel.value);
    const off = !!tn && _tenantConfig(tn).sales_enabled !== true;
    document.getElementById('promoSalesOff').classList.toggle('hidden', !off);
}

function _showFormError(msg) {
    const el = document.getElementById('promoFormError');
    el.textContent = msg || '';
    el.classList.toggle('hidden', !msg);
}

function _fillForm(p, { title, articleNote } = {}) {
    document.getElementById('promoFormTitle').textContent = title;
    const note = document.getElementById('promoFormArticle');
    note.textContent = articleNote || '';
    note.classList.toggle('hidden', !articleNote);

    const sel = document.getElementById('promoTenant');
    const txt = document.getElementById('promoTenantText');
    if (_tenants.length > 0) {
        _fillTenantSelect(sel, false);
        if (p.tenant_id && !_tenants.some(tn => tn.id === p.tenant_id)) {
            const opt = document.createElement('option');
            opt.value = p.tenant_id;
            opt.textContent = p.tenant_id;
            sel.appendChild(opt);
        }
        if (!p.tenant_id) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = t('promotions.f.pickNetwork');
            sel.insertBefore(opt, sel.firstChild);
        }
        sel.value = p.tenant_id || '';
    } else {
        sel.classList.add('hidden');
        txt.classList.remove('hidden');
        txt.value = p.tenant_id || '';
    }

    const ov = (p.overrides && typeof p.overrides === 'object') ? p.overrides : {};
    const ps = (ov.partner_service && typeof ov.partner_service === 'object') ? ov.partner_service : {};
    document.getElementById('promoTitle').value = p.title || '';
    document.getElementById('promoBotText').value = p.bot_text || '';
    document.getElementById('promoFrom').value = p.valid_from || '';
    document.getElementById('promoTo').value = p.valid_to || '';
    document.getElementById('promoFreeDelivery').checked = ov.free_delivery === true;
    document.getElementById('promoWarranty').value = Array.isArray(ov.extended_warranty_brands) ? ov.extended_warranty_brands.join(', ') : '';
    document.getElementById('promoDiscount').checked = ov.discount === true;
    document.getElementById('promoPartnerService').value = PARTNER_SERVICES.includes(ps.service) ? ps.service : '';
    document.getElementById('promoPartnerLabel').value = typeof ps.network_label === 'string' ? ps.network_label : '';
    document.getElementById('promoMentionBrands').value = (p.mention_brands || []).join(', ');
    document.getElementById('promoActive').checked = p.active !== false;
    _showFormError('');
    _updateCounter();
    _updatePastWarning();
    _updateSalesWarning();
}

function _readForm() {
    const sel = document.getElementById('promoTenant');
    const tenant_id = sel.classList.contains('hidden')
        ? document.getElementById('promoTenantText').value.trim()
        : sel.value;
    return {
        tenant_id,
        title: document.getElementById('promoTitle').value.trim(),
        bot_text: document.getElementById('promoBotText').value.trim(),
        valid_from: document.getElementById('promoFrom').value,
        valid_to: document.getElementById('promoTo').value,
        overrides: buildOverrides({
            freeDelivery: document.getElementById('promoFreeDelivery').checked,
            warrantyBrands: document.getElementById('promoWarranty').value,
            discount: document.getElementById('promoDiscount').checked,
            partnerService: document.getElementById('promoPartnerService').value,
            partnerLabel: document.getElementById('promoPartnerLabel').value,
        }),
        mention_brands: splitBrands(document.getElementById('promoMentionBrands').value),
        active: document.getElementById('promoActive').checked,
    };
}

/** Client-side check before the request; returns an error text or ''. */
export function validateForm(body, mode) {
    if (!body.tenant_id) return t('promotions.err.network');
    if (!body.title && mode !== 'article') return t('promotions.err.title');
    if (!body.bot_text && mode !== 'article') return t('promotions.err.botText');
    if (body.bot_text.length > BOT_TEXT_MAX) return t('promotions.err.botTextLong', { max: BOT_TEXT_MAX });
    if (!body.valid_from || !body.valid_to) return t('promotions.err.dates');
    if (body.valid_to < body.valid_from) return t('promotions.err.range');
    return '';
}

async function showCreate() {
    if (!canWrite()) return;
    await _loadTenants();
    _form = { mode: 'create', id: null };
    _ensureFormModal();
    const filterTenant = document.getElementById('promotionsTenantFilter')?.value || '';
    _fillForm({ tenant_id: filterTenant, valid_from: todayKyiv(), active: true }, { title: t('promotions.newTitle') });
    showModal('promotionModal');
}

async function editPromotion(index) {
    const p = _items[index];
    if (!p || !canWrite()) return;
    await _loadTenants();
    _form = { mode: 'edit', id: p.id };
    _ensureFormModal();
    _fillForm(p, { title: t('promotions.editTitle') });
    showModal('promotionModal');
}

async function savePromotion() {
    const body = _readForm();
    const err = validateForm(body, _form.mode);
    if (err) { _showFormError(err); return; }
    if (body.valid_to < todayKyiv() && !confirm(t('promotions.pastConfirm'))) return;

    try {
        if (_form.mode === 'edit') {
            await _write(`/admin/promotions/${encodeURIComponent(_form.id)}`, 'PATCH', body);
            showToast(t('promotions.updated'));
        } else if (_form.mode === 'article') {
            // Empty title/text → the server drafts them from the article.
            const payload = { ...body };
            if (!payload.title) delete payload.title;
            if (!payload.bot_text) delete payload.bot_text;
            await _write(`/admin/promotions/from-article/${encodeURIComponent(_form.id)}`, 'POST', payload);
            showToast(t('promotions.created'));
        } else {
            await _write('/admin/promotions', 'POST', body);
            showToast(t('promotions.created'));
        }
        closeModal('promotionModal');
        loadPromotions();
    } catch (e) {
        _showFormError(t('promotions.saveFailed', { error: e.message }));
    }
}

async function toggleActive(index) {
    const p = _items[index];
    if (!p || !canWrite()) return;
    try {
        await _write(`/admin/promotions/${encodeURIComponent(p.id)}`, 'PATCH', { active: !p.active });
        showToast(p.active ? t('promotions.deactivated') : t('promotions.activated'));
        loadPromotions();
    } catch (e) {
        showToast(t('promotions.saveFailed', { error: e.message }), 'error');
    }
}

async function deletePromotion(index) {
    const p = _items[index];
    if (!p || !canWrite()) return;
    if (!confirm(t('promotions.deleteConfirm', { title: p.title }))) return;
    try {
        await _write(`/admin/promotions/${encodeURIComponent(p.id)}`, 'DELETE');
        showToast(t('promotions.deleted'));
        loadPromotions();
    } catch (e) {
        showToast(t('promotions.saveFailed', { error: e.message }), 'error');
    }
}

// ─── «Create from article» ───────────────────────────────────

function _ensureArticleModal() {
    const old = document.getElementById('promotionArticleModal');
    if (old) old.remove();
    const el = document.createElement('div');
    el.id = 'promotionArticleModal';
    el.className = 'modal-overlay fixed inset-0 bg-black/50 z-[100] justify-center items-center';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
    el.innerHTML = `
        <div class="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-xl shadow-2xl w-full max-w-lg max-h-[90vh] overflow-hidden flex flex-col">
            <div class="modal-fixed-header">
                <h2 class="text-base font-semibold text-neutral-900 dark:text-neutral-50">${t('promotions.fromArticleTitle')}</h2>
            </div>
            <div class="modal-body">
                <p class="${_hint} mb-2">${t('promotions.fromArticleHint')}</p>
                <div id="promoArticleList" class="space-y-1 max-h-[55vh] overflow-y-auto">
                    <div class="${tw.loadingWrap}"><div class="spinner"></div></div>
                </div>
                <div class="flex justify-end gap-2 mt-4">
                    <button id="promoArticleCancelBtn" class="${_btnCancel}">${t('common.cancel')}</button>
                </div>
            </div>
        </div>`;
    document.body.appendChild(el);
    el.querySelector('#promoArticleCancelBtn').addEventListener('click', () => closeModal('promotionArticleModal'));
}

async function showFromArticle() {
    if (!canWrite()) return;
    await _loadTenants();
    _ensureArticleModal();
    showModal('promotionArticleModal');
    const list = document.getElementById('promoArticleList');
    try {
        const data = await api(`/knowledge/articles?category=${KB_CATEGORY}&limit=200`);
        _articles = data.articles || [];
        if (_articles.length === 0) {
            list.innerHTML = `<div class="${tw.emptyState}">${t('promotions.noArticles')}</div>`;
            return;
        }
        list.innerHTML = '';
        _articles.forEach((a, i) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'w-full text-left px-3 py-2 rounded-md border border-neutral-200 dark:border-neutral-700 hover:bg-neutral-50 dark:hover:bg-neutral-800 cursor-pointer';
            const title = document.createElement('div');
            title.className = 'text-sm text-neutral-800 dark:text-neutral-200';
            title.textContent = a.title || '—';
            const meta = document.createElement('div');
            meta.className = tw.mutedText;
            meta.textContent = (a.tenant_id ? _tenantName(String(a.tenant_id)) : t('promotions.sharedArticle'))
                + (a.active === false ? ` · ${t('promotions.articleInactive')}` : '');
            btn.append(title, meta);
            btn.addEventListener('click', () => pickArticle(i));
            list.appendChild(btn);
        });
    } catch (e) {
        list.innerHTML = '';
        const div = document.createElement('div');
        div.className = tw.emptyState;
        div.textContent = t('promotions.articlesFailed', { error: e.message });
        list.appendChild(div);
    }
}

function pickArticle(index) {
    const a = _articles[index];
    if (!a) return;
    closeModal('promotionArticleModal');
    _form = { mode: 'article', id: String(a.id) };
    _ensureFormModal();
    // Dates are never in the article — the admin enters both.
    _fillForm({
        tenant_id: a.tenant_id ? String(a.tenant_id) : '',
        title: a.title || '',
        bot_text: '',
        active: true,
    }, { title: t('promotions.fromArticleFormTitle'), articleNote: t('promotions.fromArticleNote', { title: a.title || '' }) });
    document.getElementById('promoBotText').placeholder = t('promotions.f.botTextFromArticle');
    showModal('promotionModal');
}

// ─── Init & exports ──────────────────────────────────────────

export function init() {
    registerPageLoader('promotions', () => loadPromotions());
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.relative.inline-block')) {
            document.querySelectorAll('#page-promotions .relative.inline-block > div:not(.hidden)').forEach(m => m.classList.add('hidden'));
        }
    });
    // Table cells are rendered with t(); re-render them after a language switch.
    window.addEventListener('langchange', () => {
        const page = document.getElementById('page-promotions');
        if (page && page.style.display !== 'none' && document.getElementById('promotionsRoot')?.dataset.built === '1') {
            loadPromotions();
        }
    });
}

window._pages = window._pages || {};
window._pages.promotions = {
    loadPromotions, showCreate, editPromotion, savePromotion,
    toggleActive, deletePromotion, showFromArticle, pickArticle,
};
