<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { FitAddon } from '@xterm/addon-fit';
	import { Terminal } from '@xterm/xterm';
	import '@xterm/xterm/css/xterm.css';
	import { onDestroy } from 'svelte';
	import { websocketUrl } from '../api';

	// `path` is the exec WebSocket under /api, such as /tasks/<id>/exec.
	let { path, autoConnect = false }: { path: string; autoConnect?: boolean } = $props();

	let shell = $state('/bin/sh');
	let status = $state<'idle' | 'connecting' | 'connected' | 'closed'>('idle');
	let closeReason = $state<string | undefined>();
	let container: HTMLDivElement | undefined = $state();

	let terminal: Terminal | undefined;
	let fit: FitAddon | undefined;
	let socket: WebSocket | undefined;
	let observer: ResizeObserver | undefined;

	function send(event: object) {
		if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(event));
	}

	export function connect() {
		disconnect();
		if (!container) return;
		terminal = new Terminal({
			cursorBlink: true,
			fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
			fontSize: 13,
			convertEol: false,
			theme: { background: '#0c0d10' }
		});
		fit = new FitAddon();
		terminal.loadAddon(fit);
		terminal.open(container);
		fit.fit();
		terminal.focus();

		status = 'connecting';
		closeReason = undefined;
		socket = new WebSocket(websocketUrl(`${path}?cmd=${encodeURIComponent(shell)}`));
		socket.binaryType = 'arraybuffer';
		socket.onopen = () => {
			status = 'connected';
			send({ type: 'resize', cols: terminal!.cols, rows: terminal!.rows });
		};
		socket.onmessage = (event: MessageEvent<ArrayBuffer | string>) => {
			terminal?.write(typeof event.data === 'string' ? event.data : new Uint8Array(event.data));
		};
		socket.onclose = (event) => {
			status = 'closed';
			if (event.code !== 1000 && event.code !== 1005) {
				closeReason = event.reason || `Connection closed (${event.code}).`;
			}
			terminal?.write('\r\n\x1b[2m[session ended]\x1b[0m\r\n');
		};
		terminal.onData((data) => send({ type: 'input', data }));
		terminal.onResize(({ cols, rows }) => send({ type: 'resize', cols, rows }));

		observer = new ResizeObserver(() => fit?.fit());
		observer.observe(container);
	}

	function disconnect() {
		observer?.disconnect();
		socket?.close();
		terminal?.dispose();
		socket = undefined;
		terminal = undefined;
	}

	$effect(() => {
		if (autoConnect && container && status === 'idle') connect();
	});

	onDestroy(disconnect);
</script>

<div class="bar">
	<label>
		Shell
		<select bind:value={shell} disabled={status === 'connected' || status === 'connecting'}>
			<option value="/bin/sh">/bin/sh</option>
			<option value="/bin/bash">/bin/bash</option>
			<option value="/bin/ash">/bin/ash</option>
		</select>
	</label>
	{#if status === 'connected' || status === 'connecting'}
		<button onclick={disconnect}>Disconnect</button>
	{:else}
		<button class="primary" onclick={connect}>{status === 'closed' ? 'Reconnect' : 'Connect'}</button>
	{/if}
	<span class="muted">{status === 'idle' ? '' : status}</span>
	{#if closeReason}<span class="error">{closeReason}</span>{/if}
</div>

<div class="terminal" bind:this={container}></div>

<style>
	.bar {
		display: flex;
		gap: 0.6rem;
		align-items: center;
		margin-bottom: 0.75rem;
	}
	select {
		margin-left: 0.3rem;
	}
	.terminal :global(.xterm) {
		height: 100%;
	}
	.terminal {
		height: 65vh;
		overflow: hidden;
		background: #0c0d10;
		border-radius: var(--radius);
		padding: 0.6rem;
	}
</style>
