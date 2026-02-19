import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { formatDate, escapeHtml, closeModal } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import * as tw from '../tw.js';

// ─── State ───────────────────────────────────────────────────
let _activeTab = 'chat';
let _currentConvId = null;
let _currentConv = null;
let _turns = [];
let _sending = false;

// ─── Tab switching ───────────────────────────────────────────
function showTab(tab) {
    _activeTab = tab;
    ['chat', 'history', 'regression'].forEach(t => {
        const el = document.getElementById(`sandboxContent-${t}`);
        if (el) el.style.display = t === tab ? 'block' : 'none';
    });
    document.querySelectorAll('#page-sandbox .tab-bar button').forEach(b => b.classList.remove('active'));
    const activeBtn = document.querySelector(`#page-sandbox .tab-bar button[data-tab="${tab}"]`);
    if (activeBtn) activeBtn.classList.add('active');

    if (tab === 'chat') renderChat();
    if (tab === 'history') loadHistory();
    if (tab === 'regression') loadRegressionRuns();
}

// ═══════════════════════════════════════════════════════════
//  Chat Tab
// ═══════════════════════════════════════════════════════════
function renderChat() {
    const container = document.getElementById('sandboxChatContainer');
    if (!_currentConvId) {
        container.innerHTML = `
            <div class="text-center py-12">
                <p class="${tw.emptyState} mb-4">${t('sandbox.noConversation')}</p>
                <button class="${tw.btnPrimary}" onclick="window._pages.sandbox.showNewConvModal()">${t('sandbox.newConversation')}</button>
            </div>`;
        return;
    }
    loadConversation(_currentConvId);
}

