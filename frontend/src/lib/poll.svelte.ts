// SPDX-License-Identifier: AGPL-3.0-or-later
import { onDestroy } from 'svelte';
import { api } from './api';

export interface Resource<T> {
	readonly data: T | undefined;
	readonly error: string | undefined;
	readonly loading: boolean;
	refresh(): Promise<void>;
}

/** Loads an API path and reloads it every `intervalMs` while the component is mounted. */
export function poll<T>(path: () => string, intervalMs = 10_000): Resource<T> {
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
	const timer = setInterval(refresh, intervalMs);
	onDestroy(() => clearInterval(timer));

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
