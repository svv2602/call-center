import { api, fetchWithAuth } from '../api.js';
import { showToast } from '../notifications.js';
import { qualityBadge, formatDate, escapeHtml, downloadBlob } from '../utils.js';
import { registerPageLoader } from '../router.js';
import * as tw from '../tw.js';

let callsOffset = 0;

async function loadCalls(offset = 0) {
    callsOffset = offset;
    const loading = document.getElementById('callsLoading');
    const tbody = document.querySelector('#callsTable tbody');
    loading.style.display = 'flex';

    const params = new URLSearchParams({ limit: 20, offset });
    const df = document.getElementById('filterDateFrom').value;
    const dt = document.getElementById('filterDateTo').value;
    const sc = document.getElementById('filterScenario').value;
    const tr = document.getElementById('filterTransferred').value;
    const qb = document.getElementById('filterQualityBelow').value;
    const search = document.getElementById('filterSearch').value;
    if (df) params.set('date_from', df);
    if (dt) params.set('date_to', dt);
    if (sc) params.set('scenario', sc);
    if (tr) params.set('transferred', tr);
    if (qb) params.set('quality_below', qb);
    if (search) params.set('search', search);

    try {
        const data = await api(`/analytics/calls?${params}`);
        loading.style.display = 'none';

        if (!data.calls || data.calls.length === 0) {
            tbody.innerHTML = `<tr><td colspan="7" class="${tw.emptyState}">No calls found</td></tr>`;
            document.getElementById('callsPagination').innerHTML = '';
            return;
        }

        tbody.innerHTML = data.calls.map(c => `
            <tr class="${tw.trHover} cursor-pointer" onclick="window._pages.calls.showCallDetail('${c.id}')">
                <td class="${tw.td}">${formatDate(c.started_at)}</td>
                <td class="${tw.td}">${escapeHtml(c.caller_id) || '-'}</td>
                <td class="${tw.td}">${escapeHtml(c.scenario) || '-'}</td>
                <td class="${tw.td}">${c.duration_seconds || 0}s</td>
                <td class="${tw.td}">${qualityBadge(c.quality_score)}</td>
                <td class="${tw.td}">$${(c.total_cost_usd || 0).toFixed(3)}</td>
                <td class="${tw.td}">${c.transferred_to_operator ? `<span class="${tw.badgeYellow}">Transferred</span>` : `<span class="${tw.badgeGreen}">Resolved</span>`}</td>
            </tr>
        `).join('');

        const pages = Math.ceil(data.total / 20);
        const current = Math.floor(offset / 20);
        document.getElementById('callsPagination').innerHTML = Array.from({length: Math.min(pages, 10)}, (_, i) =>
            `<button class="${tw.pageBtn}${i === current ? ' active' : ''}" onclick="window._pages.calls.loadCalls(${i * 20})">${i + 1}</button>`
        ).join('');
    } catch (e) {
        loading.style.display = 'none';
        tbody.innerHTML = `<tr><td colspan="7" class="${tw.emptyState}">Failed to load calls: ${escapeHtml(e.message)}
            <br><button class="${tw.btnPrimary} ${tw.btnSm} mt-2" onclick="window._pages.calls.loadCalls(${offset})">Retry</button></td></tr>`;
    }
}

