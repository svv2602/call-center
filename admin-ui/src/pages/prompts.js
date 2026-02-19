import { api, fetchWithAuth } from '../api.js';
import { showToast } from '../notifications.js';
import { qualityBadge, formatDate, escapeHtml, closeModal, downloadBlob } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import { makeSortable } from '../sorting.js';
import * as tw from '../tw.js';

// ─── State ───────────────────────────────────────────────────
let _promptVersionsCache = [];

// ═══════════════════════════════════════════════════════════
//  Промпты
// ═══════════════════════════════════════════════════════════
async function loadPromptVersions() {
    const container = document.getElementById('promptsContent');
    try {
        const data = await api('/prompts');
        const versions = data.versions || [];
        _promptVersionsCache = versions;
        if (versions.length === 0) {
            container.innerHTML = `
                <div class="mb-4">
                    <button class="${tw.btnPrimary}" onclick="window._pages.prompts.showCreatePrompt()">${t('prompts.newVersion')}</button>
                    <button class="${tw.btnSecondary} ml-2" onclick="window._pages.prompts.resetToDefault()">${t('prompts.resetToDefault')}</button>
                </div>
                <div class="${tw.emptyState}">${t('prompts.noVersions')}</div>`;
            loadABTests();
            return;
        }
        container.innerHTML = `
            <div class="mb-4">
                <button class="${tw.btnPrimary}" onclick="window._pages.prompts.showCreatePrompt()">${t('prompts.newVersion')}</button>
                <button class="${tw.btnSecondary} ml-2" onclick="window._pages.prompts.resetToDefault()">${t('prompts.resetToDefault')}</button>
            </div>
            <div class="overflow-x-auto"><table class="${tw.table}" id="promptsTable"><thead><tr><th class="${tw.thSortable}" data-sortable>${t('prompts.name')}</th><th class="${tw.thSortable}" data-sortable>${t('prompts.active')}</th><th class="${tw.thSortable}" data-sortable>${t('prompts.created')}</th><th class="${tw.th}">${t('prompts.action')}</th></tr></thead><tbody>
            ${versions.map(v => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}"><a href="#" class="text-blue-600 dark:text-blue-400 hover:underline" onclick="event.preventDefault(); window._pages.prompts.viewPrompt('${v.id}')">${escapeHtml(v.name)}</a></td>
                    <td class="${tw.td}">${v.is_active ? `<span class="${tw.badgeGreen}">${t('prompts.activeLabel')}</span>` : ''}</td>
                    <td class="${tw.td}" data-sort-value="${v.created_at || ''}">${formatDate(v.created_at)}</td>
                    <td class="${tw.td}">
                        <div class="flex flex-wrap gap-1">
                            ${!v.is_active ? `<button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.prompts.activatePrompt('${v.id}')">${t('prompts.activateBtn')}</button>` : ''}
                            ${!v.is_active ? `<button class="${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.prompts.deletePrompt('${v.id}', '${escapeHtml(v.name).replace(/'/g, "\\'")}')">${t('common.delete')}</button>` : ''}
                        </div>
                    </td>
                </tr>
            `).join('')}
            </tbody></table></div>`;

        makeSortable('promptsTable');
        loadABTests();
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('prompts.failedToLoad', {error: escapeHtml(e.message)})}
            <br><button class="${tw.btnPrimary} ${tw.btnSm} mt-2" onclick="window._pages.prompts.loadPromptVersions()">${t('common.retry')}</button></div>`;
    }
}

function showCreatePrompt() {
    const modalTitle = document.getElementById('createPromptModalTitle');
    if (modalTitle) modalTitle.textContent = t('prompts.createTitle');
    document.getElementById('promptName').value = '';
    document.getElementById('promptName').disabled = false;
    document.getElementById('promptSystemPrompt').value = '';
    document.getElementById('promptSystemPrompt').disabled = false;
    document.getElementById('createPromptBtn').style.display = '';
    document.getElementById('loadDefaultBtn').style.display = '';
    document.getElementById('createPromptModal').classList.add('show');
}

async function createPrompt() {
    const name = document.getElementById('promptName').value.trim();
    const systemPrompt = document.getElementById('promptSystemPrompt').value.trim();
    if (!name || !systemPrompt) { showToast(t('prompts.nameRequired'), 'error'); return; }
    try {
        await api('/prompts', { method: 'POST', body: JSON.stringify({ name, system_prompt: systemPrompt }) });
        closeModal('createPromptModal');
        showToast(t('prompts.created_toast'));
        loadPromptVersions();
    } catch (e) { showToast(t('prompts.createFailed', {error: e.message}), 'error'); }
}

async function activatePrompt(id) {
    if (!confirm(t('prompts.activateConfirm'))) return;
    try {
        await api(`/prompts/${id}/activate`, { method: 'PATCH' });
        showToast(t('prompts.activated'));
        loadPromptVersions();
    } catch (e) { showToast(t('prompts.activateFailed', {error: e.message}), 'error'); }
}

async function viewPrompt(id) {
    try {
        const data = await api(`/prompts/${id}`);
        const v = data.version;
        const modalTitle = document.getElementById('createPromptModalTitle');
        if (modalTitle) modalTitle.textContent = t('prompts.viewTitle');
        document.getElementById('promptName').value = v.name || '';
        document.getElementById('promptName').disabled = true;
        document.getElementById('promptSystemPrompt').value = v.system_prompt || '';
        document.getElementById('promptSystemPrompt').disabled = true;
        document.getElementById('createPromptBtn').style.display = 'none';
        document.getElementById('loadDefaultBtn').style.display = 'none';
        document.getElementById('createPromptModal').classList.add('show');
    } catch (e) { showToast(t('prompts.failedToLoad', {error: e.message}), 'error'); }
}

async function loadDefaultPrompt() {
    try {
        const data = await api('/prompts/default');
        document.getElementById('promptSystemPrompt').value = data.system_prompt || '';
    } catch (e) { showToast(t('prompts.failedToLoad', {error: e.message}), 'error'); }
}

async function resetToDefault() {
    if (!confirm(t('prompts.resetConfirm'))) return;
    try {
        const data = await api('/prompts/default');
        const created = await api('/prompts', {
            method: 'POST',
            body: JSON.stringify({ name: `${data.name} (reset)`, system_prompt: data.system_prompt }),
        });
        const newId = created.version?.id;
        if (newId) {
            await api(`/prompts/${newId}/activate`, { method: 'PATCH' });
        }
        showToast(t('prompts.resetDone'));
        loadPromptVersions();
    } catch (e) { showToast(t('prompts.resetFailed', {error: e.message}), 'error'); }
}

async function deletePrompt(id, name) {
    if (!confirm(t('prompts.deleteConfirm', {name}))) return;
    try {
        await api(`/prompts/${id}`, { method: 'DELETE' });
        showToast(t('prompts.deleted'));
        loadPromptVersions();
    } catch (e) { showToast(t('prompts.deleteFailed', {error: e.message}), 'error'); }
}

// ═══════════════════════════════════════════════════════════
//  A/B Tests
// ═══════════════════════════════════════════════════════════
function abStatusBadge(status) {
    switch (status) {
        case 'active': return `<span class="${tw.badgeGreen}">${escapeHtml(status)}</span>`;
        case 'completed': return `<span class="${tw.badgeBlue}">${escapeHtml(status)}</span>`;
        case 'stopped': return `<span class="${tw.badgeRed}">${escapeHtml(status)}</span>`;
        default: return `<span class="${tw.badge}">${escapeHtml(status || 'unknown')}</span>`;
    }
}

function _significanceBadge(sig) {
    if (!sig) return '';
    if (sig.min_samples_needed) {
        return `<span class="${tw.badge}">${t('prompts.notEnoughData')}</span>`;
    }
    if (sig.is_significant && sig.recommended_variant) {
        return `<span class="${tw.badgeGreen}">${t('prompts.recommended', {variant: sig.recommended_variant})}</span>`;
    }
    return `<span class="${tw.badge}">${t('prompts.noSignificance')}</span>`;
}

function _significanceText(sig) {
    if (!sig) return '';
    if (sig.min_samples_needed) return t('prompts.notEnoughData');
    if (sig.is_significant && sig.recommended_variant) {
        return t('prompts.recommended', {variant: sig.recommended_variant});
    }
    return t('prompts.noSignificance');
}

async function loadABTests() {
    const container = document.getElementById('promptsContent');
    const existing = document.getElementById('abTestsSection');
    if (existing) existing.remove();

    const section = document.createElement('div');
    section.id = 'abTestsSection';
    section.className = 'mt-6';
    container.appendChild(section);

    try {
        const data = await api('/prompts/ab-tests');
        const tests = data.tests || [];

        let html = `<h3 class="text-base font-semibold text-neutral-900 dark:text-neutral-50 mb-3">${t('prompts.abTests')}</h3>`;
        html += `<div class="mb-4"><button class="${tw.btnPrimary}" onclick="window._pages.prompts.showCreateABTest()">${t('prompts.newABTest')}</button></div>`;

        if (tests.length === 0) {
            html += `<div class="${tw.emptyState}">${t('prompts.noABTests')}</div>`;
        } else {
            html += `<div class="overflow-x-auto"><table class="${tw.table}" id="abTestsTable"><thead><tr>
                <th class="${tw.th}">${t('prompts.testName')}</th>
                <th class="${tw.th}">${t('prompts.variantA')}</th>
                <th class="${tw.th}">${t('prompts.variantB')}</th>
                <th class="${tw.th}">${t('prompts.callsAB')}</th>
                <th class="${tw.th}">${t('prompts.qualityAB')}</th>
                <th class="${tw.th}">${t('prompts.abStatus')}</th>
                <th class="${tw.th}">${t('prompts.abAction')}</th>
            </tr></thead><tbody>`;

            for (const test of tests) {
                const qualityA = test.quality_a != null ? Number(test.quality_a).toFixed(2) : '—';
                const qualityB = test.quality_b != null ? Number(test.quality_b).toFixed(2) : '—';

                let actionHtml = '';
                if (test.status === 'active') {
                    actionHtml = `<button class="${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.prompts.stopABTest('${test.id}')">${t('prompts.stopBtn')}</button>`;
                } else {
                    actionHtml = (test.significance ? _significanceBadge(test.significance) + ' ' : '')
                        + `<button class="${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.prompts.deleteABTest('${test.id}', '${escapeHtml(test.test_name).replace(/'/g, "\\'")}')">${t('common.delete')}</button>`;
                }

                html += `<tr class="${tw.trHover}">
                    <td class="${tw.td}"><a href="#" class="text-blue-600 dark:text-blue-400 hover:underline" onclick="event.preventDefault(); window._pages.prompts.showABReport('${test.id}')">${escapeHtml(test.test_name)}</a></td>
                    <td class="${tw.td}">${escapeHtml(test.variant_a_name || '')}</td>
                    <td class="${tw.td}">${escapeHtml(test.variant_b_name || '')}</td>
                    <td class="${tw.td}">${test.calls_a || 0} / ${test.calls_b || 0}</td>
                    <td class="${tw.td}">${qualityA} / ${qualityB}</td>
                    <td class="${tw.td}">${abStatusBadge(test.status)}</td>
                    <td class="${tw.td}">${actionHtml}</td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }

        section.innerHTML = html;
    } catch (e) {
        section.innerHTML = `<div class="${tw.emptyState}">${t('prompts.failedToLoadAB', {error: escapeHtml(e.message)})}</div>`;
    }
}

function showCreateABTest() {
    document.getElementById('abTestName').value = '';
    document.getElementById('abVariantA').value = '';
    document.getElementById('abVariantB').value = '';
    document.getElementById('abTrafficSplit').value = '50';
    document.getElementById('abTrafficSplitValue').textContent = '50%';

    const selA = document.getElementById('abVariantA');
    const selB = document.getElementById('abVariantB');
    selA.innerHTML = `<option value="">${t('common.select')}</option>`;
    selB.innerHTML = `<option value="">${t('common.select')}</option>`;
    for (const v of _promptVersionsCache) {
        const opt = `<option value="${v.id}">${escapeHtml(v.name)}</option>`;
        selA.innerHTML += opt;
        selB.innerHTML += opt;
    }

    document.getElementById('createABTestModal').classList.add('show');
}

async function createABTest() {
    const testName = document.getElementById('abTestName').value.trim();
    const variantAId = document.getElementById('abVariantA').value;
    const variantBId = document.getElementById('abVariantB').value;
    const trafficSplit = parseInt(document.getElementById('abTrafficSplit').value, 10) / 100;

    if (!testName) { showToast(t('prompts.testNameRequired'), 'error'); return; }
    if (!variantAId || !variantBId) { showToast(t('prompts.variantsRequired'), 'error'); return; }
    if (variantAId === variantBId) { showToast(t('prompts.variantsMustDiffer'), 'error'); return; }

    try {
        await api('/prompts/ab-tests', {
            method: 'POST',
            body: JSON.stringify({
                test_name: testName,
                variant_a_id: variantAId,
                variant_b_id: variantBId,
                traffic_split: trafficSplit,
            }),
        });
        closeModal('createABTestModal');
        showToast(t('prompts.abTestCreated'));
        loadABTests();
    } catch (e) { showToast(t('prompts.abTestCreateFailed', {error: e.message}), 'error'); }
}

async function stopABTest(id) {
    if (!confirm(t('prompts.stopConfirm'))) return;
    try {
        const data = await api(`/prompts/ab-tests/${id}/stop`, { method: 'PATCH' });
        const resultText = _significanceText(data.test?.significance);
        if (resultText) {
            showToast(t('prompts.stoppedWithResult', {result: resultText}));
        } else {
            showToast(t('prompts.stopped'));
        }
        loadABTests();
    } catch (e) { showToast(t('prompts.stopFailed', {error: e.message}), 'error'); }
}

async function deleteABTest(id, name) {
    if (!confirm(t('prompts.deleteABTestConfirm', {name}))) return;
    try {
        await api(`/prompts/ab-tests/${id}`, { method: 'DELETE' });
        showToast(t('prompts.abTestDeleted'));
        loadABTests();
    } catch (e) { showToast(t('prompts.abTestDeleteFailed', {error: e.message}), 'error'); }
}

// ═══════════════════════════════════════════════════════════
//  A/B Test Report
// ═══════════════════════════════════════════════════════════
const CRITERION_I18N = {
    accuracy: 'prompts.criterionAccuracy',
    completeness: 'prompts.criterionCompleteness',
    politeness: 'prompts.criterionPoliteness',
    response_time: 'prompts.criterionResponseTime',
    problem_resolution: 'prompts.criterionProblemResolution',
    language_quality: 'prompts.criterionLanguageQuality',
    tool_usage: 'prompts.criterionToolUsage',
    scenario_adherence: 'prompts.criterionScenarioAdherence',
};

async function showABReport(testId) {
    const container = document.getElementById('abReportContent');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;
    document.getElementById('abReportModal').classList.add('show');

    try {
        const report = await api(`/prompts/ab-tests/${testId}/report`);
        const test = report.test || {};
        const summary = report.summary || {};
        const perCriterion = report.per_criterion || [];
        const byScenario = report.by_scenario || [];
        const daily = report.daily || [];

        const titleEl = document.getElementById('abReportModalTitle');
        if (titleEl) titleEl.textContent = `${t('prompts.abReportTitle')}: ${test.test_name || ''}`;

        const fmtPct = (v) => v != null ? (v * 100).toFixed(1) + '%' : '—';
        const fmtSec = (v) => v != null ? v.toFixed(0) + 's' : '—';
        const sig = summary.significance || {};

        let html = '';

        html += `<h3 class="text-sm font-semibold text-neutral-700 dark:text-neutral-300 mb-2">${t('prompts.reportSummary')}</h3>`;
        html += `<div class="${tw.statsGrid}">
            <div class="${tw.card}">
                <div class="${tw.statValue}">${summary.calls_a || 0} / ${summary.calls_b || 0}</div>
                <div class="${tw.statLabel}">${t('prompts.reportCalls')} (A / B)</div>
            </div>
            <div class="${tw.card}">
                <div class="${tw.statValue}">${fmtPct(summary.quality_a)} / ${fmtPct(summary.quality_b)}</div>
                <div class="${tw.statLabel}">${t('prompts.reportQuality')} (A / B)</div>
            </div>
            <div class="${tw.card}">
                <div class="${tw.statValue}">${fmtSec(summary.avg_duration_a)} / ${fmtSec(summary.avg_duration_b)}</div>
                <div class="${tw.statLabel}">${t('prompts.reportAvgDuration')} (A / B)</div>
            </div>
            <div class="${tw.card}">
                <div class="${tw.statValue}">${fmtPct(summary.transfer_rate_a)} / ${fmtPct(summary.transfer_rate_b)}</div>
                <div class="${tw.statLabel}">${t('prompts.reportTransferRate')} (A / B)</div>
            </div>
        </div>`;

        if (sig.is_significant != null) {
            html += `<div class="mb-4">${_significanceBadge(sig)} <span class="${tw.mutedText} ml-2">p=${sig.p_value_approx ?? '—'}, z=${sig.z_score ?? '—'}</span></div>`;
        }

        if (perCriterion.length > 0) {
            html += `<h3 class="text-sm font-semibold text-neutral-700 dark:text-neutral-300 mb-2 mt-4">${t('prompts.reportCriteria')}</h3>`;
            html += `<div class="overflow-x-auto mb-4"><table class="${tw.table}"><thead><tr>
                <th class="${tw.th}">${t('prompts.reportCriterion')}</th>
                <th class="${tw.th}">${test.variant_a_name || 'A'}</th>
                <th class="${tw.th}">${test.variant_b_name || 'B'}</th>
                <th class="${tw.th}">${t('prompts.reportDelta')}</th>
            </tr></thead><tbody>`;
            for (const cr of perCriterion) {
                const delta = (cr.avg_a != null && cr.avg_b != null) ? cr.avg_a - cr.avg_b : null;
                const deltaStr = delta != null ? (delta >= 0 ? '+' : '') + (delta * 100).toFixed(1) + '%' : '—';
                const deltaClass = delta != null ? (delta > 0 ? 'text-emerald-600 dark:text-emerald-400' : delta < 0 ? 'text-red-600 dark:text-red-400' : '') : '';
                html += `<tr class="${tw.trHover}">
                    <td class="${tw.td}">${t(CRITERION_I18N[cr.criterion] || cr.criterion)}</td>
                    <td class="${tw.td}">${qualityBadge(cr.avg_a)}</td>
                    <td class="${tw.td}">${qualityBadge(cr.avg_b)}</td>
                    <td class="${tw.td}"><span class="${deltaClass} font-medium">${deltaStr}</span></td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }

        if (byScenario.length > 0) {
            html += `<h3 class="text-sm font-semibold text-neutral-700 dark:text-neutral-300 mb-2 mt-4">${t('prompts.reportScenarios')}</h3>`;
            html += `<div class="overflow-x-auto mb-4"><table class="${tw.table}"><thead><tr>
                <th class="${tw.th}">${t('prompts.reportScenario')}</th>
                <th class="${tw.th}">${t('prompts.reportCalls')} A</th>
                <th class="${tw.th}">${t('prompts.reportCalls')} B</th>
                <th class="${tw.th}">${t('prompts.reportQuality')} A</th>
                <th class="${tw.th}">${t('prompts.reportQuality')} B</th>
            </tr></thead><tbody>`;
            for (const sc of byScenario) {
                html += `<tr class="${tw.trHover}">
                    <td class="${tw.td}"><span class="${tw.badgeBlue}">${escapeHtml(sc.scenario)}</span></td>
                    <td class="${tw.td}">${sc.calls_a}</td>
                    <td class="${tw.td}">${sc.calls_b}</td>
                    <td class="${tw.td}">${qualityBadge(sc.quality_a)}</td>
                    <td class="${tw.td}">${qualityBadge(sc.quality_b)}</td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }

        if (daily.length > 0) {
            html += `<h3 class="text-sm font-semibold text-neutral-700 dark:text-neutral-300 mb-2 mt-4">${t('prompts.reportDaily')}</h3>`;
            html += '<div class="space-y-2 mb-4">';
            for (const d of daily) {
                const qa = d.quality_a != null ? d.quality_a : 0;
                const qb = d.quality_b != null ? d.quality_b : 0;
                html += `<div class="flex items-center gap-2 text-xs">
                    <span class="w-20 text-neutral-500 dark:text-neutral-400 shrink-0">${escapeHtml(d.date)}</span>
                    <div class="flex-1 flex flex-col gap-0.5">
                        <div class="flex items-center gap-1">
                            <span class="w-4 text-right text-neutral-400">A</span>
                            <div class="h-3 rounded bg-blue-500 dark:bg-blue-400" style="width:${Math.max(qa * 100, 2)}%"></div>
                            <span class="${tw.mutedText}">${fmtPct(d.quality_a)} (${d.calls_a})</span>
                        </div>
                        <div class="flex items-center gap-1">
                            <span class="w-4 text-right text-neutral-400">B</span>
                            <div class="h-3 rounded bg-violet-500 dark:bg-violet-400" style="width:${Math.max(qb * 100, 2)}%"></div>
                            <span class="${tw.mutedText}">${fmtPct(d.quality_b)} (${d.calls_b})</span>
                        </div>
                    </div>
                </div>`;
            }
            html += '</div>';
        }

        html += `<div class="mt-4"><button class="${tw.btnPrimary}" onclick="window._pages.prompts.exportABReportCSV('${testId}')">${t('prompts.exportCSV')}</button></div>`;

        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('prompts.reportFailedToLoad', {error: escapeHtml(e.message)})}</div>`;
    }
}

async function exportABReportCSV(testId) {
    try {
        const res = await fetchWithAuth(`/prompts/ab-tests/${testId}/report/csv`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        downloadBlob(blob, `ab_report_${testId}.csv`);
        showToast(t('prompts.csvExported'));
    } catch (e) {
        showToast(t('prompts.exportFailed', {error: e.message}), 'error');
    }
}

// ═══════════════════════════════════════════════════════════
//  Init & exports
// ═══════════════════════════════════════════════════════════
export function init() {
    registerPageLoader('prompts', loadPromptVersions);
}

window._pages = window._pages || {};
window._pages.prompts = {
    loadPromptVersions, showCreatePrompt, createPrompt, activatePrompt,
    viewPrompt, loadDefaultPrompt, resetToDefault, deletePrompt,
    loadABTests, showCreateABTest, createABTest, stopABTest, deleteABTest, showABReport, exportABReportCSV,
};
