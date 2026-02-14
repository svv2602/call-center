import { api, fetchWithAuth } from '../api.js';
import { showToast } from '../notifications.js';
import { qualityBadge, formatDate, escapeHtml, downloadBlob } from '../utils.js';
import { registerPageLoader } from '../router.js';

let callsOffset = 0;

async function loadCalls(offset = 0) {
    callsOffset = offset;
    const loading = document.getElementById('callsLoading');
    const tbody = document.querySelector('#callsTable tbody');
    loading.style.display = 'block';

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
            tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No calls found</td></tr>';
            document.getElementById('callsPagination').innerHTML = '';
            return;
        }

        tbody.innerHTML = data.calls.map(c => `
            <tr style="cursor:pointer" onclick="window._pages.calls.showCallDetail('${c.id}')">
                <td>${formatDate(c.started_at)}</td>
                <td>${escapeHtml(c.caller_id) || '-'}</td>
                <td>${escapeHtml(c.scenario) || '-'}</td>
                <td>${c.duration_seconds || 0}s</td>
                <td>${qualityBadge(c.quality_score)}</td>
                <td>$${(c.total_cost_usd || 0).toFixed(3)}</td>
                <td>${c.transferred_to_operator ? '<span class="badge badge-yellow">Transferred</span>' : '<span class="badge badge-green">Resolved</span>'}</td>
            </tr>
        `).join('');

        const pages = Math.ceil(data.total / 20);
        const current = Math.floor(offset / 20);
        document.getElementById('callsPagination').innerHTML = Array.from({length: Math.min(pages, 10)}, (_, i) =>
            `<button class="${i === current ? 'active' : ''}" onclick="window._pages.calls.loadCalls(${i * 20})">${i + 1}</button>`
        ).join('');
    } catch (e) {
        loading.style.display = 'none';
        tbody.innerHTML = `<tr><td colspan="7" class="empty-state">Failed to load calls: ${escapeHtml(e.message)}
            <br><button class="btn btn-primary btn-sm" onclick="window._pages.calls.loadCalls(${offset})" style="margin-top:.5rem">Retry</button></td></tr>`;
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
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:.8rem;margin-bottom:1rem">
                <div><strong>Caller:</strong> ${escapeHtml(c.caller_id) || '-'}</div>
                <div><strong>Scenario:</strong> ${escapeHtml(c.scenario) || '-'}</div>
                <div><strong>Duration:</strong> ${c.duration_seconds || 0}s</div>
                <div><strong>Quality:</strong> ${qualityBadge(c.quality_score)}</div>
                <div><strong>Cost:</strong> $${(c.total_cost_usd || 0).toFixed(3)}</div>
                <div><strong>Prompt:</strong> ${escapeHtml(c.prompt_version) || '-'}</div>
            </div>
        `;

        if (c.quality_details) {
            html += '<h3 style="margin:.8rem 0 .5rem">Quality Breakdown</h3><table>';
            for (const [k, v] of Object.entries(c.quality_details)) {
                if (k === 'comment') continue;
                html += `<tr><td>${escapeHtml(k)}</td><td>${qualityBadge(v)}</td></tr>`;
            }
            if (c.quality_details.comment) {
                html += `<tr><td colspan="2" style="font-style:italic">${escapeHtml(c.quality_details.comment)}</td></tr>`;
            }
            html += '</table>';
        }

        html += '<h3 style="margin:.8rem 0 .5rem">Transcription</h3>';
        if (turns.length === 0) {
            html += '<div class="empty-state">No transcription available</div>';
        } else {
            turns.forEach(t => {
                html += `<div class="turn"><span class="speaker ${t.speaker}">${t.speaker === 'customer' ? 'Customer' : 'Bot'}</span>`;
                html += `<div class="text">${escapeHtml(t.text)}</div></div>`;
            });
        }

        if (tools.length) {
            html += '<h3 style="margin:.8rem 0 .5rem">Tool Calls</h3><table><tr><th>Tool</th><th>Success</th><th>Duration</th></tr>';
            tools.forEach(t => {
                html += `<tr><td>${escapeHtml(t.tool_name)}</td><td>${t.success ? 'Yes' : 'No'}</td><td>${t.duration_ms || 0}ms</td></tr>`;
            });
            html += '</table>';
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
