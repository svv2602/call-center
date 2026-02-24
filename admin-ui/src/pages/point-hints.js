import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { escapeHtml } from '../utils.js';
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
        const [hintsData, stationsData] = await Promise.all([
            api('/admin/fitting/station-hints'),
            api('/admin/fitting/stations'),
        ]);
        _stationHints = hintsData.hints || {};
        _stationsList = stationsData.stations || [];
        renderStationHints();
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('pointHints.loadFailed', {error: escapeHtml(e.message)})}</div>`;
    }
}

function renderStationHints() {
    const container = document.getElementById('pointHintsContent-fitting');
    if (!container) return;

    if (_stationsList.length === 0) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('pointHints.noStations')}</div>`;
        return;
    }

    let html = `<div class="overflow-x-auto"><table class="${tw.table}">
        <thead><tr>
            <th class="${tw.th}">${t('pointHints.name')}</th>
            <th class="${tw.th}">${t('pointHints.address')}</th>
            <th class="${tw.th}">${t('pointHints.district')}</th>
            <th class="${tw.th}">${t('pointHints.landmarks')}</th>
            <th class="${tw.th}">${t('pointHints.description')}</th>
            <th class="${tw.th}">${t('pointHints.actions')}</th>
        </tr></thead><tbody>`;

    for (const station of _stationsList) {
        const sid = station.station_id || station.id || '';
        const hint = _stationHints[sid] || {};
        const district = hint.district || '';
        const landmarks = hint.landmarks || '';
        const description = hint.description || '';
        const name = station.name || '';
        const address = station.address || '';

        html += `<tr class="${tw.trHover}">
            <td class="${tw.td}" data-label="${t('pointHints.name')}"><strong>${escapeHtml(name)}</strong><div class="text-[10px] text-neutral-400 font-mono">${escapeHtml(sid)}</div></td>
            <td class="${tw.td}" data-label="${t('pointHints.address')}">${escapeHtml(address)}</td>
            <td class="${tw.td}" data-label="${t('pointHints.district')}"><input type="text" id="sh-district-${escapeHtml(sid)}" value="${escapeHtml(district)}" placeholder="${t('pointHints.districtPh')}" class="text-xs bg-transparent border border-neutral-300 dark:border-neutral-700 rounded px-1.5 py-0.5 w-32 focus:outline-none focus:border-blue-500"></td>
            <td class="${tw.td}" data-label="${t('pointHints.landmarks')}"><input type="text" id="sh-landmarks-${escapeHtml(sid)}" value="${escapeHtml(landmarks)}" placeholder="${t('pointHints.landmarksPh')}" class="text-xs bg-transparent border border-neutral-300 dark:border-neutral-700 rounded px-1.5 py-0.5 w-40 focus:outline-none focus:border-blue-500"></td>
            <td class="${tw.td}" data-label="${t('pointHints.description')}"><input type="text" id="sh-desc-${escapeHtml(sid)}" value="${escapeHtml(description)}" placeholder="${t('pointHints.descPh')}" class="text-xs bg-transparent border border-neutral-300 dark:border-neutral-700 rounded px-1.5 py-0.5 w-40 focus:outline-none focus:border-blue-500"></td>
            <td class="${tw.tdActions}" data-label="${t('pointHints.actions')}">
                <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.pointHints.saveStationHint('${escapeHtml(sid)}')">${t('common.save')}</button>
                ${(district || landmarks || description) ? `<button class="${tw.btnSm} text-red-600 dark:text-red-400 ml-1" onclick="window._pages.pointHints.deleteStationHint('${escapeHtml(sid)}')">${t('common.delete')}</button>` : ''}
            </td>
        </tr>`;
    }
    html += '</tbody></table></div>';
    container.innerHTML = html;
}

