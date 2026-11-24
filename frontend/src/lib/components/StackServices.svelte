<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { api, errorMessage } from '../api';
	import { ago } from '../format';
	import { poll } from '../poll.svelte';
	import type { ServiceRow, StackContainers } from '../types';
	import Badge from './Badge.svelte';
	import Icon from './Icon.svelte';
	import Status from './Status.svelte';

	let { stackId, operator }: { stackId: string; operator: boolean } = $props();

	const data = poll<StackContainers>(() => `/stacks/${stackId}/containers`, 5000);
	let showHistory = $state(false);
	let busy = $state<string | undefined>();
	let message = $state<{ text: string; error: boolean } | undefined>();

	function link(ref: string, tab?: string): string {
		return `/stacks/${stackId}/containers/${ref}${tab ? `?tab=${tab}` : ''}`;
	}

	function replicaTone(s: ServiceRow): 'ok' | 'warn' | 'bad' {
		if (s.replicas_desired == null) return 'ok';
		if ((s.replicas_running ?? 0) >= s.replicas_desired) return 'ok';
		return s.replicas_running ? 'warn' : 'bad';
	}

	function replicas(s: ServiceRow): string {
		if (s.replicas_desired == null) return `${s.replicas_running ?? 0} running`;
		return `${s.replicas_running ?? 0}/${s.replicas_desired}`;
	}

	async function restart(s: ServiceRow) {
		if (!confirm(`Restart ${s.name}?`)) return;
		busy = s.name;
		message = undefined;
		try {
			await api.post(`/stacks/${stackId}/services/${encodeURIComponent(s.name)}/restart`);
			message = { text: `Restarting ${s.name}.`, error: false };
			await data.refresh();
		} catch (e) {
			message = { text: errorMessage(e), error: true };
		} finally {
			busy = undefined;
		}
	}
</script>

<div class="heading">
	<h2>Services</h2>
	{#if data.data?.type === 'swarm'}
		<label class="muted"><input type="checkbox" bind:checked={showHistory} /> Show stopped tasks</label>
	{/if}
</div>
{#if message}<p class:error={message.error} class:muted={!message.error}>{message.text}</p>{/if}
<Status loading={data.loading && !data.data} error={data.error} />

{#if data.data}
	{#if !data.data.agents}
		<p class="muted">Logs and consoles need the Novaport agent on this stack's hosts.</p>
	{/if}
	{#each data.data.services as s (s.full_name)}
		{@const rows = s.containers.filter((c) => c.current || showHistory)}
		<section class="service">
			<header>
				<div class="title">
					<Icon name="services" />
					<strong>{s.name}</strong>
					<Badge value={replicas(s)} tone={replicaTone(s)} />
					{#if s.update_status?.state && s.update_status.state !== 'completed'}
						<Badge value={`update ${s.update_status.state}`} />
					{/if}
				</div>
				<div class="meta">
					<code>{s.image}</code>
					{#if s.mode !== 'replicated' && s.mode !== 'compose'}<span class="muted">{s.mode}</span>{/if}
					{#each s.ports as p (`${p.published}/${p.target}/${p.protocol}`)}
						<span class="port">{p.published ?? '–'} → {p.target}/{p.protocol}</span>
					{/each}
				</div>
				<div class="toolbar">
					{#if s.service_id}
						<a class="button small" href="/services/{s.service_id}">
							<Icon name="info" size={14} />Service
						</a>
					{/if}
					{#if operator}
						<button class="small" onclick={() => restart(s)} disabled={busy === s.name}>
							<Icon name="restart" size={14} />Restart
						</button>
					{/if}
				</div>
			</header>
			<div class="scroll">
			<table>
				<thead>
					<tr>
						<th>Container</th>
						<th>State</th>
						<th>Node</th>
						<th>Status</th>
						<th>Updated</th>
						<th class="actions-col"></th>
					</tr>
				</thead>
				<tbody>
					{#each rows as c (c.ref)}
						<tr class:history={!c.current}>
							<td><a href={link(c.ref)}>{c.name}</a></td>
							<td>
								<Badge value={c.state} />
								{#if c.health}<Badge value={c.health} />{/if}
							</td>
							<td>{c.node ?? '–'}</td>
							<td class="status">
								{#if c.error}<span class="error">{c.error}</span>
								{:else}<span class="muted">{c.status ?? ''}</span>{/if}
							</td>
							<td class="muted">{ago(c.updated_at)}</td>
							<td class="actions-col">
								<div class="toolbar">
									<a class="button small" href={link(c.ref, 'logs')} title="Logs">
										<Icon name="logs" size={14} />Logs
									</a>
									{#if operator && c.state === 'running' && data.data.agents}
										<a class="button small" href={link(c.ref, 'console')} title="Console">
											<Icon name="console" size={14} />Console
										</a>
									{/if}
									<a class="button small" href={link(c.ref)} title="Info">
										<Icon name="info" size={14} />Info
									</a>
								</div>
							</td>
						</tr>
					{:else}
						<tr><td colspan="6" class="muted">No containers.</td></tr>
					{/each}
				</tbody>
			</table>
			</div>
		</section>
	{:else}
		<p class="muted">Nothing of this stack is running.</p>
	{/each}
{/if}

<style>
	.heading {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 1rem;
	}
	.heading label {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		font-size: 0.85rem;
	}
	.service {
		background: var(--panel);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow: var(--shadow);
		margin-bottom: 1rem;
		overflow: hidden;
	}
	.service header {
		display: grid;
		grid-template-columns: auto 1fr auto;
		align-items: center;
		gap: 0.5rem 1rem;
		padding: 0.75rem 1rem;
		border-bottom: 1px solid var(--border);
	}
	.title {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}
	.title :global(svg) {
		color: var(--muted);
	}
	.meta {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		flex-wrap: wrap;
		min-width: 0;
	}
	.port {
		font-size: 0.8rem;
		color: var(--muted);
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 0 0.35rem;
	}
	.service table {
		border: none;
		border-radius: 0;
		box-shadow: none;
	}
	.status {
		max-width: 28rem;
		overflow-wrap: anywhere;
	}
	.actions-col {
		width: 1%;
	}
	.actions-col .toolbar {
		flex-wrap: nowrap;
		justify-content: flex-end;
	}
	.scroll {
		overflow-x: auto;
	}
	tr.history td {
		opacity: 0.6;
	}
	@media (max-width: 900px) {
		.service header {
			grid-template-columns: 1fr;
		}
	}
</style>
