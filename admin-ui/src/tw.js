// Shared Tailwind CSS class constants for JS template literals.
// Single source of truth for component styling across all page modules.

// --- Badges ---
const _badge = 'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium';
export const badge = `${_badge} bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400`;
export const badgeGreen = `${_badge} bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400`;
export const badgeYellow = `${_badge} bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-400`;
export const badgeRed = `${_badge} bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-400`;
export const badgeBlue = `${_badge} bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-400`;
export const badgePurple = `${_badge} bg-violet-50 text-violet-700 dark:bg-violet-950 dark:text-violet-400`;
export const badgeGray = `${_badge} bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400`;

// --- Buttons ---
const _btn = 'inline-flex items-center justify-center px-3 py-1.5 text-sm font-medium rounded-md transition-colors cursor-pointer max-md:min-h-11';
export const btnPrimary = `${_btn} bg-blue-600 text-white hover:bg-blue-700`;
export const btnDanger = `${_btn} bg-red-600 text-white hover:bg-red-700`;
export const btnSecondary = `${_btn} bg-neutral-500 text-white hover:bg-neutral-600 dark:bg-neutral-600 dark:hover:bg-neutral-500`;
export const btnGreen = `${_btn} bg-emerald-600 text-white hover:bg-emerald-700`;
export const btnPurple = `${_btn} bg-violet-600 text-white hover:bg-violet-700`;
export const btnSm = 'text-xs px-2 py-1';

// --- Cards ---
export const card = 'bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg p-5 mb-4';
export const statValue = 'text-2xl font-bold text-blue-600 dark:text-blue-400';
export const statLabel = 'text-xs text-neutral-500 dark:text-neutral-400 mt-1';

// --- Tables ---
export const table = 'w-full text-sm responsive-table';
export const th = 'px-3 py-2.5 text-left text-xs font-semibold text-neutral-500 dark:text-neutral-400 uppercase tracking-wider border-b border-neutral-200 dark:border-neutral-700';
export const thSortable = `${th} cursor-pointer select-none hover:text-neutral-700 dark:hover:text-neutral-300`;
export const td = 'px-3 py-2.5 border-b border-neutral-100 dark:border-neutral-800 text-neutral-700 dark:text-neutral-300';
export const tdActions = 'px-3 py-2.5 border-b border-neutral-100 dark:border-neutral-800 text-neutral-700 dark:text-neutral-300 td-actions';
export const trHover = 'hover:bg-neutral-50 dark:hover:bg-neutral-800/50 transition-colors';

// --- Filters ---
export const filterBar = 'flex flex-wrap items-center gap-2 mb-4 filter-collapsible max-md:open';
export const filterToggle = 'hidden max-md:inline-flex items-center justify-center px-3 py-1.5 text-sm font-medium rounded-md bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-300 cursor-pointer mb-2 min-h-11';
export const filterInput = 'px-2.5 py-1.5 text-sm border border-neutral-300 dark:border-neutral-700 rounded-md bg-white dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100 focus:outline-none focus:ring-2 focus:ring-blue-500 max-md:w-full';
export const filterBtn = `${_btn} bg-blue-600 text-white hover:bg-blue-700 max-md:w-full`;

// --- Pagination ---
export const paginationWrap = 'pagination flex justify-center gap-1 mt-4';
export const pageBtn = 'px-3 py-1.5 text-sm border border-neutral-300 dark:border-neutral-700 rounded-md bg-white dark:bg-neutral-800 text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-700 cursor-pointer transition-colors max-md:min-h-11 max-md:min-w-11';

// --- Empty state ---
export const emptyState = 'text-center py-8 text-neutral-400 dark:text-neutral-500 text-sm';

// --- Loading ---
export const loadingWrap = 'flex justify-center py-8';

// --- Forms ---
export const formGroup = 'mb-3';
export const formLabel = 'block text-sm font-medium text-neutral-700 dark:text-neutral-300 mb-1';
export const formInput = 'w-full px-3 py-2 text-sm border border-neutral-300 dark:border-neutral-700 rounded-md bg-white dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100 focus:outline-none focus:ring-2 focus:ring-blue-500';
export const formTextarea = `${formInput} min-h-30 font-mono resize-y`;
export const formSelect = formInput;
export const selectSm = 'text-xs px-1.5 py-1 border border-neutral-300 dark:border-neutral-700 rounded bg-white dark:bg-neutral-800 text-neutral-700 dark:text-neutral-300';

// --- Modal ---
export const modalClose = 'float-right cursor-pointer text-lg text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-200 transition-colors leading-none max-md:text-2xl max-md:p-2';
export const modalTitle = 'text-base font-semibold text-neutral-900 dark:text-neutral-50 mb-4';

// --- Transcription ---
export const turnWrap = 'py-2 border-b border-neutral-100 dark:border-neutral-800';
export const speakerCustomer = 'font-semibold text-xs text-blue-600 dark:text-blue-400';
export const speakerBot = 'font-semibold text-xs text-emerald-600 dark:text-emerald-400';
export const turnText = 'text-sm text-neutral-700 dark:text-neutral-300 mt-0.5';

// --- Breadcrumbs ---
export const breadcrumbs = 'text-xs text-neutral-500 dark:text-neutral-400 mb-3';
export const breadcrumbLink = 'text-blue-600 dark:text-blue-400 hover:underline';

// --- Page layout ---
export const pageTitle = 'text-lg font-semibold text-neutral-900 dark:text-neutral-50 mb-4';
export const sectionTitle = 'text-sm font-semibold text-neutral-900 dark:text-neutral-50 mb-3';
export const statsGrid = 'grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-4 mb-6 max-md:grid-cols-2 max-[480px]:grid-cols-1';

// --- Misc ---
export const link = 'text-blue-600 dark:text-blue-400 hover:underline';
export const mutedText = 'text-xs text-neutral-500 dark:text-neutral-400';
export const tabBar = 'tab-bar flex border-b border-neutral-200 dark:border-neutral-700 mb-4';
export const tabBtn = 'px-4 py-2 text-sm text-neutral-500 dark:text-neutral-400 border-b-2 border-transparent -mb-px cursor-pointer transition-colors hover:text-neutral-700 dark:hover:text-neutral-200 max-md:min-h-11';
export const grafanaFrame = 'w-full h-[600px] max-md:h-[350px] border-none rounded-lg';
