<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { onDestroy, tick, untrack } from 'svelte';
	import { api, websocketUrl } from '../api';

	let { serviceId }: { serviceId: string } = $props();

	const MAX_LINES = 5000;

	let lines = $state<string[]>([]);
	let tail = $state(200);
	let following = $state(false);
	let error = $state<string | undefined>();
	let pre: HTMLPreElement | undefined = $state();
	let socket: WebSocket | undefined;
	let partial = '';

	async function scrollToEnd() {
		await tick();
		if (pre) pre.scrollTop = pre.scrollHeight;
	}

	async function load() {
		stop();
		error = undefined;
		try {
			const result = await api.get<{ lines: string[] }>(
				`/services/${serviceId}/logs?tail=${tail}`
			);
			lines = result.lines;
			scrollToEnd();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}

	function follow() {
		stop();
		lines = [];
		partial = '';
		error = undefined;
		following = true;
		socket = new WebSocket(websocketUrl(`/services/${serviceId}/logs/follow?tail=${tail}`));
		socket.onmessage = (event: MessageEvent<string>) => {
			const parts = (partial + event.data).split('\n');
			partial = parts.pop() ?? '';
			lines = [...lines, ...parts].slice(-MAX_LINES);
			scrollToEnd();
		};
		socket.onclose = (event) => {
			following = false;
			if (event.code !== 1000 && event.code !== 1005) {
				error = event.reason || `Log stream closed (${event.code}).`;
			}
		};
	}

	function stop() {
		socket?.close();
		socket = undefined;
		following = false;
	}

	// The parent re-polls the service, which re-evaluates this prop. Only reload when the ID
	// actually changes, or an open log stream would be dropped every poll.
	let loadedFor: string | undefined;
	$effect(() => {
		const id = serviceId;
		if (id === loadedFor) return;
		loadedFor = id;
		untrack(load);
	});
	onDestroy(stop);
</script>

<div class="toolbar">
	<label>
		Lines
		<select bind:value={tail} onchange={() => (following ? follow() : load())}>
			{#each [100, 200, 1000, 5000] as n (n)}
				<option value={n}>{n}</option>
			{/each}
		</select>
	</label>
	<button onclick={load} disabled={following}>Refresh</button>
	{#if following}
		<button onclick={stop}>Stop following</button>
	{:else}
		<button onclick={follow}>Follow</button>
	{/if}
	{#if following}<span class="muted">following…</span>{/if}
</div>
{#if error}<p class="error">{error}</p>{/if}
<pre bind:this={pre}>{lines.join('\n') || 'No log output.'}</pre>

<style>
	.toolbar {
		display: flex;
		gap: 0.6rem;
		align-items: center;
		margin-bottom: 0.5rem;
	}
	select {
		font: inherit;
		margin-left: 0.3rem;
	}
	pre {
		background: var(--code);
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 0.75rem;
		margin: 0;
		max-height: 32rem;
		overflow: auto;
		white-space: pre-wrap;
		word-break: break-all;
	}
</style>
