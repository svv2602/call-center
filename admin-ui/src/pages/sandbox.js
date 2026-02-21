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
let _markingMode = false;
let _selectedTurnIds = new Set();
let _turnGroups = [];
let _ratingTurnId = null;
let _pendingRating = null;
let _branchTurnId = null;
let _branchTurnNumber = null;
let _replayConvId = null;
let _starterCache = [];

// ─── Tab switching ───────────────────────────────────────────
function showTab(tab) {
    _activeTab = tab;
    ['chat', 'regression', 'patterns', 'phrases', 'starters'].forEach(t => {
        const el = document.getElementById(`sandboxContent-${t}`);
        if (el) el.style.display = t === tab ? '' : 'none';
    });
    document.querySelectorAll('#page-sandbox .tab-bar button').forEach(b => b.classList.remove('active'));
    const activeBtn = document.querySelector(`#page-sandbox .tab-bar button[data-tab="${tab}"]`);
    if (activeBtn) activeBtn.classList.add('active');

    // Sidebar only visible on Chat tab
    const sidebar = document.getElementById('sandboxSidebar');
    if (sidebar) sidebar.style.display = tab === 'chat' ? '' : 'none';

    if (tab === 'chat') renderChat();
    if (tab === 'regression') loadRegressionRuns();
    if (tab === 'patterns') loadPatterns();
    if (tab === 'phrases') loadPhrases();
    if (tab === 'starters') loadStarters();
}

