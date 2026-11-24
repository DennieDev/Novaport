// SPDX-License-Identifier: AGPL-3.0-or-later
import { onDestroy } from 'svelte';
import { api } from './api';
import { live, subscribe, type Change } from './live.svelte';

export interface Resource<T> {
	readonly data: T | undefined;
	readonly error: string | undefined;
	readonly loading: boolean;
	refresh(): Promise<void>;
}

export interface Watch {
	/** Tables whose changes should reload this resource. */
	topics: string[];
	/** Narrows the changes within those topics, for example to one stack. */
	match?: (change: Change) => boolean;
}

// With live updates connected, polling only catches what the event stream can't explain.
const LIVE_INTERVAL_MS = 120_000;
const DEBOUNCE_MS = 250;

/**
 * Loads an API path and reloads it every `intervalMs` while the component is mounted. With
 * `watch`, it also reloads when the server announces a matching change, and polls rarely
 * while that stream is connected.
 */
export function poll<T>(path: () => string, intervalMs = 10_000, watch?: Watch): Resource<T> {
	let data = $state<T | undefined>(undefined);
	let error = $state<string | undefined>(undefined);
	let loading = $state(true);

	async function refresh() {
		try {
			data = await api.get<T>(path());
			error = undefined;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		path();
		loading = true;
		refresh();
	});

	let timer: ReturnType<typeof setTimeout> | undefined;
	let destroyed = false;
	function schedule() {
		const delay = watch && live.connected ? Math.max(intervalMs, LIVE_INTERVAL_MS) : intervalMs;
		timer = setTimeout(async () => {
			await refresh();
			if (!destroyed) schedule();
		}, delay);
	}
	schedule();

	let pending: ReturnType<typeof setTimeout> | undefined;
	const unsubscribe = watch
		? subscribe((change) => {
				const wanted =
					change.topic === '*' ||
					(watch.topics.includes(change.topic) && (watch.match?.(change) ?? true));
				if (!wanted || pending !== undefined) return;
				pending = setTimeout(() => {
					pending = undefined;
					refresh();
				}, DEBOUNCE_MS);
			})
		: undefined;

	onDestroy(() => {
		destroyed = true;
		clearTimeout(timer);
		clearTimeout(pending);
		unsubscribe?.();
	});

	return {
		get data() {
			return data;
		},
		get error() {
			return error;
		},
		get loading() {
			return loading;
		},
		refresh
	};
}