async function loadConversation(convId) {
    const container = document.getElementById('sandboxChatContainer');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    try {
        const data = await api(`/admin/sandbox/conversations/${convId}`);
        _currentConv = data.item;
        _turns = data.turns || [];
        _currentConvId = convId;
        renderConversationView();
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('sandbox.loadFailed', { error: escapeHtml(e.message) })}</div>`;
    }
}

function renderConversationView() {
    const container = document.getElementById('sandboxChatContainer');
    const conv = _currentConv;
    const isActive = conv.status === 'active';

    // Header
    const toolBadge = conv.tool_mode === 'mock'
        ? `<span class="${tw.badgeYellow}">${t('sandbox.mock')}</span>`
        : `<span class="${tw.badgeGreen}">${t('sandbox.live')}</span>`;
    const promptBadge = conv.prompt_version_name
        ? `<span class="${tw.badgeBlue}">${escapeHtml(conv.prompt_version_name)}</span>`
        : `<span class="${tw.badge}">${t('sandbox.promptVersionDefault')}</span>`;
    const scenarioBadge = conv.scenario_type
        ? `<span class="${tw.badge}">${escapeHtml(conv.scenario_type)}</span>`
        : '';

    // Messages
    let messagesHtml = '';
    if (_turns.length === 0) {
        messagesHtml = `<div class="${tw.emptyState} py-12">${t('sandbox.noMessages')}</div>`;
    } else {
        messagesHtml = _turns.map(turn => renderTurn(turn)).join('');
    }

    container.innerHTML = `
        <div class="flex flex-wrap items-center gap-2 mb-4 pb-3 border-b border-neutral-200 dark:border-neutral-700">
            <h2 class="text-sm font-semibold text-neutral-900 dark:text-neutral-50 mr-2">${escapeHtml(conv.title)}</h2>
            ${promptBadge} ${toolBadge} ${scenarioBadge}
            <div class="ml-auto flex gap-2">
                <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.sandbox.showNewConvModal()">${t('sandbox.newConversation')}</button>
                ${isActive ? `<button class="${tw.btnSecondary} ${tw.btnSm}" onclick="window._pages.sandbox.archiveConversation()">${t('sandbox.archiveConversation')}</button>` : ''}
            </div>
        </div>
        <div id="sandboxMessages" class="max-h-[500px] overflow-y-auto mb-4 space-y-1">
            ${messagesHtml}
        </div>
        ${isActive ? `
        <div class="flex gap-2 pt-3 border-t border-neutral-200 dark:border-neutral-700">
            <input type="text" id="sandboxInput"
                data-i18n-placeholder="sandbox.messagePlaceholder"
                placeholder="${t('sandbox.messagePlaceholder')}"
                class="${tw.formInput} flex-1"
                onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();window._pages.sandbox.sendMessage();}">
            <button id="sandboxAutoBtn" class="${tw.btnPurple} ${tw.btnSm}" title="${t('sandbox.autoCustomerHint')}" onclick="window._pages.sandbox.autoCustomer()">${t('sandbox.autoCustomer')}</button>
            <button id="sandboxSendBtn" class="${tw.btnPrimary}" onclick="window._pages.sandbox.sendMessage()">${t('sandbox.sendMessage')}</button>
        </div>` : ''}`;

    // Scroll to bottom
    const msgArea = document.getElementById('sandboxMessages');
    if (msgArea) msgArea.scrollTop = msgArea.scrollHeight;
}

function renderTurn(turn) {
    const isCustomer = turn.speaker === 'customer';
    const speakerClass = isCustomer ? tw.speakerCustomer : tw.speakerBot;
    const speakerLabel = isCustomer ? t('sandbox.customer') : t('sandbox.agent');
    const bgClass = isCustomer
        ? 'bg-blue-50 dark:bg-blue-950/30'
        : 'bg-emerald-50 dark:bg-emerald-950/30';

    let toolCallsHtml = '';
    if (turn.tool_calls && turn.tool_calls.length > 0) {
        const rows = turn.tool_calls.map(tc => `
            <tr class="${tw.trHover}">
                <td class="${tw.td}"><span class="${tw.badgeBlue}">${escapeHtml(tc.tool_name)}</span></td>
                <td class="${tw.td}"><code class="text-xs break-all">${escapeHtml(JSON.stringify(tc.tool_args).substring(0, 120))}</code></td>
                <td class="${tw.td}"><code class="text-xs break-all">${escapeHtml(JSON.stringify(tc.tool_result).substring(0, 120))}</code></td>
                <td class="${tw.td}">${tc.duration_ms != null ? tc.duration_ms + 'ms' : '-'}</td>
                <td class="${tw.td}">${tc.is_mock ? `<span class="${tw.badgeYellow}">${t('sandbox.mock')}</span>` : `<span class="${tw.badgeGreen}">${t('sandbox.live')}</span>`}</td>
            </tr>`).join('');

        toolCallsHtml = `
            <details class="mt-1">
                <summary class="text-xs text-neutral-500 dark:text-neutral-400 cursor-pointer hover:text-neutral-700 dark:hover:text-neutral-200">
                    ${t('sandbox.toolCalls')} (${turn.tool_calls.length})
                </summary>
                <div class="mt-1 overflow-x-auto">
                    <table class="${tw.table}">
                        <thead><tr>
                            <th class="${tw.th}">${t('sandbox.toolName')}</th>
                            <th class="${tw.th}">${t('sandbox.toolArgs')}</th>
                            <th class="${tw.th}">${t('sandbox.toolResult')}</th>
                            <th class="${tw.th}">${t('sandbox.toolDuration')}</th>
                            <th class="${tw.th}">${t('sandbox.toolMode')}</th>
                        </tr></thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>
            </details>`;
    }

    let metricsHtml = '';
    if (!isCustomer && turn.llm_latency_ms != null) {
        metricsHtml = `<div class="${tw.mutedText} mt-1">${t('sandbox.metrics', {
            latency: turn.llm_latency_ms,
            input: turn.input_tokens || 0,
            output: turn.output_tokens || 0,
            model: turn.model || '-'
        })}</div>`;
    }

    let ratingHtml = '';
    let branchHtml = '';
    if (!isCustomer) {
        const stars = [1, 2, 3, 4, 5].map(s => {
            const filled = turn.rating && s <= turn.rating;
            const color = filled ? 'text-amber-400' : 'text-neutral-300 dark:text-neutral-600';
            return `<span class="cursor-pointer ${color} text-lg" onclick="window._pages.sandbox.rateTurn('${turn.id}', ${s})">&#9733;</span>`;
        }).join('');
        ratingHtml = `<div class="mt-1 flex items-center gap-1">${stars}${turn.rating_comment ? `<span class="${tw.mutedText} ml-2">${escapeHtml(turn.rating_comment)}</span>` : ''}</div>`;
        branchHtml = `<button class="${tw.btnSecondary} ${tw.btnSm} mt-1" onclick="window._pages.sandbox.branchFrom('${turn.id}', ${turn.turn_number})">${t('sandbox.branch')}</button>`;
    }

    return `
        <div class="${bgClass} rounded-lg px-3 py-2">
            <div class="flex items-center gap-2">
                <span class="${speakerClass}">${speakerLabel}</span>
                <span class="${tw.mutedText}">${formatDate(turn.created_at)}</span>
            </div>
            <div class="${tw.turnText}">${escapeHtml(turn.content)}</div>
            ${toolCallsHtml}
            ${metricsHtml}
            <div class="flex items-center gap-2 flex-wrap">
                ${ratingHtml}
                ${branchHtml}
            </div>
        </div>`;
}

