import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { escapeHtml, closeModal } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import * as tw from '../tw.js';

// ─── State ───────────────────────────────────────────────────
let _activeTab = 'fitting';
let _stationHints = {};
let _stationsList = [];
let _pickupHints = {};
let _pickupPointsList = [];

// ═══════════════════════════════════════════════════════════
//  Modal helpers
// ═══════════════════════════════════════════════════════════
function openHintModal(type, id, label, hint) {
    document.getElementById('pointHintEditType').value = type;
    document.getElementById('pointHintEditId').value = id;
    document.getElementById('pointHintModalTitle').textContent = t('pointHints.editHint');
    document.getElementById('pointHintModalSubtitle').textContent = label;
    document.getElementById('pointHintDistrict').value = hint.district || '';
    document.getElementById('pointHintLandmarks').value = hint.landmarks || '';
    document.getElementById('pointHintDescription').value = hint.description || '';
    document.getElementById('pointHintModal').classList.add('show');
    // Focus first field
    document.getElementById('pointHintDistrict').focus();
}

async function saveHintFromModal() {
    const type = document.getElementById('pointHintEditType').value;
    const id = document.getElementById('pointHintEditId').value;
    const district = document.getElementById('pointHintDistrict').value.trim();
    const landmarks = document.getElementById('pointHintLandmarks').value.trim();
    const description = document.getElementById('pointHintDescription').value.trim();

    if (type === 'fitting') {
        if (!district && !landmarks && !description) {
            if (_stationHints[id]) {
                await deleteStationHint(id);
            }
            closeModal('pointHintModal');
            return;
        }
        try {
            await api(`/admin/fitting/station-hints/${encodeURIComponent(id)}`, {
                method: 'PUT',
                body: JSON.stringify({ district, landmarks, description }),
            });
            _stationHints[id] = { district, landmarks, description };
            renderStationHints();
            showToast(t('pointHints.saved'), 'success');
            closeModal('pointHintModal');
        } catch (e) {
            showToast(t('pointHints.saveFailed', { error: e.message }), 'error');
        }
    } else {
        if (!district && !landmarks && !description) {
            if (_pickupHints[id]) {
                await deletePickupHint(id);
            }
            closeModal('pointHintModal');
            return;
        }
        try {
            await api(`/admin/fitting/pickup-hints/${encodeURIComponent(id)}`, {
                method: 'PUT',
                body: JSON.stringify({ district, landmarks, description }),
            });
            _pickupHints[id] = { district, landmarks, description };
            renderPickupHints();
            showToast(t('pointHints.saved'), 'success');
            closeModal('pointHintModal');
        } catch (e) {
            showToast(t('pointHints.saveFailed', { error: e.message }), 'error');
        }
    }
}

// ═══════════════════════════════════════════════════════════
//  Tab switching
// ═══════════════════════════════════════════════════════════
function switchTab(tab) {
    _activeTab = tab;
    document.querySelectorAll('#pointHintsTabs .tab-btn').forEach(btn => {
        const isActive = btn.dataset.tab === tab;
        btn.className = `${tw.tabBtn} ${isActive ? 'border-blue-600 text-blue-600 dark:text-blue-400 dark:border-blue-400 font-semibold' : ''}`;
    });
    document.getElementById('pointHintsContent-fitting').style.display = tab === 'fitting' ? '' : 'none';
    document.getElementById('pointHintsContent-pickup').style.display = tab === 'pickup' ? '' : 'none';

    if (tab === 'fitting' && _stationsList.length === 0) loadStationHints();
    if (tab === 'pickup' && _pickupPointsList.length === 0) loadPickupHints();
}

