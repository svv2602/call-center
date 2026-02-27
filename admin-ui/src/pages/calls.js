import { api, fetchWithAuth } from '../api.js';
import { showToast } from '../notifications.js';
import { qualityBadge, formatDate, escapeHtml, downloadBlob, updateTimestamp } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import { makeSortable } from '../sorting.js';
import * as tw from '../tw.js';

let callsOffset = 0;
let _tenants = [];

async function _loadTenants() {
    try {
        const data = await api('/admin/tenants?is_active=true&limit=100');
        _tenants = data.tenants || [];
    } catch {
        _tenants = [];
    }
}

function _populateTenantFilter() {
    const sel = document.getElementById('filterTenant');
    if (!sel || _tenants.length === 0) return;
    // Keep "All" option, add tenants
    const current = sel.value;
    sel.innerHTML = `<option value="">${t('dashboard.allNetworks')}</option>`;
    for (const ten of _tenants) {
        const opt = document.createElement('option');
        opt.value = ten.id;
        opt.textContent = ten.name;
        if (ten.id === current) opt.selected = true;
        sel.appendChild(opt);
    }
}

async function loadCalls(offset = 0) {
    callsOffset = offset;
    const loading = document.getElementById('callsLoading');
    const tbody = document.querySelector('#callsTable tbody');
    if (loading) loading.style.display = 'flex';

    const params = new URLSearchParams({ limit: 20, offset });
    const df = document.getElementById('filterDateFrom')?.value;
    const dt = document.getElementById('filterDateTo')?.value;
    const sc = document.getElementById('filterScenario')?.value;
    const tr = document.getElementById('filterTransferred')?.value;
    const qb = document.getElementById('filterQualityBelow')?.value;
    const search = document.getElementById('filterSearch')?.value;
    const tenantId = document.getElementById('filterTenant')?.value;
    if (df) params.set('date_from', df);
    if (dt) params.set('date_to', dt);
    if (sc) params.set('scenario', sc);
    if (tr) params.set('transferred', tr);
    if (qb) params.set('quality_below', qb);
    if (search) params.set('search', search);
    if (tenantId) params.set('tenant_id', tenantId);

    try {
        const data = await api(`/analytics/calls?${params}`);
        if (loading) loading.style.display = 'none';

        if (!data.calls || data.calls.length === 0) {
            if (tbody) tbody.innerHTML = `<tr><td colspan="7" class="${tw.emptyState}">${t('calls.noCalls')}</td></tr>`;
            const pag = document.getElementById('callsPagination');
            if (pag) pag.innerHTML = '';
            return;
        }

        tbody.innerHTML = data.calls.map(c => `
            <tr class="${tw.trHover} cursor-pointer" data-id="${escapeHtml(c.id)}" onclick="window._pages.calls.showCallDetail(this.dataset.id)">
                <td class="${tw.td}" data-label="${t('calls.date')}" data-sort-value="${c.started_at || ''}">${formatDate(c.started_at)}</td>
                <td class="${tw.td}" data-label="${t('calls.caller')}">${escapeHtml(c.caller_id) || '-'}</td>
                <td class="${tw.td}" data-label="${t('calls.scenario')}">${escapeHtml(c.scenario) || '-'}</td>
                <td class="${tw.td}" data-label="${t('calls.duration')}">${c.duration_seconds || 0}s</td>
                <td class="${tw.td}" data-label="${t('calls.quality')}">${qualityBadge(c.quality_score)}</td>
                <td class="${tw.td}" data-label="${t('calls.cost')}">$${(c.total_cost_usd || 0).toFixed(3)}</td>
                <td class="${tw.td}" data-label="${t('calls.status')}">${c.transferred_to_operator ? `<span class="${tw.badgeYellow}">${t('calls.statusTransferred')}</span>` : `<span class="${tw.badgeGreen}">${t('calls.statusResolved')}</span>`}</td>
            </tr>
        `).join('');

        const pages = Math.ceil(data.total / 20);
        const current = Math.floor(offset / 20);
        document.getElementById('callsPagination').innerHTML = Array.from({length: Math.min(pages, 10)}, (_, i) =>
            `<button class="${tw.pageBtn}${i === current ? ' active' : ''}" onclick="window._pages.calls.loadCalls(${i * 20})">${i + 1}</button>`
        ).join('');

        makeSortable('callsTable');
        updateTimestamp('callsLastUpdated');
    } catch (e) {
        loading.style.display = 'none';
        tbody.innerHTML = `<tr><td colspan="7" class="${tw.emptyState}">${t('calls.failedToLoad', {error: escapeHtml(e.message)})}
            <br><button class="${tw.btnPrimary} ${tw.btnSm} mt-2" onclick="window._pages.calls.loadCalls(${offset})">${t('common.retry')}</button></td></tr>`;
    }
}