async function sendMessage() {
    if (_sending) return;
    const input = document.getElementById('sandboxInput');
    const msg = input?.value?.trim();
    if (!msg || !_currentConvId) return;

    _sending = true;
    const btn = document.getElementById('sandboxSendBtn');
    if (btn) btn.disabled = true;
    input.value = '';

    try {
        await api(`/admin/sandbox/conversations/${_currentConvId}/send`, {
            method: 'POST',
            body: JSON.stringify({ message: msg }),
        });
        await loadConversation(_currentConvId);
    } catch (e) {
        showToast(t('sandbox.sendFailed', { error: e.message }), 'error');
    } finally {
        _sending = false;
        if (btn) btn.disabled = false;
        // Refocus input
        const newInput = document.getElementById('sandboxInput');
        if (newInput) newInput.focus();
    }
}

async function rateTurn(turnId, rating) {
    try {
        await api(`/admin/sandbox/turns/${turnId}/rate`, {
            method: 'PATCH',
            body: JSON.stringify({ rating }),
        });
        showToast(t('sandbox.ratingSaved'));
        // Refresh the turn inline
        if (_currentConvId) await loadConversation(_currentConvId);
    } catch (e) {
        showToast(t('sandbox.ratingFailed', { error: e.message }), 'error');
    }
}

function branchFrom(turnId, turnNumber) {
    const msg = prompt(t('sandbox.branchFrom', { turn: turnNumber }));
    if (!msg || !msg.trim()) return;

    _sending = true;
    api(`/admin/sandbox/conversations/${_currentConvId}/send`, {
        method: 'POST',
        body: JSON.stringify({ message: msg.trim(), parent_turn_id: turnId }),
    }).then(() => {
        loadConversation(_currentConvId);
    }).catch(e => {
        showToast(t('sandbox.sendFailed', { error: e.message }), 'error');
    }).finally(() => { _sending = false; });
}

async function autoCustomer() {
    const btn = document.getElementById('sandboxAutoBtn');
    if (btn) { btn.disabled = true; btn.textContent = t('sandbox.generating'); }

    try {
        const data = await api(`/admin/sandbox/conversations/${_currentConvId}/auto-customer`, {
            method: 'POST',
            body: JSON.stringify({ persona: 'neutral' }),
        });
        const input = document.getElementById('sandboxInput');
        if (input && data.suggested_message) {
            input.value = data.suggested_message;
            input.focus();
        }
    } catch (e) {
        showToast(t('sandbox.sendFailed', { error: e.message }), 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = t('sandbox.autoCustomer'); }
    }
}

