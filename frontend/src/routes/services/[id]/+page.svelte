<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { page } from '$app/state';
	import { api, errorMessage } from '#lib/api.ts';
	import Crumbs from '#lib/components/Crumbs.svelte';
	import Icon from '#lib/components/Icon.svelte';
	import Badge from '#lib/components/Badge.svelte';
	import LogViewer from '#lib/components/LogViewer.svelte';
	import Status from '#lib/components/Status.svelte';
	import { ago, degraded, replicas, shortId } from '#lib/format.ts';
	import { poll } from '#lib/poll.svelte.ts';
	import { can } from '#lib/session.svelte.ts';
	import type { Service, Stack, Task } from '#lib/types.ts';

	const id = $derived(page.params.id ?? '');
	const service = poll<Service>(() => `/services/${id}`);
	const tasks = poll<Task[]>(() => `/services/${id}/tasks`, 5000);

	const stacks = poll<Stack[]>(() => '/stacks', 60_000);
	const stackId = $derived(stacks.data?.find((st) => st.name === service.data?.stack)?.id);

	let restarting = $state(false);
	let restartError = $state<string | undefined>();

	async function restart() {
		if (!service.data || !confirm(`Restart ${service.data.name}?`)) return;
		restarting = true;
		restartError = undefined;
		try {
			await api.post(`/services/${id}/restart`);
			await tasks.refresh();
		} catch (e) {
			restartError = errorMessage(e);
		} finally {
			restarting = false;
		}
	}

	let showHistory = $state(false);
	let showEnv = $state(false);

	const shownTasks = $derived(
		(tasks.data ?? []).filter((t) => showHistory || t.desired_state === 'running' || t.error)
	);
</script>

<Crumbs items={[{ href: '/services', label: 'Services' }]} />
<Status loading={service.loading && !service.data} error={service.error} />

{#if service.data}
	{@const s = service.data}
	<div class="page-header">
		<div>
			<h1>{s.name}</h1>
			{#if s.stack}
				<p class="subtitle">
					stack {#if stackId}<a href="/stacks/{stackId}">{s.stack}</a>{:else}{s.stack}{/if}
				</p>
			{/if}
		</div>
		{#if can('operator', stackId)}
			<div class="toolbar">
				<button onclick={restart} disabled={restarting}>
					<Icon name="restart" size={14} />Restart
				</button>
			</div>
		{/if}
	</div>
	{#if restartError}<p class="error">{restartError}</p>{/if}

	<div class="panel">
		<dl class="kv">
			<dt>Image</dt>
			<dd><code>{s.image}</code></dd>
			<dt>Mode</dt>
			<dd>{s.mode}</dd>
			<dt>Replicas</dt>
			<dd>
				{#if degraded(s)}<Badge value={replicas(s)} tone="bad" />{:else}{replicas(s)}{/if}
			</dd>
			{#if s.update_status}
				<dt>Last update</dt>
				<dd>
					<Badge value={s.update_status.state ?? 'unknown'} />
					{s.update_status.message ?? ''}
				</dd>
			{/if}
			<dt>Restart</dt>
			<dd>{s.restart_condition ?? 'any'}</dd>
			{#if s.command || s.args}
				<dt>Command</dt>
				<dd><code>{[...(s.command ?? []), ...(s.args ?? [])].join(' ')}</code></dd>
			{/if}
			<dt>Ports</dt>
			<dd>
				{#each s.ports as p, i (i)}
					<div>{p.published ?? '–'} → {p.target}/{p.protocol} ({p.mode})</div>
				{:else}<span class="muted">none</span>{/each}
			</dd>
			<dt>Placement</dt>
			<dd>
				{#each s.constraints as c (c)}<div><code>{c}</code></div>{:else}<span class="muted"
						>any node</span
					>{/each}
			</dd>
			<dt>Networks</dt>
			<dd>
				{#each s.networks as n (n.id)}
					<div>
						{n.name ?? shortId(n.id)}
						{#if n.aliases.length}<span class="muted">({n.aliases.join(', ')})</span>{/if}
					</div>
				{:else}<span class="muted">none</span>{/each}
			</dd>
			<dt>Secrets</dt>
			<dd>{s.secrets.join(', ') || '–'}</dd>
			<dt>Configs</dt>
			<dd>{s.configs.join(', ') || '–'}</dd>
			<dt>Updated</dt>
			<dd>{ago(s.updated_at)}</dd>
		</dl>
	</div>

	<h2>Mounts</h2>
	{#if s.mounts.length}
		<table>
			<thead><tr><th>Type</th><th>Source</th><th>Target</th><th>Mode</th></tr></thead>
			<tbody>
				{#each s.mounts as m, i (i)}
					<tr>
						<td>{m.type}</td>
						<td><code>{m.source ?? '(anonymous)'}</code></td>
						<td><code>{m.target}</code></td>
						<td>{m.read_only ? 'read-only' : 'read-write'}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{:else}
		<p class="muted">No mounts.</p>
	{/if}

	<h2>
		Tasks
		<label class="toggle"><input type="checkbox" bind:checked={showHistory} /> show history</label>
	</h2>
	<Status loading={tasks.loading && !tasks.data} error={tasks.error} />
	<table>
		<thead>
			<tr>
				<th>Slot</th>
				<th>Node</th>
				<th>State</th>
				<th>Desired</th>
				<th>Message</th>
				<th>Updated</th>
				<th></th>
			</tr>
		</thead>
		<tbody>
			{#each shownTasks as t (t.id)}
				<tr class:failed={t.error}>
					<td>{t.slot ?? '–'}</td>
					<td>{t.node ?? '–'}</td>
					<td><Badge value={t.state} /></td>
					<td class="muted">{t.desired_state}</td>
					<td>
						{#if t.error}<div class="error">{t.error}</div>{/if}
						<span class="muted">
							{t.message ?? ''}{#if t.exit_code}{' '}· exit {t.exit_code}{/if}
						</span>
					</td>
					<td class="muted">{ago(t.updated_at)}</td>
					<td class="nowrap">
						{#if t.container_id}
							<a href="/tasks/{t.id}">logs</a>
							{#if t.state === 'running'}· <a href="/tasks/{t.id}/terminal">terminal</a>{/if}
						{/if}
					</td>
				</tr>
			{:else}
				<tr><td colspan="7" class="muted">No tasks.</td></tr>
			{/each}
		</tbody>
	</table>

	<h2>Logs</h2>
	<LogViewer source={`/services/${s.id}`} />

	<h2>
		Environment
		<button class="link" onclick={() => (showEnv = !showEnv)}>{showEnv ? 'hide' : 'show'}</button>
	</h2>
	{#if showEnv}
		<pre class="panel">{s.env.join('\n') || 'No environment variables.'}</pre>
	{/if}

	{#if Object.keys(s.labels).length}
		<h2>Labels</h2>
		<table>
			<tbody>
				{#each Object.entries(s.labels) as [k, v] (k)}
					<tr><td><code>{k}</code></td><td><code>{v}</code></td></tr>
				{/each}
			</tbody>
		</table>
	{/if}
{/if}

<style>
	.toggle {
		font-weight: 400;
		font-size: 0.85rem;
		margin-left: 1rem;
		color: var(--muted);
	}
	h2 .link {
		font-size: 0.85rem;
		font-weight: 400;
		margin-left: 0.75rem;
	}
	.nowrap {
		white-space: nowrap;
	}
	tr.failed td {
		background: color-mix(in srgb, var(--bad) 6%, transparent);
	}
	pre.panel {
		white-space: pre-wrap;
	}
</style>