async function saveStationHint(stationId) {
    const district = document.getElementById(`sh-district-${stationId}`)?.value || '';
    const landmarks = document.getElementById(`sh-landmarks-${stationId}`)?.value || '';
    const description = document.getElementById(`sh-desc-${stationId}`)?.value || '';

    if (!district && !landmarks && !description) {
        if (_stationHints[stationId]) {
            await deleteStationHint(stationId);
        }
        return;
    }

    try {
        await api(`/admin/fitting/station-hints/${encodeURIComponent(stationId)}`, {
            method: 'PUT',
            body: JSON.stringify({ district, landmarks, description }),
        });
        _stationHints[stationId] = { district, landmarks, description };
        renderStationHints();
        showToast(t('pointHints.saved'), 'success');
    } catch (e) {
        showToast(t('pointHints.saveFailed', {error: e.message}), 'error');
    }
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
        container.innerHTML = `<div class="${tw.emptyState}">${t('pointHints.noPoints')}</div>`;
        return;
    }

    let html = `<div class="overflow-x-auto"><table class="${tw.table}">
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

        html += `<tr class="${tw.trHover}">
            <td class="${tw.td}" data-label="${t('pointHints.address')}"><strong>${escapeHtml(address)}</strong><div class="text-[10px] text-neutral-400 font-mono">${escapeHtml(pid)}</div></td>
            <td class="${tw.td}" data-label="${t('pointHints.city')}">${escapeHtml(city)}</td>
            <td class="${tw.td}" data-label="${t('pointHints.district')}"><input type="text" id="ph-district-${escapeHtml(pid)}" value="${escapeHtml(district)}" placeholder="${t('pointHints.districtPh')}" class="text-xs bg-transparent border border-neutral-300 dark:border-neutral-700 rounded px-1.5 py-0.5 w-32 focus:outline-none focus:border-blue-500"></td>
            <td class="${tw.td}" data-label="${t('pointHints.landmarks')}"><input type="text" id="ph-landmarks-${escapeHtml(pid)}" value="${escapeHtml(landmarks)}" placeholder="${t('pointHints.landmarksPh')}" class="text-xs bg-transparent border border-neutral-300 dark:border-neutral-700 rounded px-1.5 py-0.5 w-40 focus:outline-none focus:border-blue-500"></td>
            <td class="${tw.td}" data-label="${t('pointHints.description')}"><input type="text" id="ph-desc-${escapeHtml(pid)}" value="${escapeHtml(description)}" placeholder="${t('pointHints.descPh')}" class="text-xs bg-transparent border border-neutral-300 dark:border-neutral-700 rounded px-1.5 py-0.5 w-40 focus:outline-none focus:border-blue-500"></td>
            <td class="${tw.tdActions}" data-label="${t('pointHints.actions')}">
                <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.pointHints.savePickupHint('${escapeHtml(pid)}')">${t('common.save')}</button>
                ${(district || landmarks || description) ? `<button class="${tw.btnSm} text-red-600 dark:text-red-400 ml-1" onclick="window._pages.pointHints.deletePickupHint('${escapeHtml(pid)}')">${t('common.delete')}</button>` : ''}
            </td>
        </tr>`;
    }
    html += '</tbody></table></div>';
    container.innerHTML = html;
}

async function savePickupHint(pointId) {
    const district = document.getElementById(`ph-district-${pointId}`)?.value || '';
    const landmarks = document.getElementById(`ph-landmarks-${pointId}`)?.value || '';
    const description = document.getElementById(`ph-desc-${pointId}`)?.value || '';

    if (!district && !landmarks && !description) {
        if (_pickupHints[pointId]) {
            await deletePickupHint(pointId);
        }
        return;
    }

    try {
        await api(`/admin/fitting/pickup-hints/${encodeURIComponent(pointId)}`, {
            method: 'PUT',
            body: JSON.stringify({ district, landmarks, description }),
        });
        _pickupHints[pointId] = { district, landmarks, description };
        renderPickupHints();
        showToast(t('pointHints.saved'), 'success');
    } catch (e) {
        showToast(t('pointHints.saveFailed', {error: e.message}), 'error');
    }
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
    loadStationHints, saveStationHint, deleteStationHint,
    loadPickupHints, savePickupHint, deletePickupHint,
};