async function archiveConversation() {
    if (!_currentConvId) return;
    try {
        await api(`/admin/sandbox/conversations/${_currentConvId}`, {
            method: 'PATCH',
            body: JSON.stringify({ status: 'archived' }),
        });
        showToast(t('sandbox.archived'));
        _currentConvId = null;
        _currentConv = null;
        _turns = [];
        renderChat();
    } catch (e) {
        showToast(t('sandbox.loadFailed', { error: e.message }), 'error');
    }
}

// ═══════════════════════════════════════════════════════════
//  New Conversation Modal
// ═══════════════════════════════════════════════════════════
async function showNewConvModal() {
    document.getElementById('sandboxConvTitle').value = '';
    document.getElementById('sandboxConvTags').value = '';
    document.getElementById('sandboxConvToolMode').value = 'mock';
    document.getElementById('sandboxConvScenario').value = '';

    // Load prompt versions for select
    try {
        const data = await api('/prompts');
        const select = document.getElementById('sandboxConvPrompt');
        select.innerHTML = `<option value="">${t('sandbox.promptVersionDefault')}</option>`;
        const versions = data.versions || data.items || [];
        for (const v of versions) {
            const activeLabel = v.is_active ? ' *' : '';
            select.innerHTML += `<option value="${v.id}">${escapeHtml(v.name)}${activeLabel}</option>`;
        }
    } catch { /* ignore — select keeps default */ }

    document.getElementById('sandboxNewConvModal').classList.add('show');
}

async function createConversation() {
    const title = document.getElementById('sandboxConvTitle').value.trim();
    if (!title) { showToast(t('sandbox.conversationTitle'), 'error'); return; }

    const promptId = document.getElementById('sandboxConvPrompt').value || null;
    const toolMode = document.getElementById('sandboxConvToolMode').value;
    const scenarioType = document.getElementById('sandboxConvScenario').value || null;
    const tagsRaw = document.getElementById('sandboxConvTags').value.trim();
    const tags = tagsRaw ? tagsRaw.split(',').map(t => t.trim()).filter(Boolean) : [];

    try {
        const body = { title, tool_mode: toolMode, tags };
        if (promptId) body.prompt_version_id = promptId;
        if (scenarioType) body.scenario_type = scenarioType;

        const result = await api('/admin/sandbox/conversations', {
            method: 'POST',
            body: JSON.stringify(body),
        });
        closeModal('sandboxNewConvModal');
        showToast(t('sandbox.created'));
        _currentConvId = result.item.id;
        showTab('chat');
    } catch (e) {
        showToast(t('sandbox.loadFailed', { error: e.message }), 'error');
    }
}