async function exportCallsCSV() {
    const params = new URLSearchParams();
    const df = document.getElementById('filterDateFrom')?.value;
    const dt = document.getElementById('filterDateTo')?.value;
    const sc = document.getElementById('filterScenario')?.value;
    const tr = document.getElementById('filterTransferred')?.value;
    const qb = document.getElementById('filterQualityBelow')?.value;
    const tenantId = document.getElementById('filterTenant')?.value;
    if (df) params.set('date_from', df);
    if (dt) params.set('date_to', dt);
    if (sc) params.set('scenario', sc);
    if (tr) params.set('transferred', tr);
    if (qb) params.set('min_quality', qb);
    if (tenantId) params.set('tenant_id', tenantId);

    try {
        const res = await fetchWithAuth(`/analytics/calls/export?${params}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        const disposition = res.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename="(.+)"/);
        const filename = match ? match[1] : 'calls_export.csv';
        downloadBlob(blob, filename);
        showToast(t('calls.csvExported'));
    } catch (e) {
        showToast(t('calls.exportFailed', {error: e.message}), 'error');
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
                <div><span class="font-medium text-neutral-500 dark:text-neutral-400">${t('calls.detailCaller')}</span> <span class="text-neutral-900 dark:text-neutral-100">${escapeHtml(c.caller_id) || '-'}</span></div>
                <div><span class="font-medium text-neutral-500 dark:text-neutral-400">${t('calls.detailScenario')}</span> <span class="text-neutral-900 dark:text-neutral-100">${escapeHtml(c.scenario) || '-'}</span></div>
                <div><span class="font-medium text-neutral-500 dark:text-neutral-400">${t('calls.detailDuration')}</span> <span class="text-neutral-900 dark:text-neutral-100">${c.duration_seconds || 0}s</span></div>
                <div><span class="font-medium text-neutral-500 dark:text-neutral-400">${t('calls.detailQuality')}</span> ${qualityBadge(c.quality_score)}</div>
                <div><span class="font-medium text-neutral-500 dark:text-neutral-400">${t('calls.detailCost')}</span> <span class="text-neutral-900 dark:text-neutral-100">$${(c.total_cost_usd || 0).toFixed(3)}</span></div>
                <div><span class="font-medium text-neutral-500 dark:text-neutral-400">${t('calls.detailPrompt')}</span> <span class="text-neutral-900 dark:text-neutral-100">${escapeHtml(c.prompt_version) || '-'}</span></div>
            </div>
        `;

        if (c.quality_details) {
            html += `<h3 class="${tw.sectionTitle} mt-4">${t('calls.qualityBreakdown')}</h3><div class="overflow-x-auto"><table class="${tw.table}">`;
            for (const [k, v] of Object.entries(c.quality_details)) {
                if (k === 'comment') continue;
                html += `<tr class="${tw.trHover}"><td class="${tw.td}">${escapeHtml(k)}</td><td class="${tw.td}">${qualityBadge(v)}</td></tr>`;
            }
            if (c.quality_details.comment) {
                html += `<tr><td colspan="2" class="${tw.td} italic">${escapeHtml(c.quality_details.comment)}</td></tr>`;
            }
            html += '</table></div>';
        }

        if (c.cost_breakdown) {
            const cb = c.cost_breakdown;
            html += `<h3 class="${tw.sectionTitle} mt-4">${t('calls.costBreakdownTitle')}</h3><div class="overflow-x-auto"><table class="${tw.table}">`;
            html += `<tr class="${tw.trHover}"><td class="${tw.td} font-medium">${t('calls.sttProvider')}</td><td class="${tw.td}">${escapeHtml(cb.stt_provider || 'google')}</td></tr>`;
            html += `<tr class="${tw.trHover}"><td class="${tw.td} font-medium">${t('calls.sttSeconds')}</td><td class="${tw.td}">${cb.stt_seconds ?? '-'}</td></tr>`;
            html += `<tr class="${tw.trHover}"><td class="${tw.td} font-medium">${t('calls.sttCost')}</td><td class="${tw.td}">$${(cb.stt_cost || 0).toFixed(6)}</td></tr>`;
            html += `<tr class="${tw.trHover}"><td class="${tw.td} font-medium">${t('calls.llmModel')}</td><td class="${tw.td}">${escapeHtml(cb.llm_model || '-')}</td></tr>`;
            html += `<tr class="${tw.trHover}"><td class="${tw.td} font-medium">${t('calls.llmInputTokens')}</td><td class="${tw.td}">${cb.llm_input_tokens ?? 0}</td></tr>`;
            html += `<tr class="${tw.trHover}"><td class="${tw.td} font-medium">${t('calls.llmOutputTokens')}</td><td class="${tw.td}">${cb.llm_output_tokens ?? 0}</td></tr>`;
            if (cb.llm_input_price_per_1m != null) {
                html += `<tr class="${tw.trHover}"><td class="${tw.td} font-medium">${t('calls.llmInputPrice')}</td><td class="${tw.td}">$${cb.llm_input_price_per_1m}</td></tr>`;
                html += `<tr class="${tw.trHover}"><td class="${tw.td} font-medium">${t('calls.llmOutputPrice')}</td><td class="${tw.td}">$${cb.llm_output_price_per_1m}</td></tr>`;
            }
            html += `<tr class="${tw.trHover}"><td class="${tw.td} font-medium">${t('calls.llmCost')}</td><td class="${tw.td}">$${(cb.llm_cost || 0).toFixed(6)}</td></tr>`;
            html += `<tr class="${tw.trHover}"><td class="${tw.td} font-medium">${t('calls.ttsCharacters')}</td><td class="${tw.td}">${cb.tts_characters ?? 0}</td></tr>`;
            html += `<tr class="${tw.trHover}"><td class="${tw.td} font-medium">${t('calls.ttsCost')}</td><td class="${tw.td}">$${(cb.tts_cost || 0).toFixed(6)}</td></tr>`;
            html += `<tr class="${tw.trHover} font-semibold"><td class="${tw.td}">${t('calls.totalCost')}</td><td class="${tw.td}">$${(cb.total_cost || 0).toFixed(6)}</td></tr>`;
            html += '</table></div>';
        }

        html += `<div class="flex items-center justify-between mt-4"><h3 class="${tw.sectionTitle}">${t('calls.transcription')}</h3><div class="flex gap-2"><button class="${tw.btnPrimary} ${tw.btnSm}" data-id="${escapeHtml(callId)}" onclick="window._pages.calls.importToSandbox(this.dataset.id, this)">${t('calls.openInSandbox')}</button><button class="${tw.btnPrimary} ${tw.btnSm}" data-id="${escapeHtml(callId)}" onclick="window._pages.calls.downloadTranscript(this.dataset.id)">${t('calls.downloadTranscript')}</button></div></div>`;
        if (turns.length === 0) {
            html += `<div class="${tw.emptyState}">${t('calls.noTranscription')}</div>`;
        } else {
            turns.forEach(turn => {
                html += `<div class="${tw.turnWrap}"><span class="${turn.speaker === 'customer' ? tw.speakerCustomer : tw.speakerBot}">${turn.speaker === 'customer' ? t('calls.customer') : t('calls.bot')}</span>`;
                html += `<div class="${tw.turnText}">${escapeHtml(turn.text)}</div></div>`;
            });
        }

        if (tools.length) {
            html += `<h3 class="${tw.sectionTitle} mt-4">${t('calls.toolCalls')}</h3><div class="overflow-x-auto"><table class="${tw.table}"><tr><th class="${tw.th}">${t('calls.toolName')}</th><th class="${tw.th}">${t('calls.toolSuccess')}</th><th class="${tw.th}">${t('calls.toolDuration')}</th></tr>`;
            tools.forEach((tc, idx) => {
                const hasDetail = tc.tool_args || tc.tool_result;
                html += `<tr class="${tw.trHover}${hasDetail ? ' cursor-pointer' : ''}" ${hasDetail ? `onclick="window._pages.calls.toggleToolDetail(${idx})"` : ''}>
                    <td class="${tw.td}"><span class="inline-flex items-center gap-1">${hasDetail ? `<svg class="w-3.5 h-3.5 text-neutral-400 transition-transform tool-chevron-${idx}" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M7.21 14.77a.75.75 0 01.02-1.06L11.168 10 7.23 6.29a.75.75 0 111.04-1.08l4.5 4.25a.75.75 0 010 1.08l-4.5 4.25a.75.75 0 01-1.06-.02z" clip-rule="evenodd"/></svg>` : ''}${escapeHtml(tc.tool_name)}</span></td>
                    <td class="${tw.td}">${tc.success ? `<span class="${tw.badgeGreen}">${t('common.yes')}</span>` : `<span class="${tw.badgeRed}">${t('common.no')}</span>`}</td>
                    <td class="${tw.td}">${tc.duration_ms || 0}ms</td></tr>`;
                if (hasDetail) {
                    html += `<tr id="toolDetail-${idx}" class="hidden"><td colspan="3" class="px-3 py-2 border-b border-neutral-100 dark:border-neutral-800">`;
                    if (tc.tool_args && Object.keys(tc.tool_args).length) {
                        html += `<div class="mb-2"><span class="text-xs font-semibold text-neutral-500 dark:text-neutral-400">${t('calls.toolArgs')}</span><pre class="mt-1 p-2 text-xs bg-neutral-50 dark:bg-neutral-800 rounded-md overflow-x-auto text-neutral-700 dark:text-neutral-300">${escapeHtml(JSON.stringify(tc.tool_args, null, 2))}</pre></div>`;
                    }
                    if (tc.tool_result) {
                        html += `<div><span class="text-xs font-semibold text-neutral-500 dark:text-neutral-400">${t('calls.toolResult')}</span><pre class="mt-1 p-2 text-xs bg-neutral-50 dark:bg-neutral-800 rounded-md overflow-x-auto text-neutral-700 dark:text-neutral-300 max-h-64 overflow-y-auto">${escapeHtml(JSON.stringify(tc.tool_result, null, 2))}</pre></div>`;
                    }
                    html += '</td></tr>';
                }
            });
            html += '</table></div>';
        }

        document.getElementById('callDetailContent').innerHTML = html;
        document.getElementById('callModal').classList.add('show');
    } catch (e) {
        showToast(t('calls.failedToLoadDetail', {error: e.message}), 'error');
    }
}

async function downloadTranscript(callId) {
    try {
        const res = await fetchWithAuth(`/analytics/calls/${callId}/transcript`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        const disposition = res.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename="(.+)"/);
        const filename = match ? match[1] : `transcript_${callId}.txt`;
        downloadBlob(blob, filename);
        showToast(t('calls.transcriptDownloaded'));
    } catch (e) {
        showToast(t('calls.transcriptFailed', {error: e.message}), 'error');
    }
}

async function importToSandbox(callId, btn) {
    const origText = btn.textContent;
    btn.disabled = true;
    btn.textContent = t('calls.importingToSandbox');
    try {
        const data = await api('/admin/sandbox/conversations/import-call', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ call_id: callId }),
        });
        showToast(t('calls.importedToSandbox'));
        closeCallModal();
        const convId = data.item?.id;
        if (window._app && window._app.showPage) {
            window._app.showPage('sandbox');
            if (convId) {
                setTimeout(() => {
                    if (window._pages.sandbox && window._pages.sandbox.openConversation) {
                        window._pages.sandbox.openConversation(convId);
                    }
                }, 300);
            }
        }
    } catch (e) {
        showToast(t('calls.importFailed', { error: e.message }), 'error');
        btn.disabled = false;
        btn.textContent = origText;
    }
}

function toggleToolDetail(idx) {
    const row = document.getElementById(`toolDetail-${idx}`);
    if (!row) return;
    const chevron = document.querySelector(`.tool-chevron-${idx}`);
    row.classList.toggle('hidden');
    if (chevron) chevron.style.transform = row.classList.contains('hidden') ? '' : 'rotate(90deg)';
}

function closeCallModal() {
    document.getElementById('callModal').classList.remove('show');
}

export function getCallsOffset() { return callsOffset; }

export function init() {
    registerPageLoader('calls', async () => {
        await _loadTenants();
        _populateTenantFilter();
        loadCalls();
    });
}

window._pages = window._pages || {};
window._pages.calls = { loadCalls, exportCallsCSV, showCallDetail, closeCallModal, downloadTranscript, importToSandbox, toggleToolDetail };