// ═══════════════════════════════════════════════════════════
//  Station Hints (tab: fitting)
// ═══════════════════════════════════════════════════════════
async function loadStationHints() {
    const container = document.getElementById('pointHintsContent-fitting');
    if (!container) return;
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;
    try {
        // station-hints API returns both hints (from PG) and stations (from cache)
        const hintsData = await api('/admin/fitting/station-hints');
        _stationHints = hintsData.hints || {};
        _stationsList = hintsData.stations || [];
        renderStationHints();
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('pointHints.loadFailed', {error: escapeHtml(e.message)})}</div>`;
    }
}

function _hintPreview(text, maxLen) {
    if (!text) return `<span class="text-neutral-400 dark:text-neutral-600">—</span>`;
    const escaped = escapeHtml(text);
    if (text.length <= maxLen) return escaped;
    return `<span title="${escaped}">${escapeHtml(text.slice(0, maxLen))}…</span>`;
}

function renderStationHints() {
    const container = document.getElementById('pointHintsContent-fitting');
    if (!container) return;

    // Build unified list: all stations + hints-only entries (for stations not in cache)
    const stationsById = {};
    for (const station of _stationsList) {
        const sid = station.station_id || station.id || '';
        if (sid) stationsById[sid] = station;
    }

    // Collect all IDs: stations from cache + stations that have hints in DB
    const allIds = new Set(Object.keys(stationsById));
    for (const hintId of Object.keys(_stationHints)) {
        allIds.add(hintId);
    }

    if (allIds.size === 0) {
        container.innerHTML = `<div class="${tw.emptyState}">
            <p>${t('pointHints.noStations')}</p>
            <button class="${tw.btnPrimary} ${tw.btnSm} mt-3" onclick="window._pages.pointHints.refreshStations()">${t('pointHints.refreshFromOnec')}</button>
        </div>`;
        return;
    }

    let html = `<div class="flex items-center justify-end mb-3">
        <button class="${tw.btnSm} border border-neutral-300 dark:border-neutral-600 text-neutral-700 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-800" onclick="window._pages.pointHints.refreshStations()">${t('pointHints.refreshFromOnec')}</button>
    </div>`;
    html += `<div class="overflow-x-auto"><table class="${tw.table}">
        <thead><tr>
            <th class="${tw.th}">${t('pointHints.name')}</th>
            <th class="${tw.th}">${t('pointHints.address')}</th>
            <th class="${tw.th}">${t('pointHints.district')}</th>
            <th class="${tw.th}">${t('pointHints.landmarks')}</th>
            <th class="${tw.th}">${t('pointHints.description')}</th>
            <th class="${tw.th}">${t('pointHints.actions')}</th>
        </tr></thead><tbody>`;

    // Show stations with hints first, then without
    const sortedIds = [...allIds].sort((a, b) => {
        const aHas = _stationHints[a] ? 1 : 0;
        const bHas = _stationHints[b] ? 1 : 0;
        return bHas - aHas; // hints first
    });

    for (const sid of sortedIds) {
        const station = stationsById[sid] || {};
        const hint = _stationHints[sid] || {};
        const district = hint.district || '';
        const landmarks = hint.landmarks || '';
        const description = hint.description || '';
        const name = station.name || '';
        const address = station.address || '';
        const hasHint = !!(district || landmarks || description);

        html += `<tr class="${tw.trHover}">
            <td class="${tw.td}" data-label="${t('pointHints.name')}"><strong>${escapeHtml(name || sid)}</strong><div class="text-[10px] text-neutral-400 font-mono">${escapeHtml(sid)}</div></td>
            <td class="${tw.td}" data-label="${t('pointHints.address')}">${escapeHtml(address)}</td>
            <td class="${tw.td}" data-label="${t('pointHints.district')}">${_hintPreview(district, 30)}</td>
            <td class="${tw.td}" data-label="${t('pointHints.landmarks')}">${_hintPreview(landmarks, 40)}</td>
            <td class="${tw.td}" data-label="${t('pointHints.description')}">${_hintPreview(description, 40)}</td>
            <td class="${tw.tdActions}" data-label="${t('pointHints.actions')}">
                <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.pointHints.editStationHint('${escapeHtml(sid)}')">${t('common.edit')}</button>
                ${hasHint ? `<button class="${tw.btnSm} text-red-600 dark:text-red-400 ml-1" onclick="window._pages.pointHints.deleteStationHint('${escapeHtml(sid)}')">${t('common.delete')}</button>` : ''}
            </td>
        </tr>`;
    }
    html += '</tbody></table></div>';
    container.innerHTML = html;
}

function editStationHint(stationId) {
    const station = _stationsList.find(s => (s.station_id || s.id) === stationId);
    const label = station ? `${station.name || ''} — ${station.address || ''}` : stationId;
    const hint = _stationHints[stationId] || {};
    openHintModal('fitting', stationId, label, hint);
}

async function deleteStationHint(stationId) {
    try {
        await api(`/admin/fitting/station-hints/${encodeURIComponent(stationId)}`, {
            method: 'DELETE',
        });
        delete _stationHints[stationId];
        renderStationHints();
        showToast(t('pointHints.deleted'), 'success');
    } catch (e) {
        showToast(t('pointHints.deleteFailed', {error: e.message}), 'error');
    }
}

// ═══════════════════════════════════════════════════════════
//  Pickup Point Hints (tab: pickup)
// ═══════════════════════════════════════════════════════════
async function loadPickupHints() {
    const container = document.getElementById('pointHintsContent-pickup');
    if (!container) return;
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;
    try {
        const [hintsData, pointsData] = await Promise.all([
            api('/admin/fitting/pickup-hints'),
            api('/admin/fitting/pickup-points'),
        ]);
        _pickupHints = hintsData.hints || {};
        _pickupPointsList = pointsData.points || [];
        renderPickupHints();
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('pointHints.loadFailed', {error: escapeHtml(e.message)})}</div>`;
    }
}