// ═══════════════════════════════════════════════════════════
//  Chat Tab
// ═══════════════════════════════════════════════════════════
function renderChat() {
    const container = document.getElementById('sandboxChatContainer');
    if (!_currentConvId) {
        container.innerHTML = `
            <div class="flex items-center justify-center h-full">
                <p class="${tw.emptyState}">${t('sandbox.noConversation')}</p>
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

        // Load turn groups
        try {
            const gdata = await api(`/admin/sandbox/conversations/${convId}/turn-groups`);
            _turnGroups = gdata.items || [];
        } catch { _turnGroups = []; }

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
    const modelBadge = conv.model
        ? `<span class="${tw.badgeGray}">${escapeHtml(conv.model.replace('claude-', '').replace(/-\d+$/, ''))}</span>`
        : '';
    const scenarioBadge = conv.scenario_type
        ? `<span class="${tw.badge}">${escapeHtml(conv.scenario_type)}</span>`
        : '';

    const tagsBadges = (conv.tags || []).map(tg => `<span class="${tw.badge}">${escapeHtml(tg)}</span>`).join(' ');
    const createdStr = conv.created_at ? formatDate(conv.created_at) : '-';
    const updatedStr = conv.updated_at ? formatDate(conv.updated_at) : '-';
    const avgRatingStr = conv.avg_rating != null ? parseFloat(conv.avg_rating).toFixed(1) + ' &#9733;' : '-';
    const turnsCountStr = conv.turns_count ?? _turns.length;

    const convInfoPanel = `
        <div class="mb-3 p-3 bg-neutral-50 dark:bg-neutral-800 rounded-md text-xs grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-2">
            <div><span class="text-neutral-500 dark:text-neutral-400">${t('sandbox.promptVersion')}:</span><br>${conv.prompt_version_name ? escapeHtml(conv.prompt_version_name) : t('sandbox.promptVersionDefault')}</div>
            <div><span class="text-neutral-500 dark:text-neutral-400">${t('sandbox.model')}:</span><br>${conv.model ? escapeHtml(conv.model) : t('sandbox.modelDefault')}</div>
            <div><span class="text-neutral-500 dark:text-neutral-400">${t('sandbox.toolMode')}:</span><br>${conv.tool_mode === 'mock' ? t('sandbox.toolModeMock') : t('sandbox.toolModeLive')}</div>
            <div><span class="text-neutral-500 dark:text-neutral-400">${t('sandbox.scenarioType')}:</span><br>${conv.scenario_type || t('sandbox.scenarioNone')}</div>
            <div><span class="text-neutral-500 dark:text-neutral-400">${t('sandbox.turnsCount')}:</span><br>${turnsCountStr}</div>
            <div><span class="text-neutral-500 dark:text-neutral-400">${t('sandbox.avgRating')}:</span><br>${avgRatingStr}</div>
            <div><span class="text-neutral-500 dark:text-neutral-400">${t('sandbox.status')}:</span><br>${conv.status === 'active' ? t('sandbox.active') : t('sandbox.archivedStatus')}</div>
            <div><span class="text-neutral-500 dark:text-neutral-400">${t('sandbox.lastActivity')}:</span><br>${updatedStr}</div>
            ${tagsBadges ? `<div class="col-span-2 sm:col-span-4"><span class="text-neutral-500 dark:text-neutral-400">${t('sandbox.tags')}:</span> ${tagsBadges}</div>` : ''}
        </div>`;

    const markingBtnClass = _markingMode ? tw.btnPrimary : tw.btnSecondary;
    const markingLabel = _markingMode ? t('sandbox.exitMarking') : t('sandbox.markingMode');

    // Messages
    let messagesHtml = '';
    if (_turns.length === 0) {
        messagesHtml = `<div class="${tw.emptyState} py-12">${t('sandbox.noMessages')}</div>`;
    } else {
        messagesHtml = _turns.map(turn => renderTurn(turn)).join('');
    }

    // Marking mode toolbar
    let markingToolbar = '';
    if (_markingMode && _selectedTurnIds.size >= 1) {
        markingToolbar = `
            <div class="flex items-center gap-2 mb-2 p-2 bg-yellow-50 dark:bg-yellow-950/20 rounded border border-yellow-200 dark:border-yellow-800">
                <span class="text-sm">${t('sandbox.turnsSelected', { count: _selectedTurnIds.size })}</span>
                <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.sandbox.showGroupModal()">${t('sandbox.createGroup')}</button>
            </div>`;
    } else if (_markingMode && _selectedTurnIds.size === 0) {
        markingToolbar = `
            <div class="flex items-center gap-2 mb-2 p-2 bg-blue-50 dark:bg-blue-950/20 rounded border border-blue-200 dark:border-blue-800">
                <span class="text-sm text-blue-700 dark:text-blue-300">${t('sandbox.markingHint')}</span>
            </div>`;
    }

    // Groups panel
    let groupsHtml = '';
    if (_turnGroups.length > 0) {
        const groupRows = _turnGroups.map(g => {
            const typeBadge = g.pattern_type === 'positive'
                ? `<span class="${tw.badgeGreen}">positive</span>`
                : `<span class="${tw.badgeRed}">negative</span>`;
            const exportBadge = g.is_exported
                ? `<span class="${tw.badgeBlue}">${t('sandbox.alreadyExported')}</span>`
                : `<button class="${tw.btnSecondary} ${tw.btnSm}" onclick="window._pages.sandbox.showExportModal('${g.id}')">${t('sandbox.exportToPatterns')}</button>`;
            const ratingStars = g.rating ? '&#9733;'.repeat(g.rating) : '-';
            const tagsHtml = (g.tags || []).map(tg => `<span class="${tw.badge}">${escapeHtml(tg)}</span>`).join(' ');
            return `
                <div class="p-2 rounded border ${g.pattern_type === 'positive' ? 'border-emerald-300 dark:border-emerald-800 bg-emerald-50/50 dark:bg-emerald-950/10' : 'border-red-300 dark:border-red-800 bg-red-50/50 dark:bg-red-950/10'} mb-2">
                    <div class="flex items-center gap-2 mb-1">
                        <span class="text-sm font-medium">${escapeHtml(g.intent_label)}</span>
                        ${typeBadge}
                        <span class="text-amber-400 text-sm">${ratingStars}</span>
                        <div class="ml-auto flex gap-1">
                            ${exportBadge}
                            <button class="${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.sandbox.deleteGroup('${g.id}')">&times;</button>
                        </div>
                    </div>
                    <div class="${tw.mutedText}">${g.turn_ids.length} ${t('sandbox.turnsCount').toLowerCase()} ${tagsHtml}</div>
                    ${g.rating_comment ? `<div class="${tw.mutedText}">${escapeHtml(g.rating_comment)}</div>` : ''}
                    ${g.correction ? `<div class="text-xs text-red-600 dark:text-red-400 mt-1">${t('sandbox.correction')}: ${escapeHtml(g.correction)}</div>` : ''}
                </div>`;
        }).join('');
        groupsHtml = `
            <details class="mb-3" open>
                <summary class="text-sm font-semibold text-neutral-700 dark:text-neutral-300 cursor-pointer mb-2">${t('sandbox.turnGroups')} (${_turnGroups.length})</summary>
                ${groupRows}
            </details>`;
    }

    // Preserve input value across re-renders
    const prevInput = document.getElementById('sandboxInput')?.value || '';

    container.innerHTML = `
        <div class="flex flex-col h-full">
            <div class="mb-3 pb-3 border-b border-neutral-200 dark:border-neutral-700">
                <div class="flex flex-wrap items-center gap-2">
                    <h2 class="text-sm font-semibold text-neutral-900 dark:text-neutral-50 mr-2">${escapeHtml(conv.title)}</h2>
                    ${promptBadge} ${toolBadge} ${modelBadge} ${scenarioBadge}
                    <div class="ml-auto flex flex-wrap gap-2">
                        <button class="${tw.btnSecondary} ${tw.btnSm}" onclick="document.getElementById('sandboxConvInfo').toggleAttribute('open')" title="${t('sandbox.convInfo')}">&#9432;</button>
                        <button class="${markingBtnClass} ${tw.btnSm}" onclick="window._pages.sandbox.toggleMarking()">${markingLabel}</button>
                        <button class="${conv.is_baseline ? tw.btnPrimary : tw.btnSecondary} ${tw.btnSm}" onclick="window._pages.sandbox.toggleBaseline('${conv.id}', ${!!conv.is_baseline})">${conv.is_baseline ? t('sandbox.unmarkBaseline') : t('sandbox.markBaseline')}</button>
                        ${isActive ? `<button class="${tw.btnSecondary} ${tw.btnSm}" onclick="window._pages.sandbox.archiveConversation()">${t('sandbox.archiveConversation')}</button>` : ''}
                    </div>
                </div>
                <details id="sandboxConvInfo" class="mt-2">
                    <summary class="text-xs text-blue-600 dark:text-blue-400 cursor-pointer">${t('sandbox.convInfo')}</summary>
                    ${convInfoPanel}
                </details>
            </div>
            ${groupsHtml}
            ${markingToolbar}
            <div id="sandboxMessages" class="flex-1 overflow-y-auto mb-3 space-y-2">
                ${messagesHtml}
            </div>
            ${isActive ? `
            <div class="flex gap-2 pt-3 border-t border-neutral-200 dark:border-neutral-700">
                <input type="text" id="sandboxInput"
                    data-i18n-placeholder="sandbox.messagePlaceholder"
                    placeholder="${t('sandbox.messagePlaceholder')}"
                    class="${tw.formInput} flex-1"
                    onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();window._pages.sandbox.sendMessage();}">
                <select id="sandboxPersonaSelect" class="px-1.5 py-1 text-xs border border-neutral-300 dark:border-neutral-700 rounded-md bg-white dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100 focus:outline-none focus:ring-2 focus:ring-blue-500" title="${t('sandbox.persona')}">
                    <option value="neutral">${t('sandbox.personaNeutral')}</option>
                    <option value="angry">${t('sandbox.personaAngry')}</option>
                    <option value="confused">${t('sandbox.personaConfused')}</option>
                    <option value="rushed">${t('sandbox.personaRushed')}</option>
                    <option value="detailed">${t('sandbox.personaDetailed')}</option>
                </select>
                <button id="sandboxAutoBtn" class="${tw.btnPurple} ${tw.btnSm}" title="${t('sandbox.autoCustomerHint')}" onclick="window._pages.sandbox.autoCustomer()">${t('sandbox.autoCustomer')}</button>
                <button id="sandboxSendBtn" class="${tw.btnPrimary}" onclick="window._pages.sandbox.sendMessage()">${t('sandbox.sendMessage')}</button>
            </div>` : ''}
        </div>`;

    // Restore input value
    const newInput = document.getElementById('sandboxInput');
    if (newInput && prevInput) newInput.value = prevInput;

    // Scroll to bottom
    const msgArea = document.getElementById('sandboxMessages');
    if (msgArea) msgArea.scrollTop = msgArea.scrollHeight;
}

function _getTurnGroupIds() {
    const ids = new Set();
    for (const g of _turnGroups) {
        for (const tid of g.turn_ids) ids.add(tid);
    }
    return ids;
}

function _getGroupForTurn(turnId) {
    return _turnGroups.find(g => g.turn_ids.includes(turnId));
}

function renderTurn(turn) {
    const isCustomer = turn.speaker === 'customer';
    const speakerClass = isCustomer ? tw.speakerCustomer : tw.speakerBot;
    const speakerLabel = isCustomer ? t('sandbox.customer') : t('sandbox.agent');

    // Group membership styling
    const group = _getGroupForTurn(turn.id);
    let bgClass;
    if (group) {
        bgClass = group.pattern_type === 'positive'
            ? 'bg-emerald-100 dark:bg-emerald-950/40 border-l-4 border-emerald-500'
            : 'bg-red-100 dark:bg-red-950/40 border-l-4 border-red-500';
    } else {
        bgClass = isCustomer
            ? 'bg-blue-50 dark:bg-blue-950/30'
            : 'bg-emerald-50 dark:bg-emerald-950/30';
    }

    // Checkbox for marking mode
    let checkboxHtml = '';
    if (_markingMode) {
        const checked = _selectedTurnIds.has(turn.id) ? 'checked' : '';
        checkboxHtml = `<input type="checkbox" ${checked} onchange="window._pages.sandbox.toggleTurnSelect('${turn.id}')" class="mr-2 mt-1 cursor-pointer">`;
    }

    let toolCallsHtml = '';
    if (turn.tool_calls && turn.tool_calls.length > 0) {
        const rows = turn.tool_calls.map(tc => {
            const argsJson = JSON.stringify(tc.tool_args);
            const resultJson = JSON.stringify(tc.tool_result);
            const argsCell = argsJson.length > 60
                ? `<details><summary class="text-xs cursor-pointer">${escapeHtml(argsJson.substring(0, 60))}...</summary><pre class="text-xs bg-neutral-100 dark:bg-neutral-800 p-2 rounded mt-1 max-h-48 overflow-auto whitespace-pre-wrap">${escapeHtml(JSON.stringify(tc.tool_args, null, 2))}</pre></details>`
                : `<code class="text-xs break-all">${escapeHtml(argsJson)}</code>`;
            const resultCell = resultJson.length > 60
                ? `<details><summary class="text-xs cursor-pointer">${escapeHtml(resultJson.substring(0, 60))}...</summary><pre class="text-xs bg-neutral-100 dark:bg-neutral-800 p-2 rounded mt-1 max-h-48 overflow-auto whitespace-pre-wrap">${escapeHtml(JSON.stringify(tc.tool_result, null, 2))}</pre></details>`
                : `<code class="text-xs break-all">${escapeHtml(resultJson)}</code>`;
            return `
            <tr class="${tw.trHover}">
                <td class="${tw.td}"><span class="${tw.badgeBlue}">${escapeHtml(tc.tool_name)}</span></td>
                <td class="${tw.td}">${argsCell}</td>
                <td class="${tw.td}">${resultCell}</td>
                <td class="${tw.td}">${tc.duration_ms != null ? tc.duration_ms + 'ms' : '-'}</td>
                <td class="${tw.td}">${tc.is_mock ? `<span class="${tw.badgeYellow}">${t('sandbox.mock')}</span>` : `<span class="${tw.badgeGreen}">${t('sandbox.live')}</span>`}</td>
            </tr>`;
        }).join('');

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
        let ratingCommentHtml = '';
        if (turn.id === _ratingTurnId) {
            ratingCommentHtml = `
                <div class="flex items-center gap-2 mt-1">
                    <input type="text" id="ratingCommentInput" placeholder="${t('sandbox.ratingCommentPlaceholder')}" class="${tw.formInput} text-xs flex-1"
                        onkeydown="if(event.key==='Enter'){event.preventDefault();window._pages.sandbox.saveRating();}">
                    <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.sandbox.saveRating()">OK</button>
                    <button class="${tw.btnSecondary} ${tw.btnSm}" onclick="window._pages.sandbox.cancelRating()">&times;</button>
                </div>`;
        } else if (turn.rating_comment) {
            ratingCommentHtml = `<span class="${tw.mutedText} ml-2">${escapeHtml(turn.rating_comment)}</span>`;
        }
        ratingHtml = `<div class="mt-1 flex items-center gap-1 flex-wrap">${stars}${ratingCommentHtml}</div>`;
        branchHtml = `<button class="${tw.btnSecondary} ${tw.btnSm} mt-1" onclick="window._pages.sandbox.branchFrom('${turn.id}', ${turn.turn_number})">${t('sandbox.branch')}</button>`;
    }

    // Group badge
    let groupBadge = '';
    if (group) {
        const icon = group.pattern_type === 'positive' ? '&#9989;' : '&#10060;';
        groupBadge = `<span class="text-xs ml-2">${icon} ${escapeHtml(group.intent_label)}</span>`;
    }

    const alignClass = isCustomer ? 'ml-auto max-w-[80%]' : 'mr-auto max-w-[80%]';

    return `
        <div class="${bgClass} ${alignClass} rounded-lg px-3 py-2">
            <div class="flex items-start gap-1">
                ${checkboxHtml}
                <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-2">
                        <span class="${speakerClass}">${speakerLabel}</span>
                        <span class="${tw.mutedText}">${formatDate(turn.created_at)}</span>
                        ${groupBadge}
                    </div>
                    <div class="${tw.turnText}">${escapeHtml(turn.content)}</div>
                    ${toolCallsHtml}
                    ${metricsHtml}
                    <div class="flex items-center gap-2 flex-wrap">
                        ${ratingHtml}
                        ${branchHtml}
                    </div>
                </div>
            </div>
        </div>`;
}

// ═══════════════════════════════════════════════════════════
//  Marking Mode & Turn Groups
// ═══════════════════════════════════════════════════════════

function toggleMarking() {
    _markingMode = !_markingMode;
    _selectedTurnIds.clear();
    renderConversationView();
}

function toggleTurnSelect(turnId) {
    if (_selectedTurnIds.has(turnId)) _selectedTurnIds.delete(turnId);
    else _selectedTurnIds.add(turnId);
    renderConversationView();
}

function showGroupModal() {
    if (_selectedTurnIds.size < 1) return;
    document.getElementById('sandboxGroupIntent').value = '';
    document.getElementById('sandboxGroupType').value = 'positive';
    document.getElementById('sandboxGroupRating').value = '';
    document.getElementById('sandboxGroupComment').value = '';
    document.getElementById('sandboxGroupCorrection').value = '';
    document.getElementById('sandboxGroupTags').value = '';
    document.getElementById('sandboxGroupCorrectionWrap').style.display = 'none';
    document.getElementById('sandboxGroupModal').classList.add('show');
}

async function submitGroup() {
    const intentLabel = document.getElementById('sandboxGroupIntent').value.trim();
    if (!intentLabel) { showToast(t('sandbox.intentRequired'), 'error'); return; }

    const patternType = document.getElementById('sandboxGroupType').value;
    const ratingVal = document.getElementById('sandboxGroupRating').value;
    const rating = ratingVal ? parseInt(ratingVal) : null;
    const ratingComment = document.getElementById('sandboxGroupComment').value.trim() || null;
    const correction = document.getElementById('sandboxGroupCorrection').value.trim() || null;
    const tagsRaw = document.getElementById('sandboxGroupTags').value.trim();
    const tags = tagsRaw ? tagsRaw.split(',').map(t => t.trim()).filter(Boolean) : [];

    try {
        await api(`/admin/sandbox/conversations/${_currentConvId}/turn-groups`, {
            method: 'POST',
            body: JSON.stringify({
                turn_ids: Array.from(_selectedTurnIds),
                intent_label: intentLabel,
                pattern_type: patternType,
                rating,
                rating_comment: ratingComment,
                correction,
                tags,
            }),
        });
        closeModal('sandboxGroupModal');
        showToast(t('sandbox.groupCreated'));
        _selectedTurnIds.clear();
        _markingMode = false;
        await loadConversation(_currentConvId);
    } catch (e) {
        showToast(t('sandbox.loadFailed', { error: e.message }), 'error');
    }
}

async function deleteGroup(groupId) {
    if (!confirm(t('sandbox.deleteConfirm', { title: 'group' }))) return;
    const btn = document.querySelector(`[onclick*="deleteGroup('${groupId}')"]`);
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-sm"></span>'; }
    try {
        await api(`/admin/sandbox/turn-groups/${groupId}`, { method: 'DELETE' });
        showToast(t('sandbox.groupDeleted'));
        await loadConversation(_currentConvId);
    } catch (e) {
        showToast(t('sandbox.loadFailed', { error: e.message }), 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '\u00d7'; }
    }
}

function showExportModal(groupId) {
    document.getElementById('sandboxExportGroupId').value = groupId;
    document.getElementById('sandboxExportGuidance').value = '';
    document.getElementById('sandboxExportModal').classList.add('show');
}

async function submitExport() {
    const groupId = document.getElementById('sandboxExportGroupId').value;
    const guidanceNote = document.getElementById('sandboxExportGuidance').value.trim();
    if (!guidanceNote) { showToast(t('sandbox.guidanceRequired'), 'error'); return; }

    try {
        await api(`/admin/sandbox/turn-groups/${groupId}/export`, {
            method: 'POST',
            body: JSON.stringify({ guidance_note: guidanceNote }),
        });
        closeModal('sandboxExportModal');
        showToast(t('sandbox.exportSuccess'));
        await loadConversation(_currentConvId);
    } catch (e) {
        showToast(t('sandbox.loadFailed', { error: e.message }), 'error');
    }
}

async function sendMessage() {
    if (_sending) return;
    const input = document.getElementById('sandboxInput');
    const msg = input?.value?.trim();
    if (!msg || !_currentConvId) return;

    _sending = true;
    const btn = document.getElementById('sandboxSendBtn');
    if (btn) { btn.disabled = true; btn.textContent = t('sandbox.sending'); }
    const savedMsg = msg;
    input.value = '';

    try {
        await api(`/admin/sandbox/conversations/${_currentConvId}/send`, {
            method: 'POST',
            body: JSON.stringify({ message: msg }),
        });
        await loadConversation(_currentConvId);
    } catch (e) {
        showToast(t('sandbox.sendFailed', { error: e.message }), 'error');
        const inp = document.getElementById('sandboxInput');
        if (inp) inp.value = savedMsg;
    } finally {
        _sending = false;
        const curBtn = document.getElementById('sandboxSendBtn');
        if (curBtn) { curBtn.disabled = false; curBtn.textContent = t('sandbox.sendMessage'); }
        // Refocus input
        const newInput = document.getElementById('sandboxInput');
        if (newInput) newInput.focus();
    }
}

function rateTurn(turnId, stars) {
    _ratingTurnId = turnId;
    _pendingRating = stars;
    renderConversationView();
    // Focus the comment input after render
    setTimeout(() => {
        const inp = document.getElementById('ratingCommentInput');
        if (inp) inp.focus();
    }, 0);
}

async function saveRating() {
    if (!_ratingTurnId || !_pendingRating) return;
    const commentInput = document.getElementById('ratingCommentInput');
    const comment = commentInput?.value?.trim() || '';

    try {
        const body = { rating: _pendingRating };
        if (comment) body.comment = comment;
        await api(`/admin/sandbox/turns/${_ratingTurnId}/rate`, {
            method: 'PATCH',
            body: JSON.stringify(body),
        });
        showToast(t('sandbox.ratingSaved'));
        _ratingTurnId = null;
        _pendingRating = null;
        if (_currentConvId) await loadConversation(_currentConvId);
    } catch (e) {
        showToast(t('sandbox.ratingFailed', { error: e.message }), 'error');
    }
}

function cancelRating() {
    _ratingTurnId = null;
    _pendingRating = null;
    renderConversationView();
}

function branchFrom(turnId, turnNumber) {
    _branchTurnId = turnId;
    _branchTurnNumber = turnNumber;
    const titleEl = document.getElementById('sandboxBranchTitle');
    if (titleEl) titleEl.textContent = t('sandbox.branchFromTurn', { turn: turnNumber });
    const input = document.getElementById('sandboxBranchInput');
    if (input) input.value = '';
    document.getElementById('sandboxBranchModal').classList.add('show');
    setTimeout(() => { if (input) input.focus(); }, 100);
}

async function submitBranch() {
    const input = document.getElementById('sandboxBranchInput');
    const msg = input?.value?.trim();
    if (!msg || !_branchTurnId) return;

    _sending = true;
    try {
        await api(`/admin/sandbox/conversations/${_currentConvId}/send`, {
            method: 'POST',
            body: JSON.stringify({ message: msg, parent_turn_id: _branchTurnId }),
        });
        closeModal('sandboxBranchModal');
        await loadConversation(_currentConvId);
    } catch (e) {
        showToast(t('sandbox.sendFailed', { error: e.message }), 'error');
    } finally {
        _sending = false;
    }
}

async function autoCustomer() {
    const btn = document.getElementById('sandboxAutoBtn');
    if (btn) { btn.disabled = true; btn.textContent = t('sandbox.generating'); }

    try {
        const persona = document.getElementById('sandboxPersonaSelect')?.value || 'neutral';
        const data = await api(`/admin/sandbox/conversations/${_currentConvId}/auto-customer`, {
            method: 'POST',
            body: JSON.stringify({ persona }),
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
    const btn = document.querySelector(`[onclick*="archiveConversation()"]`);
    if (btn) { btn.disabled = true; btn.textContent = '...'; }
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
        loadSidebar();
    } catch (e) {
        showToast(t('sandbox.loadFailed', { error: e.message }), 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = t('sandbox.archiveConversation'); }
    }
}

// ═══════════════════════════════════════════════════════════
//  New Conversation Modal
// ═══════════════════════════════════════════════════════════
async function showNewConvModal() {
    document.getElementById('sandboxConvTitle').value = '';
    document.getElementById('sandboxConvTags').value = '';
    document.getElementById('sandboxConvToolMode').value = 'live';
    document.getElementById('sandboxConvScenario').value = '';

    // Load starters into quick start section
    try {
        const sdata = await api('/admin/sandbox/scenario-starters?limit=20');
        const starters = sdata.items || [];
        _starterCache = starters;
        const section = document.getElementById('sandboxStartersSection');
        const cards = document.getElementById('sandboxStarterCards');
        if (starters.length > 0) {
            section.style.display = '';
            cards.innerHTML = starters.map((s, idx) => {
                const scenarioBadge = s.scenario_type
                    ? `<span class="text-[10px] px-1 rounded bg-neutral-200 dark:bg-neutral-700 text-neutral-600 dark:text-neutral-300">${escapeHtml(s.scenario_type)}</span>`
                    : '';
                return `
                    <div class="p-2 rounded-md border border-neutral-200 dark:border-neutral-700 hover:border-blue-400 dark:hover:border-blue-600 cursor-pointer transition-colors bg-neutral-50 dark:bg-neutral-800"
                         onclick="window._pages.sandbox.useStarterByIndex(${idx})">
                        <div class="text-xs font-medium text-neutral-900 dark:text-neutral-100 mb-1">${escapeHtml(s.title)}</div>
                        <div class="text-[10px] text-neutral-500 dark:text-neutral-400 truncate mb-1">${escapeHtml(s.first_message)}</div>
                        ${scenarioBadge}
                    </div>`;
            }).join('');
        } else {
            section.style.display = 'none';
        }
    } catch {
        const section = document.getElementById('sandboxStartersSection');
        if (section) section.style.display = 'none';
    }

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

    // Load models for select
    try {
        const data = await api('/admin/sandbox/models');
        const select = document.getElementById('sandboxConvModel');
        select.innerHTML = `<option value="">${t('sandbox.modelDefault')}</option>`;
        for (const m of data.models || []) {
            const speedLabel = m.speed === 'fast' ? ' ⚡' : '';
            const routerLabel = m.source === 'router' ? ` [${t('sandbox.routerModel')}]` : '';
            select.innerHTML += `<option value="${m.id}">${escapeHtml(m.label)}${speedLabel}${routerLabel}</option>`;
        }
    } catch { /* ignore */ }

    document.getElementById('sandboxNewConvModal').classList.add('show');
}

async function createConversation() {
    const title = document.getElementById('sandboxConvTitle').value.trim();
    if (!title) { showToast(t('sandbox.titleRequired'), 'error'); return; }

    const promptId = document.getElementById('sandboxConvPrompt').value || null;
    const modelId = document.getElementById('sandboxConvModel').value || null;
    const toolMode = document.getElementById('sandboxConvToolMode').value;
    const scenarioType = document.getElementById('sandboxConvScenario').value || null;
    const tagsRaw = document.getElementById('sandboxConvTags').value.trim();
    const tags = tagsRaw ? tagsRaw.split(',').map(t => t.trim()).filter(Boolean) : [];

    try {
        const body = { title, tool_mode: toolMode, tags };
        if (promptId) body.prompt_version_id = promptId;
        if (modelId) body.model = modelId;
        if (scenarioType) body.scenario_type = scenarioType;

        const result = await api('/admin/sandbox/conversations', {
            method: 'POST',
            body: JSON.stringify(body),
        });
        closeModal('sandboxNewConvModal');
        showToast(t('sandbox.created'));
        _currentConvId = result.item.id;
        loadSidebar();
        showTab('chat');
    } catch (e) {
        showToast(t('sandbox.loadFailed', { error: e.message }), 'error');
    }
}

// ═══════════════════════════════════════════════════════════
//  Sidebar — conversation list
// ═══════════════════════════════════════════════════════════
async function loadSidebar() {
    const container = document.getElementById('sandboxSidebarList');
    if (!container) return;
    container.innerHTML = `<div class="flex justify-center py-6"><div class="spinner"></div></div>`;

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
            container.innerHTML = `<div class="p-4 text-center ${tw.mutedText}">${t('sandbox.noConversations')}</div>`;
            return;
        }

        container.innerHTML = items.map(item => {
            const isActive = item.id === _currentConvId;
            const activeCls = isActive
                ? 'bg-blue-50 dark:bg-blue-950/30 border-l-2 border-blue-500'
                : 'border-l-2 border-transparent hover:bg-neutral-50 dark:hover:bg-neutral-800';
            const toolBadge = item.tool_mode === 'mock'
                ? `<span class="text-[10px] px-1 rounded bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300">${t('sandbox.mock')}</span>`
                : `<span class="text-[10px] px-1 rounded bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300">${t('sandbox.live')}</span>`;
            const modelLabel = item.model
                ? item.model.replace('claude-', '').replace(/-\d+$/, '')
                : '';
            const dateStr = item.updated_at ? new Date(item.updated_at).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }) : '';

            const baselineBadge = item.is_baseline
                ? '<span class="text-[10px] px-1 rounded bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 font-semibold">B</span>'
                : '';

            return `
                <div class="px-3 py-2 cursor-pointer ${activeCls} transition-colors" onclick="window._pages.sandbox.openConversation('${item.id}')">
                    <div class="flex items-center gap-1">
                        <span class="text-xs font-medium text-neutral-900 dark:text-neutral-100 truncate flex-1">${escapeHtml(item.title)}</span>
                        ${baselineBadge}
                        <button class="text-neutral-400 hover:text-red-500 text-xs leading-none flex-shrink-0" onclick="event.stopPropagation();window._pages.sandbox.deleteConversation('${item.id}', '${escapeHtml(item.title).replace(/'/g, "\\'")}')" title="${t('common.delete')}">&times;</button>
                    </div>
                    <div class="flex items-center gap-1.5 mt-0.5 text-[10px] text-neutral-500 dark:text-neutral-400">
                        <span>${dateStr}</span>
                        <span>&middot;</span>
                        <span>${item.turns_count || 0} ${t('sandbox.turnsCount').toLowerCase()}</span>
                        ${toolBadge}
                        ${modelLabel ? `<span class="text-neutral-400">${escapeHtml(modelLabel)}</span>` : ''}
                    </div>
                </div>`;
        }).join('');
    } catch (e) {
        container.innerHTML = `<div class="p-4 text-center ${tw.mutedText}">${t('sandbox.loadFailed', { error: escapeHtml(e.message) })}</div>`;
    }
}

function openConversation(convId) {
    _currentConvId = convId;
    if (_activeTab !== 'chat') showTab('chat');
    else renderChat();
    loadSidebar();
}

async function deleteConversation(convId, title) {
    if (!confirm(t('sandbox.deleteConfirm', { title }))) return;
    const btn = document.querySelector(`[onclick*="deleteConversation('${convId}'"]`);
    if (btn) { btn.disabled = true; btn.innerHTML = '...'; }
    try {
        await api(`/admin/sandbox/conversations/${convId}`, { method: 'DELETE' });
        showToast(t('sandbox.deleted'));
        if (_currentConvId === convId) {
            _currentConvId = null;
            _currentConv = null;
            _turns = [];
            renderChat();
        }
        loadSidebar();
    } catch (e) {
        showToast(t('sandbox.loadFailed', { error: e.message }), 'error');
        if (btn) { btn.disabled = false; btn.innerHTML = '&times;'; }
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
                <th class="${tw.th}">${t('sandbox.error')}</th>
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
                ${item.new_conversation_id ? `<button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.sandbox.openConversation('${item.new_conversation_id}');window._pages.sandbox.showTab('chat')">${t('sandbox.openNewConversation')}</button>` : ''}
            </div>
            ${diffsHtml}`;
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('sandbox.loadFailed', { error: escapeHtml(e.message) })}</div>`;
    }
}