// ═══════════════════════════════════════════════════════════
//  History Tab
// ═══════════════════════════════════════════════════════════
async function loadHistory() {
    const container = document.getElementById('sandboxHistoryContainer');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    const search = document.getElementById('sandboxSearch')?.value?.trim() || '';
    const status = document.getElementById('sandboxFilterStatus')?.value || '';
    const scenario = document.getElementById('sandboxFilterScenario')?.value || '';

    const params = new URLSearchParams();
    if (search) params.set('search', search);
    if (status) params.set('status', status);
    if (scenario) params.set('scenario_type', scenario);
    params.set('limit', '50');

    try {
        const data = await api(`/admin/sandbox/conversations?${params}`);
        const items = data.items || [];

        if (items.length === 0) {
            container.innerHTML = `
                <div class="${tw.emptyState}">${t('sandbox.noConversations')}</div>
                <div class="text-center mt-3">
                    <button class="${tw.btnPrimary}" onclick="window._pages.sandbox.showNewConvModal()">${t('sandbox.newConversation')}</button>
                </div>`;
            return;
        }

        const rows = items.map(item => {
            const statusBadge = item.status === 'active'
                ? `<span class="${tw.badgeGreen}">${t('sandbox.active')}</span>`
                : `<span class="${tw.badgeGray}">${t('sandbox.archivedStatus')}</span>`;
            const toolBadge = item.tool_mode === 'mock'
                ? `<span class="${tw.badgeYellow}">${t('sandbox.mock')}</span>`
                : `<span class="${tw.badgeGreen}">${t('sandbox.live')}</span>`;
            const avgRating = item.avg_rating != null
                ? parseFloat(item.avg_rating).toFixed(1) + ' &#9733;'
                : '-';

            return `
                <tr class="${tw.trHover} cursor-pointer" onclick="window._pages.sandbox.openConversation('${item.id}')">
                    <td class="${tw.td}">${escapeHtml(item.title)}</td>
                    <td class="${tw.td}">${item.prompt_version_name ? `<span class="${tw.badgeBlue}">${escapeHtml(item.prompt_version_name)}</span>` : '-'}</td>
                    <td class="${tw.td}">${item.scenario_type ? `<span class="${tw.badge}">${escapeHtml(item.scenario_type)}</span>` : '-'}</td>
                    <td class="${tw.td}">${toolBadge}</td>
                    <td class="${tw.td}">${item.turns_count || 0}</td>
                    <td class="${tw.td}">${avgRating}</td>
                    <td class="${tw.td}">${formatDate(item.updated_at)}</td>
                    <td class="${tw.td}">${statusBadge}</td>
                    <td class="${tw.td}">
                        <button class="${tw.btnDanger} ${tw.btnSm}" onclick="event.stopPropagation();window._pages.sandbox.deleteConversation('${item.id}', '${escapeHtml(item.title).replace(/'/g, "\\'")}')">${t('common.delete')}</button>
                    </td>
                </tr>`;
        }).join('');

        container.innerHTML = `
            <div class="mb-3">
                <button class="${tw.btnPrimary}" onclick="window._pages.sandbox.showNewConvModal()">${t('sandbox.newConversation')}</button>
            </div>
            <div class="overflow-x-auto"><table class="${tw.table}"><thead><tr>
                <th class="${tw.th}">${t('sandbox.conversationTitle')}</th>
                <th class="${tw.th}">${t('sandbox.promptVersion')}</th>
                <th class="${tw.th}">${t('sandbox.scenarioType')}</th>
                <th class="${tw.th}">${t('sandbox.toolMode')}</th>
                <th class="${tw.th}">${t('sandbox.turnsCount')}</th>
                <th class="${tw.th}">${t('sandbox.avgRating')}</th>
                <th class="${tw.th}">${t('sandbox.lastActivity')}</th>
                <th class="${tw.th}">${t('sandbox.status')}</th>
                <th class="${tw.th}"></th>
            </tr></thead><tbody>${rows}</tbody></table></div>`;
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('sandbox.loadFailed', { error: escapeHtml(e.message) })}</div>`;
    }
}

function openConversation(convId) {
    _currentConvId = convId;
    showTab('chat');
}

async function deleteConversation(convId, title) {
    if (!confirm(t('sandbox.deleteConfirm', { title }))) return;
    try {
        await api(`/admin/sandbox/conversations/${convId}`, { method: 'DELETE' });
        showToast(t('sandbox.deleted'));
        if (_currentConvId === convId) {
            _currentConvId = null;
            _currentConv = null;
            _turns = [];
        }
        loadHistory();
    } catch (e) {
        showToast(t('sandbox.loadFailed', { error: e.message }), 'error');
    }
}

// ═══════════════════════════════════════════════════════════
//  Regression Tab (Phase 3)
// ═══════════════════════════════════════════════════════════
async function loadRegressionRuns() {
    const container = document.getElementById('sandboxRegressionContainer');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    try {
        const data = await api('/admin/sandbox/regression-runs?limit=50');
        const items = data.items || [];

        if (items.length === 0) {
            container.innerHTML = `<div class="${tw.emptyState}">${t('sandbox.noRegressions')}</div>`;
            return;
        }

        function statusBadge(s) {
            const map = {
                pending: tw.badge,
                running: tw.badgeYellow,
                completed: tw.badgeGreen,
                failed: tw.badgeRed,
            };
            const labelMap = {
                pending: t('sandbox.pending'),
                running: t('sandbox.running'),
                completed: t('sandbox.completed'),
                failed: t('sandbox.failed'),
            };
            return `<span class="${map[s] || tw.badge}">${labelMap[s] || s}</span>`;
        }

        const rows = items.map(item => `
            <tr class="${tw.trHover} cursor-pointer" onclick="window._pages.sandbox.showRegressionDetail('${item.id}')">
                <td class="${tw.td}">${escapeHtml(item.source_title || '-')}</td>
                <td class="${tw.td}">${item.prompt_version_name ? `<span class="${tw.badgeBlue}">${escapeHtml(item.prompt_version_name)}</span>` : '-'}</td>
                <td class="${tw.td}">${statusBadge(item.status)}</td>
                <td class="${tw.td}">${item.turns_compared || '-'}</td>
                <td class="${tw.td}">${item.avg_source_rating != null ? parseFloat(item.avg_source_rating).toFixed(1) : '-'}</td>
                <td class="${tw.td}">${item.error_message ? `<span class="${tw.mutedText}">${escapeHtml(item.error_message.substring(0, 50))}</span>` : '-'}</td>
                <td class="${tw.td}">${formatDate(item.created_at)}</td>
            </tr>`).join('');

        container.innerHTML = `
            <div class="overflow-x-auto"><table class="${tw.table}"><thead><tr>
                <th class="${tw.th}">${t('sandbox.conversationTitle')}</th>
                <th class="${tw.th}">${t('sandbox.promptVersion')}</th>
                <th class="${tw.th}">${t('sandbox.status')}</th>
                <th class="${tw.th}">${t('sandbox.turnsCompared')}</th>
                <th class="${tw.th}">${t('sandbox.sourceRating')}</th>
                <th class="${tw.th}">Error</th>
                <th class="${tw.th}">${t('sandbox.lastActivity')}</th>
            </tr></thead><tbody>${rows}</tbody></table></div>`;
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('sandbox.loadFailed', { error: escapeHtml(e.message) })}</div>`;
    }
}