async function exportCallsCSV() {
    const params = new URLSearchParams();
    const df = document.getElementById('filterDateFrom').value;
    const dt = document.getElementById('filterDateTo').value;
    const sc = document.getElementById('filterScenario').value;
    const tr = document.getElementById('filterTransferred').value;
    const qb = document.getElementById('filterQualityBelow').value;
    if (df) params.set('date_from', df);
    if (dt) params.set('date_to', dt);
    if (sc) params.set('scenario', sc);
    if (tr) params.set('transferred', tr);
    if (qb) params.set('min_quality', qb);

    try {
        const res = await fetchWithAuth(`/analytics/calls/export?${params}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        const disposition = res.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename="(.+)"/);
        const filename = match ? match[1] : 'calls_export.csv';
        downloadBlob(blob, filename);
        showToast('CSV exported');
    } catch (e) {
        showToast('Export failed: ' + e.message, 'error');
    }
}

async function showCallDetail(callId) {
    try {
        const data = await api(`/analytics/calls/${callId}`);
        const c = data.call;
        const turns = data.turns || [];
        const tools = data.tool_calls || [];

        let html = `
            <div class="grid grid-cols-2 gap-3 mb-4 text-sm">
                <div><span class="font-medium text-neutral-500 dark:text-neutral-400">Caller:</span> <span class="text-neutral-900 dark:text-neutral-100">${escapeHtml(c.caller_id) || '-'}</span></div>
                <div><span class="font-medium text-neutral-500 dark:text-neutral-400">Scenario:</span> <span class="text-neutral-900 dark:text-neutral-100">${escapeHtml(c.scenario) || '-'}</span></div>
                <div><span class="font-medium text-neutral-500 dark:text-neutral-400">Duration:</span> <span class="text-neutral-900 dark:text-neutral-100">${c.duration_seconds || 0}s</span></div>
                <div><span class="font-medium text-neutral-500 dark:text-neutral-400">Quality:</span> ${qualityBadge(c.quality_score)}</div>
                <div><span class="font-medium text-neutral-500 dark:text-neutral-400">Cost:</span> <span class="text-neutral-900 dark:text-neutral-100">$${(c.total_cost_usd || 0).toFixed(3)}</span></div>
                <div><span class="font-medium text-neutral-500 dark:text-neutral-400">Prompt:</span> <span class="text-neutral-900 dark:text-neutral-100">${escapeHtml(c.prompt_version) || '-'}</span></div>
            </div>
        `;

        if (c.quality_details) {
            html += `<h3 class="${tw.sectionTitle} mt-4">Quality Breakdown</h3><div class="overflow-x-auto"><table class="${tw.table}">`;
            for (const [k, v] of Object.entries(c.quality_details)) {
                if (k === 'comment') continue;
                html += `<tr class="${tw.trHover}"><td class="${tw.td}">${escapeHtml(k)}</td><td class="${tw.td}">${qualityBadge(v)}</td></tr>`;
            }
            if (c.quality_details.comment) {
                html += `<tr><td colspan="2" class="${tw.td} italic">${escapeHtml(c.quality_details.comment)}</td></tr>`;
            }
            html += '</table></div>';
        }

        html += `<h3 class="${tw.sectionTitle} mt-4">Transcription</h3>`;
        if (turns.length === 0) {
            html += `<div class="${tw.emptyState}">No transcription available</div>`;
        } else {
            turns.forEach(t => {
                html += `<div class="${tw.turnWrap}"><span class="${t.speaker === 'customer' ? tw.speakerCustomer : tw.speakerBot}">${t.speaker === 'customer' ? 'Customer' : 'Bot'}</span>`;
                html += `<div class="${tw.turnText}">${escapeHtml(t.text)}</div></div>`;
            });
        }

        if (tools.length) {
            html += `<h3 class="${tw.sectionTitle} mt-4">Tool Calls</h3><div class="overflow-x-auto"><table class="${tw.table}"><tr><th class="${tw.th}">Tool</th><th class="${tw.th}">Success</th><th class="${tw.th}">Duration</th></tr>`;
            tools.forEach(t => {
                html += `<tr class="${tw.trHover}"><td class="${tw.td}">${escapeHtml(t.tool_name)}</td><td class="${tw.td}">${t.success ? `<span class="${tw.badgeGreen}">Yes</span>` : `<span class="${tw.badgeRed}">No</span>`}</td><td class="${tw.td}">${t.duration_ms || 0}ms</td></tr>`;
            });
            html += '</table></div>';
        }

        document.getElementById('callDetailContent').innerHTML = html;
        document.getElementById('callModal').classList.add('show');
    } catch (e) {
        showToast('Failed to load call details: ' + e.message, 'error');
    }
}

function closeCallModal() {
    document.getElementById('callModal').classList.remove('show');
}

export function getCallsOffset() { return callsOffset; }

export function init() {
    registerPageLoader('calls', () => loadCalls());
}

window._pages = window._pages || {};
window._pages.calls = { loadCalls, exportCallsCSV, showCallDetail, closeCallModal };