async function replayConversation(convId) {
    _replayConvId = convId;
    document.getElementById('sandboxReplayConvId').value = convId;

    const select = document.getElementById('sandboxReplayPrompt');
    select.innerHTML = '';

    try {
        const data = await api('/prompts');
        const versions = data.versions || data.items || [];
        if (versions.length === 0) {
            showToast(t('sandbox.regressionFailed', { error: 'No prompt versions available' }), 'error');
            return;
        }
        for (const v of versions) {
            const activeLabel = v.is_active ? ' *' : '';
            select.innerHTML += `<option value="${v.id}">${escapeHtml(v.name)}${activeLabel}</option>`;
        }
    } catch {
        showToast(t('sandbox.regressionFailed', { error: 'Failed to load versions' }), 'error');
        return;
    }

    document.getElementById('sandboxReplayModal').classList.add('show');
}

async function submitReplay() {
    const select = document.getElementById('sandboxReplayPrompt');
    const convId = document.getElementById('sandboxReplayConvId').value;
    const versionId = select?.value;
    if (!versionId || !convId) return;

    const btn = document.querySelector('#sandboxReplayModal button[onclick*="submitReplay"]');
    if (btn) { btn.disabled = true; btn.textContent = '...'; }

    try {
        await api(`/admin/sandbox/conversations/${convId}/replay`, {
            method: 'POST',
            body: JSON.stringify({ new_prompt_version_id: versionId }),
        });
        closeModal('sandboxReplayModal');
        showToast(t('sandbox.regressionStarted'));
        showTab('regression');
    } catch (e) {
        showToast(t('sandbox.regressionFailed', { error: e.message }), 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = t('sandbox.submitReplay'); }
    }
}

