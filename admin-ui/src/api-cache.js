/**
 * Simple client-side cache for API GET responses with TTL.
 *
 * Usage:
 *   import { cachedApi, invalidateCache } from './api-cache.js';
 *   const data = await cachedApi('/analytics/calls?limit=20', {}, 30_000);
 *   // After a mutation:
 *   invalidateCache('/analytics/calls');
 */
import { api } from './api.js';

const _cache = new Map();

/**
 * Cached wrapper around api(). Only caches GET requests.
 * @param {string} path - API path
 * @param {object} [opts] - fetch options (passed to api())
 * @param {number} [ttlMs=30000] - cache TTL in milliseconds
 * @returns {Promise<any>}
 */
export async function cachedApi(path, opts = {}, ttlMs = 30_000) {
    const method = (opts.method || 'GET').toUpperCase();
    if (method !== 'GET') return api(path, opts);

    const entry = _cache.get(path);
    if (entry && Date.now() - entry.ts < ttlMs) {
        return entry.data;
    }

    const data = await api(path, opts);
    _cache.set(path, { data, ts: Date.now() });
    return data;
}

/**
 * Invalidate cached entries matching a URL prefix.
 * @param {string} prefix - URL prefix to match (e.g. '/analytics/calls')
 */
export function invalidateCache(prefix) {
    for (const key of _cache.keys()) {
        if (key.startsWith(prefix)) _cache.delete(key);
    }
}

/** Clear all cached entries (e.g. on logout). */
export function clearCache() {
    _cache.clear();
}