async function showRegressionDetail(runId) {
    const container = document.getElementById('sandboxRegressionContainer');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    try {
        const data = await api(`/admin/sandbox/regression-runs/${runId}`);
        const item = data.item;
        const summary = item.summary || {};
        const diffs = summary.turn_diffs || [];

        let diffsHtml = '';
        if (diffs.length > 0) {
            diffsHtml = diffs.map(d => `
                <div class="${tw.card}">
                    <div class="${tw.mutedText} mb-1">Turn ${d.turn_number}: ${escapeHtml(d.customer_message)}</div>
                    <div class="grid grid-cols-2 gap-3">
                        <div>
                            <div class="text-xs font-semibold text-neutral-500 mb-1">${t('sandbox.sourceResponse')}</div>
                            <div class="text-sm bg-neutral-50 dark:bg-neutral-800 rounded p-2">${escapeHtml(d.source_response)}</div>
                        </div>
                        <div>
                            <div class="text-xs font-semibold text-neutral-500 mb-1">${t('sandbox.newResponse')}</div>
                            <div class="text-sm bg-emerald-50 dark:bg-emerald-950/30 rounded p-2">${escapeHtml(d.new_response)}</div>
                        </div>
                    </div>
                    ${d.diff_lines && d.diff_lines.length > 0 ? `
                        <details class="mt-2">
                            <summary class="${tw.mutedText} cursor-pointer">${t('sandbox.diffView')}</summary>
                            <pre class="text-xs bg-neutral-900 text-neutral-100 rounded p-2 mt-1 overflow-x-auto">${escapeHtml(d.diff_lines.join('\n'))}</pre>
                        </details>` : ''}
                </div>`).join('');
        }

        container.innerHTML = `
            <div class="mb-3">
                <button class="${tw.btnSecondary} ${tw.btnSm}" onclick="window._pages.sandbox.loadRegressionRuns()">&larr; ${t('sandbox.regression')}</button>
            </div>
            <div class="${tw.card}">
                <div class="grid grid-cols-4 gap-4 mb-3">
                    <div><span class="${tw.mutedText}">${t('sandbox.status')}</span><br>${escapeHtml(item.status)}</div>
                    <div><span class="${tw.mutedText}">${t('sandbox.turnsCompared')}</span><br>${item.turns_compared || 0}</div>
                    <div><span class="${tw.mutedText}">${t('sandbox.sourceRating')}</span><br>${item.avg_source_rating != null ? parseFloat(item.avg_source_rating).toFixed(1) : '-'}</div>
                    <div><span class="${tw.mutedText}">${t('sandbox.promptVersion')}</span><br>${escapeHtml(item.prompt_version_name || '-')}</div>
                </div>
                ${item.error_message ? `<div class="${tw.badgeRed} mb-2">${escapeHtml(item.error_message)}</div>` : ''}
                ${item.new_conversation_id ? `<button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.sandbox.openConversation('${item.new_conversation_id}');window._pages.sandbox.showTab('chat')">Open new conversation</button>` : ''}
            </div>
            ${diffsHtml}`;
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('sandbox.loadFailed', { error: escapeHtml(e.message) })}</div>`;
    }
}

