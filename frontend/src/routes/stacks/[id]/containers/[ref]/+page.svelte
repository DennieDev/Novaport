<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { page } from '$app/state';
	import { api, errorMessage } from '#lib/api.ts';
	import Badge from '#lib/components/Badge.svelte';
	import Icon from '#lib/components/Icon.svelte';
	import LogViewer from '#lib/components/LogViewer.svelte';
	import Status from '#lib/components/Status.svelte';
	import Terminal from '#lib/components/Terminal.svelte';
	import { ago, shortId } from '#lib/format.ts';
	import { onStack } from '#lib/live.svelte.ts';
	import { poll } from '#lib/poll.svelte.ts';
	import { can } from '#lib/session.svelte.ts';
	import type { ContainerInfo, Stack } from '#lib/types.ts';

	const id = $derived(page.params.id ?? '');
	const ref = $derived(page.params.ref ?? '');
	const tab = $derived(page.url.searchParams.get('tab') ?? 'info');
	const base = $derived(`/stacks/${id}/containers/${ref}`);

	const stack = poll<Stack>(() => `/stacks/${id}`, 60_000, {
		topics: ['stacks'],
		match: onStack(() => id)
	});
	const info = poll<ContainerInfo>(() => base, 5000);

	let showEnv = $state(false);
	let busy = $state(false);
	let message = $state<{ text: string; error: boolean } | undefined>();

	const operator = $derived(can('operator', id));
	const tabs = $derived([
		{ key: 'info', label: 'Info', icon: 'info' as const },
		{ key: 'logs', label: 'Logs', icon: 'logs' as const },
		...(operator ? [{ key: 'console', label: 'Console', icon: 'console' as const }] : [])
	]);

	async function restart() {
		const c = info.data;
		if (!c || !confirm(`Restart ${c.name}?`)) return;
		busy = true;
		message = undefined;
		try {
			if (stack.data?.type === 'compose') {
				await api.post(`${base}/restart`);
			} else {
				await api.post(`/stacks/${id}/services/${encodeURIComponent(c.service)}/restart`);
			}
			message = { text: 'Restart started.', error: false };
			await info.refresh();
		} catch (e) {
			message = { text: errorMessage(e), error: true };
		} finally {
			busy = false;
		}
	}

	function when(value: string | null): string {
		return value ? `${new Date(value).toLocaleString()} (${ago(value)})` : '–';
	}
</script>