async function toggleBaseline(convId, isBaseline) {
    try {
        await api(`/admin/sandbox/conversations/${convId}`, {
            method: 'PATCH',
            body: JSON.stringify({ is_baseline: !isBaseline }),
        });
        if (_currentConvId === convId) await loadConversation(convId);
        else loadSidebar();
    } catch (e) {
        showToast(t('sandbox.loadFailed', { error: e.message }), 'error');
    }
}

// ═══════════════════════════════════════════════════════════
//  Patterns Tab (Phase 4)
// ═══════════════════════════════════════════════════════════

async function loadPatterns() {
    const container = document.getElementById('sandboxPatternsContainer');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    try {
        const data = await api('/admin/sandbox/patterns?limit=100');
        const items = data.items || [];

        if (items.length === 0) {
            container.innerHTML = `<div class="${tw.emptyState}">${t('sandbox.noPatterns')}</div>`;
            return;
        }

        const rows = items.map(item => {
            const typeBadge = item.pattern_type === 'positive'
                ? `<span class="${tw.badgeGreen}">positive</span>`
                : `<span class="${tw.badgeRed}">negative</span>`;
            const activeBadge = item.is_active
                ? `<span class="${tw.badgeGreen}">${t('sandbox.patternActive')}</span>`
                : `<span class="${tw.badgeGray}">${t('sandbox.patternInactive')}</span>`;
            const tagsHtml = (item.tags || []).map(tg => `<span class="${tw.badge}">${escapeHtml(tg)}</span>`).join(' ');
            const rating = item.rating ? '&#9733;'.repeat(item.rating) : '-';

            return `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}">${escapeHtml(item.intent_label)}</td>
                    <td class="${tw.td}">${typeBadge}</td>
                    <td class="${tw.td}"><div class="max-w-xs truncate text-xs">${escapeHtml(item.guidance_note)}</div></td>
                    <td class="${tw.td}"><div class="max-w-xs truncate text-xs">${escapeHtml(item.customer_messages?.substring(0, 80) || '-')}</div></td>
                    <td class="${tw.td}"><span class="text-amber-400">${rating}</span></td>
                    <td class="${tw.td}">${item.times_used}</td>
                    <td class="${tw.td}">${activeBadge}</td>
                    <td class="${tw.td}">${tagsHtml}</td>
                    <td class="${tw.td}">
                        <button class="${tw.btnSecondary} ${tw.btnSm}" onclick="window._pages.sandbox.togglePatternActive('${item.id}', ${item.is_active})">${item.is_active ? t('common.deactivate') : t('common.activate')}</button>
                        <button class="${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.sandbox.deletePattern('${item.id}')">&times;</button>
                    </td>
                </tr>`;
        }).join('');

        container.innerHTML = `
            <div class="mb-4 flex flex-wrap items-center gap-2">
                <input type="text" id="patternSearchQuery" placeholder="${t('sandbox.testSearchHint')}"
                    class="${tw.formInput} flex-1"
                    onkeydown="if(event.key==='Enter')window._pages.sandbox.testSearch()">
                <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.sandbox.testSearch()">${t('sandbox.testSearch')}</button>
            </div>
            <div id="patternSearchResults" class="mb-4"></div>
            <div class="overflow-x-auto"><table class="${tw.table}"><thead><tr>
                <th class="${tw.th}">${t('sandbox.intentLabel')}</th>
                <th class="${tw.th}">${t('sandbox.patternType')}</th>
                <th class="${tw.th}">${t('sandbox.guidanceNote')}</th>
                <th class="${tw.th}">${t('sandbox.customerMessages')}</th>
                <th class="${tw.th}">${t('sandbox.avgRating')}</th>
                <th class="${tw.th}">${t('sandbox.timesUsed')}</th>
                <th class="${tw.th}">${t('sandbox.status')}</th>
                <th class="${tw.th}">${t('sandbox.tags')}</th>
                <th class="${tw.th}"></th>
            </tr></thead><tbody>${rows}</tbody></table></div>`;
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('sandbox.loadFailed', { error: escapeHtml(e.message) })}</div>`;
    }
}

async function togglePatternActive(patternId, currentActive) {
    try {
        await api(`/admin/sandbox/patterns/${patternId}`, {
            method: 'PATCH',
            body: JSON.stringify({ is_active: !currentActive }),
        });
        loadPatterns();
    } catch (e) {
        showToast(t('sandbox.loadFailed', { error: e.message }), 'error');
    }
}

async function deletePattern(patternId) {
    if (!confirm(t('sandbox.deleteConfirm', { title: 'pattern' }))) return;
    const btn = document.querySelector(`[onclick*="deletePattern('${patternId}')"]`);
    if (btn) { btn.disabled = true; btn.innerHTML = '...'; }
    try {
        await api(`/admin/sandbox/patterns/${patternId}`, { method: 'DELETE' });
        loadPatterns();
    } catch (e) {
        showToast(t('sandbox.loadFailed', { error: e.message }), 'error');
        if (btn) { btn.disabled = false; btn.innerHTML = '&times;'; }
    }
}

async function testSearch() {
    const query = document.getElementById('patternSearchQuery')?.value?.trim();
    if (!query) return;

    const container = document.getElementById('patternSearchResults');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    try {
        const params = new URLSearchParams({ query, top_k: '5', min_similarity: '0.5' });
        const data = await api(`/admin/sandbox/patterns/search-test?${params}`, { method: 'POST' });
        const results = data.results || [];

        if (results.length === 0) {
            container.innerHTML = `<div class="${tw.mutedText} p-2">${t('sandbox.noPatterns')}</div>`;
            return;
        }

        const rows = results.map(r => {
            const sim = (parseFloat(r.similarity) * 100).toFixed(1);
            const typeBadge = r.pattern_type === 'positive'
                ? `<span class="${tw.badgeGreen}">+</span>`
                : `<span class="${tw.badgeRed}">&minus;</span>`;
            return `
                <div class="flex items-center gap-2 p-2 rounded bg-neutral-50 dark:bg-neutral-800 mb-1">
                    ${typeBadge}
                    <span class="text-sm font-medium">${escapeHtml(r.intent_label)}</span>
                    <span class="${tw.badgeBlue}">${sim}%</span>
                    <span class="${tw.mutedText} text-xs truncate flex-1">${escapeHtml(r.guidance_note)}</span>
                </div>`;
        }).join('');

        container.innerHTML = `
            <div class="text-xs font-semibold text-neutral-500 mb-1">${t('sandbox.similarity')} (${results.length})</div>
            ${rows}`;
    } catch (e) {
        container.innerHTML = `<div class="${tw.mutedText} p-2">${escapeHtml(e.message)}</div>`;
    }
}

// ═══════════════════════════════════════════════════════════
//  Phrases Tab — read-only agent phrase reference
// ═══════════════════════════════════════════════════════════

async function loadPhrases() {
    const container = document.getElementById('sandboxPhrasesContainer');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    try {
        const data = await api('/admin/sandbox/agent-phrases');
        const fixed = data.fixed || [];
        const pools = data.wait_pools || [];
        const dbTemplates = data.db_templates || {};
        const hasDbTemplates = Object.keys(dbTemplates).length > 0;

        // ── DB templates section (active overrides) ─────────────
        let dbSection = '';
        if (hasDbTemplates) {
            const dbRows = Object.entries(dbTemplates).map(([key, variants]) => {
                const activeCount = variants.filter(v => v.is_active).length;
                const variantItems = variants.map((v, idx) => `
                    <div class="py-1.5 ${idx > 0 ? 'border-t border-neutral-100 dark:border-neutral-800' : ''}">
                        <div class="flex items-center gap-2">
                            <span class="${tw.mutedText} text-xs">#${v.variant_number}</span>
                            ${!v.is_active ? `<span class="text-[10px] px-1.5 py-0.5 rounded bg-neutral-200 dark:bg-neutral-700 text-neutral-500 dark:text-neutral-400">${t('sandbox.phrasesInactive')}</span>` : ''}
                            <span class="text-xs text-neutral-600 dark:text-neutral-400">${escapeHtml(v.title)}</span>
                        </div>
                        <div class="font-mono text-xs mt-0.5 ${!v.is_active ? 'opacity-40 line-through' : ''}">${escapeHtml(v.content)}</div>
                    </div>`).join('');

                return `
                    <tr class="${tw.trHover}">
                        <td class="${tw.td}">
                            <span class="${tw.badgePurple}">${escapeHtml(key)}</span>
                            <div class="${tw.mutedText} text-xs mt-1">${t('sandbox.phrasesDbVariants', { active: activeCount, total: variants.length })}</div>
                        </td>
                        <td class="${tw.td}">
                            <details>
                                <summary class="cursor-pointer text-xs text-blue-600 dark:text-blue-400">${escapeHtml(variants.find(v => v.is_active)?.content || variants[0].content)}</summary>
                                <div class="mt-1 pl-2 border-l-2 border-purple-200 dark:border-purple-800">${variantItems}</div>
                            </details>
                        </td>
                    </tr>`;
            }).join('');

            dbSection = `
                <div class="mb-6 p-3 bg-purple-50 dark:bg-purple-950/20 rounded-md border border-purple-200 dark:border-purple-800">
                    <h2 class="text-sm font-semibold text-purple-900 dark:text-purple-100 mb-1">${t('sandbox.phrasesDbTitle')}</h2>
                    <p class="text-xs text-purple-700 dark:text-purple-300 mb-3">${t('sandbox.phrasesDbDescription')}</p>
                    <div class="overflow-x-auto">
                        <table class="${tw.table}">
                            <thead><tr>
                                <th class="${tw.th}" style="width:200px">${t('sandbox.phrasesKey')}</th>
                                <th class="${tw.th}">${t('sandbox.phrasesText')}</th>
                            </tr></thead>
                            <tbody>${dbRows}</tbody>
                        </table>
                    </div>
                </div>`;
        }

        // ── Fixed phrases table (hardcoded fallbacks) ───────────
        const fixedRows = fixed.map(item => {
            const hasOverride = !!dbTemplates[item.key];
            return `
            <tr class="${tw.trHover} ${hasOverride ? 'opacity-50' : ''}">
                <td class="${tw.td}"><span class="${tw.badgeBlue}">${escapeHtml(item.label)}</span></td>
                <td class="${tw.td} font-mono text-xs whitespace-pre-wrap">${escapeHtml(item.text)}</td>
                <td class="${tw.td}">
                    ${hasOverride
                        ? `<span class="${tw.badgePurple}">${t('sandbox.phrasesOverridden')}</span>`
                        : `<span class="${tw.badgeGreen}">${t('sandbox.phrasesActive')}</span>`}
                </td>
            </tr>`;
        }).join('');

        // Wait pool rows — each pool is expandable with all variants
        const poolRows = pools.map(pool => {
            const variants = pool.phrases.map((phrase, idx) => `
                <div class="py-1 ${idx > 0 ? 'border-t border-neutral-100 dark:border-neutral-800' : ''}">
                    <span class="${tw.mutedText} text-xs mr-2">#${idx + 1}</span>
                    <span class="font-mono text-xs">${escapeHtml(phrase)}</span>
                </div>`).join('');

            return `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}">
                        <span class="${tw.badgeYellow}">${escapeHtml(pool.label)}</span>
                        <div class="${tw.mutedText} text-xs mt-1">${t('sandbox.phrasesPoolLabel', { count: pool.phrases.length })}</div>
                    </td>
                    <td class="${tw.td}">
                        <details>
                            <summary class="cursor-pointer text-xs text-blue-600 dark:text-blue-400">${escapeHtml(pool.phrases[0])}</summary>
                            <div class="mt-1 pl-2 border-l-2 border-blue-200 dark:border-blue-800">${variants}</div>
                        </details>
                    </td>
                    <td class="${tw.td}"><span class="${tw.badgeGreen}">${t('sandbox.phrasesActive')}</span></td>
                </tr>`;
        }).join('');

        container.innerHTML = `
            <div class="mb-4">
                <h2 class="text-sm font-semibold text-neutral-900 dark:text-neutral-50 mb-1">${t('sandbox.phrasesTitle')}</h2>
                <p class="${tw.mutedText} text-xs mb-4">${t('sandbox.phrasesDescription')}</p>
            </div>

            ${dbSection}

            <h3 class="text-xs font-semibold text-neutral-700 dark:text-neutral-300 mb-2">${t('sandbox.phrasesCatFixed')}</h3>
            <div class="overflow-x-auto mb-6">
                <table class="${tw.table}">
                    <thead><tr>
                        <th class="${tw.th}" style="width:200px">${t('sandbox.phrasesKey')}</th>
                        <th class="${tw.th}">${t('sandbox.phrasesText')}</th>
                        <th class="${tw.th}" style="width:140px">${t('sandbox.phrasesStatus')}</th>
                    </tr></thead>
                    <tbody>${fixedRows}</tbody>
                </table>
            </div>

            <h3 class="text-xs font-semibold text-neutral-700 dark:text-neutral-300 mb-2">${t('sandbox.phrasesCatWait')}</h3>
            <div class="overflow-x-auto mb-6">
                <table class="${tw.table}">
                    <thead><tr>
                        <th class="${tw.th}" style="width:200px">${t('sandbox.phrasesKey')}</th>
                        <th class="${tw.th}">${t('sandbox.phrasesText')}</th>
                        <th class="${tw.th}" style="width:140px">${t('sandbox.phrasesStatus')}</th>
                    </tr></thead>
                    <tbody>${poolRows}</tbody>
                </table>
            </div>

            <div class="p-3 bg-amber-50 dark:bg-amber-950/20 rounded-md border border-amber-200 dark:border-amber-800">
                <p class="text-xs text-amber-700 dark:text-amber-300">${t('sandbox.phrasesLlmNote')}</p>
            </div>`;
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('sandbox.phrasesLoadFailed', { error: escapeHtml(e.message) })}</div>`;
    }
}

