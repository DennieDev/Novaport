<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { agentsMissing } from '../format';
	import { onDestroy, tick, untrack } from 'svelte';
	import { api, websocketUrl } from '../api';

	// `source` is an API path such as /services/<id> or /tasks/<id>; logs live under it.
	let { source }: { source: string } = $props();

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
				`${source}/logs?tail=${tail}`
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
		socket = new WebSocket(websocketUrl(`${source}/logs/follow?tail=${tail}`));
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

	// The parent re-polls its data, which re-evaluates this prop. Only reload when the source
	// actually changes, or an open log stream would be dropped every poll.
	let loadedFor: string | undefined;
	$effect(() => {
		const id = source;
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
{#if error}<p class={agentsMissing(error) ? 'muted' : 'error'}>{error}</p>{/if}
<pre bind:this={pre}>{lines.join('\n') || 'No log output.'}</pre>

<style>
	.toolbar {
		display: flex;
		gap: 0.6rem;
		align-items: center;
		margin-bottom: 0.75rem;
	}
	select {
		font: inherit;
		margin-left: 0.3rem;
	}
	pre {
		background: #0c0d10;
		color: #d6d8df;
		border-radius: var(--radius);
		padding: 0.75rem 0.9rem;
		margin: 0;
		line-height: 1.55;
		height: 60vh;
		min-height: 18rem;
		overflow: auto;
		white-space: pre-wrap;
		word-break: break-all;
	}
</style>