async function replayConversation(convId) {
    // Load prompt versions and show a simple selection
    let versions = [];
    try {
        const data = await api('/prompts');
        versions = data.versions || data.items || [];
    } catch { /* ignore */ }

    if (versions.length === 0) {
        showToast(t('sandbox.regressionFailed', { error: 'No prompt versions available' }), 'error');
        return;
    }

    const options = versions.map(v => `${v.name} (${v.id})`).join('\n');
    const choice = prompt(`${t('sandbox.selectPromptVersion')}:\n${options}`);
    if (!choice) return;

    // Find the selected version
    const selected = versions.find(v => choice.includes(v.id) || choice.includes(v.name));
    if (!selected) { showToast(t('sandbox.regressionFailed', { error: 'Version not found' }), 'error'); return; }

    try {
        const result = await api(`/admin/sandbox/conversations/${convId}/replay`, {
            method: 'POST',
            body: JSON.stringify({ new_prompt_version_id: selected.id }),
        });
        showToast(t('sandbox.regressionStarted'));
        showTab('regression');
    } catch (e) {
        showToast(t('sandbox.regressionFailed', { error: e.message }), 'error');
    }
}

async function toggleBaseline(convId, isBaseline) {
    try {
        await api(`/admin/sandbox/conversations/${convId}`, {
            method: 'PATCH',
            body: JSON.stringify({ is_baseline: !isBaseline }),
        });
        if (_currentConvId === convId) await loadConversation(convId);
        else loadHistory();
    } catch (e) {
        showToast(t('sandbox.loadFailed', { error: e.message }), 'error');
    }
}

// ─── Init ────────────────────────────────────────────────────
export function init() {
    registerPageLoader('sandbox', () => showTab(_activeTab));
}

window._pages = window._pages || {};
window._pages.sandbox = {
    showTab,
    loadHistory,
    openConversation,
    showNewConvModal,
    createConversation,
    sendMessage,
    rateTurn,
    archiveConversation,
    deleteConversation,
    branchFrom,
    autoCustomer,
    loadRegressionRuns,
    showRegressionDetail,
    replayConversation,
    toggleBaseline,
};