// ═══════════════════════════════════════════════════════════
//  Scenario Starters Tab (Phase 5)
// ═══════════════════════════════════════════════════════════

async function loadStarters() {
    const container = document.getElementById('sandboxStartersContainer');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    try {
        const data = await api('/admin/sandbox/scenario-starters?limit=100');
        const items = data.items || [];

        if (items.length === 0) {
            container.innerHTML = `
                <div class="flex items-center justify-between mb-4">
                    <h2 class="text-sm font-semibold text-neutral-900 dark:text-neutral-50">${t('sandbox.starters')}</h2>
                    <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.sandbox.showStarterModal()">${t('sandbox.newStarter')}</button>
                </div>
                <div class="${tw.emptyState}">${t('sandbox.noStarters')}</div>`;
            return;
        }

        const rows = items.map(item => {
            const scenarioBadge = item.scenario_type
                ? `<span class="${tw.badge}">${escapeHtml(item.scenario_type)}</span>`
                : '';
            const personaBadge = item.persona
                ? `<span class="${tw.badgeBlue}">${escapeHtml(item.persona)}</span>`
                : '';
            return `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}"><span class="font-medium">${escapeHtml(item.title)}</span></td>
                    <td class="${tw.td}"><div class="max-w-xs truncate text-xs">${escapeHtml(item.first_message)}</div></td>
                    <td class="${tw.td}">${scenarioBadge}</td>
                    <td class="${tw.td}">${personaBadge}</td>
                    <td class="${tw.td}"><div class="max-w-xs truncate text-xs">${escapeHtml(item.description || '')}</div></td>
                    <td class="${tw.td}">
                        <div class="flex gap-1">
                            <button class="${tw.btnSecondary} ${tw.btnSm}" onclick="window._pages.sandbox.showStarterModal('${item.id}')">${t('common.edit')}</button>
                            <button class="${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.sandbox.deleteStarter('${item.id}', '${escapeHtml(item.title).replace(/'/g, "\\'")}')">&times;</button>
                        </div>
                    </td>
                </tr>`;
        }).join('');

        container.innerHTML = `
            <div class="flex items-center justify-between mb-4">
                <h2 class="text-sm font-semibold text-neutral-900 dark:text-neutral-50">${t('sandbox.starters')} (${items.length})</h2>
                <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.sandbox.showStarterModal()">${t('sandbox.newStarter')}</button>
            </div>
            <div class="overflow-x-auto"><table class="${tw.table}"><thead><tr>
                <th class="${tw.th}">${t('sandbox.starterTitle')}</th>
                <th class="${tw.th}">${t('sandbox.starterFirstMessage')}</th>
                <th class="${tw.th}">${t('sandbox.scenarioType')}</th>
                <th class="${tw.th}">${t('sandbox.starterPersona')}</th>
                <th class="${tw.th}">${t('sandbox.starterDescription')}</th>
                <th class="${tw.th}"></th>
            </tr></thead><tbody>${rows}</tbody></table></div>`;
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('sandbox.loadFailed', { error: escapeHtml(e.message) })}</div>`;
    }
}

async function showStarterModal(starterId) {
    document.getElementById('sandboxStarterEditId').value = starterId || '';
    document.getElementById('sandboxStarterTitle').value = '';
    document.getElementById('sandboxStarterFirstMsg').value = '';
    document.getElementById('sandboxStarterDesc').value = '';
    document.getElementById('sandboxStarterScenario').value = '';
    document.getElementById('sandboxStarterPersona').value = 'neutral';

    const titleEl = document.getElementById('sandboxStarterModalTitle');

    if (starterId) {
        titleEl.textContent = t('sandbox.editStarter');
        try {
            const data = await api(`/admin/sandbox/scenario-starters/${starterId}`);
            const item = data.item || data;
            document.getElementById('sandboxStarterTitle').value = item.title || '';
            document.getElementById('sandboxStarterFirstMsg').value = item.first_message || '';
            document.getElementById('sandboxStarterDesc').value = item.description || '';
            document.getElementById('sandboxStarterScenario').value = item.scenario_type || '';
            document.getElementById('sandboxStarterPersona').value = item.persona || 'neutral';
        } catch { /* new starter fallback */ }
    } else {
        titleEl.textContent = t('sandbox.newStarter');
    }

    document.getElementById('sandboxStarterModal').classList.add('show');
}

async function saveStarter() {
    const editId = document.getElementById('sandboxStarterEditId').value;
    const title = document.getElementById('sandboxStarterTitle').value.trim();
    const firstMessage = document.getElementById('sandboxStarterFirstMsg').value.trim();
    if (!title || !firstMessage) { showToast(t('sandbox.starterTitleRequired'), 'error'); return; }

    const body = {
        title,
        first_message: firstMessage,
        description: document.getElementById('sandboxStarterDesc').value.trim() || null,
        scenario_type: document.getElementById('sandboxStarterScenario').value || null,
        persona: document.getElementById('sandboxStarterPersona').value || 'neutral',
    };

    try {
        if (editId) {
            await api(`/admin/sandbox/scenario-starters/${editId}`, {
                method: 'PUT',
                body: JSON.stringify(body),
            });
        } else {
            await api('/admin/sandbox/scenario-starters', {
                method: 'POST',
                body: JSON.stringify(body),
            });
        }
        closeModal('sandboxStarterModal');
        showToast(t('sandbox.starterSaved'));
        loadStarters();
    } catch (e) {
        showToast(t('sandbox.loadFailed', { error: e.message }), 'error');
    }
}

async function deleteStarter(starterId, title) {
    if (!confirm(t('sandbox.deleteStarterConfirm', { title }))) return;
    const btn = document.querySelector(`[onclick*="deleteStarter('${starterId}'"]`);
    if (btn) { btn.disabled = true; btn.innerHTML = '...'; }
    try {
        await api(`/admin/sandbox/scenario-starters/${starterId}`, { method: 'DELETE' });
        showToast(t('sandbox.starterDeleted'));
        loadStarters();
    } catch (e) {
        showToast(t('sandbox.loadFailed', { error: e.message }), 'error');
        if (btn) { btn.disabled = false; btn.innerHTML = '&times;'; }
    }
}

function useStarterByIndex(idx) {
    const starter = _starterCache[idx];
    if (starter) useStarter(starter);
}

async function useStarter(starter) {
    // Create conversation from starter and send first message
    const body = {
        title: starter.title,
        tool_mode: 'mock',
        tags: [],
    };
    if (starter.scenario_type) body.scenario_type = starter.scenario_type;

    try {
        const result = await api('/admin/sandbox/conversations', {
            method: 'POST',
            body: JSON.stringify(body),
        });
        const convId = result.item.id;
        _currentConvId = convId;

        // Send the first message
        await api(`/admin/sandbox/conversations/${convId}/send`, {
            method: 'POST',
            body: JSON.stringify({ message: starter.first_message }),
        });

        closeModal('sandboxNewConvModal');
        showToast(t('sandbox.created'));
        loadSidebar();
        showTab('chat');
    } catch (e) {
        showToast(t('sandbox.loadFailed', { error: e.message }), 'error');
    }
}

// ─── Init ────────────────────────────────────────────────────
export function init() {
    registerPageLoader('sandbox', () => {
        loadSidebar();
        showTab(_activeTab);
    });
}

window._pages = window._pages || {};
window._pages.sandbox = {
    showTab,
    loadSidebar,
    openConversation,
    showNewConvModal,
    createConversation,
    sendMessage,
    rateTurn,
    saveRating,
    cancelRating,
    archiveConversation,
    deleteConversation,
    branchFrom,
    submitBranch,
    autoCustomer,
    loadRegressionRuns,
    showRegressionDetail,
    replayConversation,
    submitReplay,
    toggleBaseline,
    // Phase 4: Turn Groups + Patterns
    toggleMarking,
    toggleTurnSelect,
    showGroupModal,
    submitGroup,
    deleteGroup,
    showExportModal,
    submitExport,
    loadPatterns,
    togglePatternActive,
    deletePattern,
    testSearch,
    // Phase 5: Agent Phrases
    loadPhrases,
    // Phase 6: Scenario Starters
    loadStarters,
    showStarterModal,
    saveStarter,
    deleteStarter,
    useStarter,
    useStarterByIndex,
};