<p class="crumbs">
	<a href="/stacks">Stacks</a>
	<Icon name="chevron" size={12} />
	<a href="/stacks/{id}">{stack.data?.name ?? 'Stack'}</a>
	{#if info.data}
		<Icon name="chevron" size={12} />
		<span>{info.data.service}</span>
	{/if}
</p>

<Status loading={info.loading && !info.data} error={info.error} />

{#if info.data}
	{@const c = info.data}
	<div class="page-header">
		<div>
			<h1>
				{c.name}
				<Badge value={c.state} />
				{#if c.health}<Badge value={c.health} />{/if}
			</h1>
			<p class="subtitle">
				<code>{c.image}</code>
				{#if c.node}· on {c.node}{/if}
			</p>
		</div>
		{#if operator}
			<div class="toolbar">
				<button onclick={restart} disabled={busy}>
					<Icon name="restart" size={14} />
					{stack.data?.type === 'compose' ? 'Restart container' : 'Restart service'}
				</button>
			</div>
		{/if}
	</div>
	{#if message}<p class:error={message.error} class:muted={!message.error}>{message.text}</p>{/if}

	<nav class="tabs">
		{#each tabs as t (t.key)}
			<a href="{base}?tab={t.key}" class:active={tab === t.key}>{t.label}</a>
		{/each}
	</nav>

	{#if tab === 'logs'}
		{#if c.container_id}
			<LogViewer source={base} />
		{:else}
			<p class="muted">This task has no container yet.</p>
		{/if}
	{:else if tab === 'console' && operator}
		{#if c.state === 'running'}
			{#key ref}<Terminal path="{base}/exec" autoConnect />{/key}
		{:else}
			<p class="muted">The container isn't running.</p>
		{/if}
	{:else}
		<div class="grid">
			<section class="panel">
				<h3>Status</h3>
				<dl class="kv">
					<dt>State</dt>
					<dd>{c.state}{c.status && c.status !== c.state ? ` · ${c.status}` : ''}</dd>
					{#if c.error}<dt>Error</dt><dd class="error">{c.error}</dd>{/if}
					{#if c.exit_code != null && c.state !== 'running'}<dt>Exit code</dt><dd>{c.exit_code}</dd>{/if}
					{#if c.health_log}<dt>Last check</dt><dd><code>{c.health_log}</code></dd>{/if}
					<dt>Created</dt>
					<dd>{when(c.created_at)}</dd>
					{#if c.started_at}<dt>Started</dt><dd>{when(c.started_at)}</dd>{/if}
					{#if c.finished_at}<dt>Finished</dt><dd>{when(c.finished_at)}</dd>{/if}
					{#if c.restart_count != null}<dt>Restarts</dt><dd>{c.restart_count}</dd>{/if}
					<dt>Container</dt>
					<dd><code>{shortId(c.container_id)}</code></dd>
				</dl>
			</section>

			<section class="panel">
				<h3>Runtime</h3>
				<dl class="kv">
					<dt>Command</dt>
					<dd>{#if c.command.length}<code>{c.command.join(' ')}</code>{:else}<span class="muted">image default</span>{/if}</dd>
					<dt>Ports</dt>
					<dd>
						{#each c.ports as p (`${p.published}/${p.target}/${p.protocol}`)}
							<div><code>{p.published ?? '–'} → {p.target}/{p.protocol}</code> <span class="muted">{p.mode}</span></div>
						{:else}<span class="muted">none</span>{/each}
					</dd>
					<dt>Networks</dt>
					<dd>
						{#each c.networks as n (n.name)}
							<div>{n.name} <span class="muted">{n.addresses.join(', ')}</span></div>
						{:else}<span class="muted">none</span>{/each}
					</dd>
				</dl>
			</section>
		</div>

		<h2>Mounts</h2>
		<table>
			<thead><tr><th>Type</th><th>Source</th><th>Target</th><th>Mode</th></tr></thead>
			<tbody>
				{#each c.mounts as m (m.target)}
					<tr>
						<td>{m.type}</td>
						<td><code>{m.source ?? '–'}</code></td>
						<td><code>{m.target}</code></td>
						<td>{m.read_only ? 'read-only' : 'read-write'}</td>
					</tr>
				{:else}
					<tr><td colspan="4" class="muted">No mounts.</td></tr>
				{/each}
			</tbody>
		</table>

		<h2>
			Environment
			<button class="link small-link" onclick={() => (showEnv = !showEnv)}>
				{showEnv ? 'hide values' : 'show values'}
			</button>
		</h2>
		<table>
			<tbody>
				{#each c.env as line (line)}
					{@const [key, ...rest] = line.split('=')}
					<tr>
						<td class="key"><code>{key}</code></td>
						<td><code>{showEnv ? rest.join('=') : '••••••'}</code></td>
					</tr>
				{:else}
					<tr><td class="muted">No variables.</td></tr>
				{/each}
			</tbody>
		</table>

		{#if Object.keys(c.labels).length}
			<h2>Labels</h2>
			<table>
				<tbody>
					{#each Object.entries(c.labels) as [key, value] (key)}
						<tr><td class="key"><code>{key}</code></td><td><code>{value}</code></td></tr>
					{/each}
				</tbody>
			</table>
		{/if}
	{/if}
{/if}

<style>
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(22rem, 1fr));
		gap: 1rem;
	}
	h3 {
		margin: 0 0 0.75rem;
		font-size: 0.8rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--muted);
	}
	.key {
		width: 30%;
		white-space: nowrap;
	}
	.small-link {
		font-size: 0.8rem;
		margin-left: 0.5rem;
	}
	td code {
		overflow-wrap: anywhere;
	}
</style>