function renderPickupHints() {
    const container = document.getElementById('pointHintsContent-pickup');
    if (!container) return;

    if (_pickupPointsList.length === 0) {
        container.innerHTML = `<div class="${tw.emptyState}">
            <p>${t('pointHints.noPoints')}</p>
            <button class="${tw.btnPrimary} ${tw.btnSm} mt-3" onclick="window._pages.pointHints.refreshPickupPoints()">${t('pointHints.refreshFromOnec')}</button>
        </div>`;
        return;
    }

    let html = `<div class="flex items-center justify-end mb-3">
        <button class="${tw.btnSm} border border-neutral-300 dark:border-neutral-600 text-neutral-700 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-800" onclick="window._pages.pointHints.refreshPickupPoints()">${t('pointHints.refreshFromOnec')}</button>
    </div>`;
    html += `<div class="overflow-x-auto"><table class="${tw.table}">
        <thead><tr>
            <th class="${tw.th}">${t('pointHints.address')}</th>
            <th class="${tw.th}">${t('pointHints.city')}</th>
            <th class="${tw.th}">${t('pointHints.district')}</th>
            <th class="${tw.th}">${t('pointHints.landmarks')}</th>
            <th class="${tw.th}">${t('pointHints.description')}</th>
            <th class="${tw.th}">${t('pointHints.actions')}</th>
        </tr></thead><tbody>`;

    for (const point of _pickupPointsList) {
        const pid = point.id || '';
        const hint = _pickupHints[pid] || {};
        const district = hint.district || '';
        const landmarks = hint.landmarks || '';
        const description = hint.description || '';
        const address = point.address || '';
        const city = point.city || '';
        const hasHint = !!(district || landmarks || description);

        html += `<tr class="${tw.trHover}">
            <td class="${tw.td}" data-label="${t('pointHints.address')}"><strong>${escapeHtml(address)}</strong><div class="text-[10px] text-neutral-400 font-mono">${escapeHtml(pid)}</div></td>
            <td class="${tw.td}" data-label="${t('pointHints.city')}">${escapeHtml(city)}</td>
            <td class="${tw.td}" data-label="${t('pointHints.district')}">${_hintPreview(district, 30)}</td>
            <td class="${tw.td}" data-label="${t('pointHints.landmarks')}">${_hintPreview(landmarks, 40)}</td>
            <td class="${tw.td}" data-label="${t('pointHints.description')}">${_hintPreview(description, 40)}</td>
            <td class="${tw.tdActions}" data-label="${t('pointHints.actions')}">
                <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.pointHints.editPickupHint('${escapeHtml(pid)}')">${t('common.edit')}</button>
                ${hasHint ? `<button class="${tw.btnSm} text-red-600 dark:text-red-400 ml-1" onclick="window._pages.pointHints.deletePickupHint('${escapeHtml(pid)}')">${t('common.delete')}</button>` : ''}
            </td>
        </tr>`;
    }
    html += '</tbody></table></div>';
    container.innerHTML = html;
}

function editPickupHint(pointId) {
    const point = _pickupPointsList.find(p => p.id === pointId);
    const label = point ? `${point.address || ''} (${point.city || ''})` : pointId;
    const hint = _pickupHints[pointId] || {};
    openHintModal('pickup', pointId, label, hint);
}

async function deletePickupHint(pointId) {
    try {
        await api(`/admin/fitting/pickup-hints/${encodeURIComponent(pointId)}`, {
            method: 'DELETE',
        });
        delete _pickupHints[pointId];
        renderPickupHints();
        showToast(t('pointHints.deleted'), 'success');
    } catch (e) {
        showToast(t('pointHints.deleteFailed', {error: e.message}), 'error');
    }
}

// ═══════════════════════════════════════════════════════════
//  Refresh from 1C
// ═══════════════════════════════════════════════════════════
async function refreshStations() {
    showToast(t('pointHints.refreshing'), 'info');
    try {
        const data = await api('/admin/fitting/stations/refresh', { method: 'POST' });
        _stationsList = data.stations || [];
        showToast(t('pointHints.refreshed', { count: data.total || 0 }), 'success');
        // Reload hints too
        try {
            const hintsData = await api('/admin/fitting/station-hints');
            _stationHints = hintsData.hints || {};
        } catch { /* hints load optional */ }
        renderStationHints();
    } catch (e) {
        showToast(t('pointHints.refreshFailed', { error: e.message }), 'error');
    }
}

async function refreshPickupPoints() {
    showToast(t('pointHints.refreshing'), 'info');
    try {
        const data = await api('/admin/fitting/pickup-points/refresh', { method: 'POST' });
        _pickupPointsList = data.points || [];
        showToast(t('pointHints.refreshed', { count: data.total || 0 }), 'success');
        // Reload hints too
        try {
            const hintsData = await api('/admin/fitting/pickup-hints');
            _pickupHints = hintsData.hints || {};
        } catch { /* hints load optional */ }
        renderPickupHints();
    } catch (e) {
        showToast(t('pointHints.refreshFailed', { error: e.message }), 'error');
    }
}

// ═══════════════════════════════════════════════════════════
//  Init
// ═══════════════════════════════════════════════════════════
export function init() {
    registerPageLoader('point-hints', () => {
        _activeTab = 'fitting';
        _stationsList = [];
        _pickupPointsList = [];
        switchTab('fitting');
    });
}

window._pages = window._pages || {};
window._pages.pointHints = {
    switchTab,
    loadStationHints, editStationHint, deleteStationHint, refreshStations,
    loadPickupHints, editPickupHint, deletePickupHint, refreshPickupPoints,
    saveHintFromModal,
};
