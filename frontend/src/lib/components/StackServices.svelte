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
	// What the user opened or closed; services they haven't touched follow `openByDefault`.
	let toggled = $state<Record<string, boolean>>({});
	let showStopped = $state<Record<string, boolean>>({});
	let busy = $state<string | undefined>();
	let message = $state<{ text: string; error: boolean } | undefined>();

	function healthy(s: ServiceRow): boolean {
		return s.replicas_desired == null || (s.replicas_running ?? 0) >= s.replicas_desired;
	}

	const RECENT_MS = 10 * 60_000;
	const FAILED = ['failed', 'rejected', 'exited', 'dead'];

	// Swarm counts a crash-looping task as running between restarts; its failed tasks give it away.
	function restarts(s: ServiceRow): number {
		const since = Date.now() - RECENT_MS;
		return s.containers.filter(
			(c) =>
				!c.current &&
				FAILED.includes(c.state) &&
				c.updated_at != null &&
				new Date(c.updated_at).getTime() > since
		).length;
	}

	function isOpen(s: ServiceRow, count: number): boolean {
		return toggled[s.full_name] ?? (count === 1 || !healthy(s) || restarts(s) > 0);
	}

	function replicas(s: ServiceRow): string {
		if (s.replicas_desired == null) return `${s.replicas_running ?? 0} running`;
		return `${s.replicas_running ?? 0}/${s.replicas_desired}`;
	}

	function link(ref: string, tab?: string): string {
		return `/stacks/${stackId}/containers/${ref}${tab ? `?tab=${tab}` : ''}`;
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

{#if message}<p class:error={message.error} class:muted={!message.error}>{message.text}</p>{/if}
<Status loading={data.loading && !data.data} error={data.error} />

{#if data.data}
	{@const services = data.data.services}
	{#if !data.data.agents}
		<p class="muted">Logs and consoles need the Novaport agent on this stack's hosts.</p>
	{/if}
	<div class="list">
		{#each services as s (s.full_name)}
			{@const open = isOpen(s, services.length)}
			{@const current = s.containers.filter((c) => c.current)}
			{@const stopped = s.containers.filter((c) => !c.current)}
			{@const failing = current.filter((c) => c.error).length}
			<section class="service" class:open>
				<div class="row">
					<button
						class="toggle"
						aria-expanded={open}
						onclick={() => (toggled[s.full_name] = !open)}
					>
						<span class="chevron"><Icon name="chevron" size={14} /></span>
						<strong>{s.name}</strong>
						<Badge value={replicas(s)} tone={healthy(s) ? 'ok' : s.replicas_running ? 'warn' : 'bad'} />
						{#if failing}<span class="error small">{failing} failing</span>{/if}
						{#if restarts(s)}
							<Badge value={`${restarts(s)} restart${restarts(s) === 1 ? '' : 's'} in 10 min`} tone="warn" />
						{/if}
						{#if s.update_status?.state && s.update_status.state !== 'completed'}
							<Badge value={`update ${s.update_status.state}`} />
						{/if}
					</button>
					<code class="image">{s.image}</code>
					<span class="ports">
						{#each s.ports as p (`${p.published}/${p.target}/${p.protocol}`)}
							<span class="port">{p.published ?? '–'}→{p.target}{p.protocol === 'tcp' ? '' : `/${p.protocol}`}</span>
						{/each}
					</span>
					<span class="toolbar">
						{#if s.service_id}
							<a class="button small" href="/services/{s.service_id}" title="Service details">
								<Icon name="info" size={14} />
							</a>
						{/if}
						{#if operator}
							<button
								class="small"
								onclick={() => restart(s)}
								disabled={busy === s.name}
								title="Restart {s.name}"
							>
								<Icon name="restart" size={14} />
							</button>
						{/if}
					</span>
				</div>

				{#if open}
					<div class="containers">
						{#each [...current, ...(showStopped[s.full_name] ? stopped : [])] as c (c.ref)}
							<div class="container" class:stopped={!c.current}>
								<a class="name" href={link(c.ref)}>{c.name}</a>
								<span class="state">
									<Badge value={c.state} />
									{#if c.health}<Badge value={c.health} />{/if}
								</span>
								<span class="muted node">{c.node ?? ''}</span>
								<span class="detail">
									{#if c.error}<span class="error">{c.error}</span>
									{:else}<span class="muted">{ago(c.updated_at)}</span>{/if}
								</span>
								<span class="toolbar">
									<a class="button small" href={link(c.ref, 'logs')}>
										<Icon name="logs" size={14} />Logs
									</a>
									{#if operator && c.state === 'running' && data.data.agents}
										<a class="button small" href={link(c.ref, 'console')}>
											<Icon name="console" size={14} />Console
										</a>
									{/if}
								</span>
							</div>
						{:else}
							<p class="muted empty">No running containers.</p>
						{/each}
						{#if stopped.length}
							<button
								class="link more"
								onclick={() => (showStopped[s.full_name] = !showStopped[s.full_name])}
							>
								{showStopped[s.full_name] ? 'Hide' : 'Show'}
								{stopped.length} stopped
							</button>
						{/if}
					</div>
				{/if}
			</section>
		{:else}
			<p class="muted">Nothing of this stack is running.</p>
		{/each}
	</div>
{/if}

<style>
	.list {
		background: var(--panel);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow: var(--shadow);
		overflow: hidden;
	}
	.service + .service {
		border-top: 1px solid var(--border);
	}
	.row {
		display: grid;
		grid-template-columns: minmax(14rem, auto) minmax(0, 1fr) auto auto;
		align-items: center;
		gap: 1rem;
		padding: 0.55rem 0.8rem 0.55rem 0.5rem;
	}
	.row:hover {
		background: var(--panel-alt);
	}
	.toggle {
		border: none;
		background: none;
		padding: 0.15rem 0.3rem;
		gap: 0.55rem;
		justify-content: flex-start;
	}
	.toggle:hover:not(:disabled) {
		background: none;
	}
	.chevron {
		display: inline-flex;
		color: var(--muted);
		transition: transform 0.12s;
	}
	.open .chevron {
		transform: rotate(90deg);
	}
	.image {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		justify-self: start;
		max-width: 100%;
	}
	.ports {
		display: flex;
		gap: 0.35rem;
	}
	.port {
		font-size: 0.78rem;
		color: var(--muted);
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 0 0.35rem;
		white-space: nowrap;
	}
	.small {
		font-size: 0.82rem;
	}
	.toolbar {
		display: flex;
		gap: 0.35rem;
		flex-wrap: nowrap;
		justify-content: flex-end;
	}
	.containers {
		padding: 0.15rem 0.8rem 0.6rem 2.4rem;
		background: var(--panel-alt);
		border-top: 1px solid var(--border);
	}
	.container {
		display: grid;
		grid-template-columns: minmax(10rem, 14rem) minmax(7rem, auto) minmax(5rem, 8rem) minmax(0, 1fr) auto;
		align-items: center;
		gap: 1rem;
		padding: 0.4rem 0;
		border-bottom: 1px solid var(--border);
	}
	.container:last-of-type {
		border-bottom: none;
	}
	.container.stopped {
		opacity: 0.6;
	}
	.name {
		font-weight: 500;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.state {
		display: flex;
		gap: 0.3rem;
	}
	.detail {
		min-width: 0;
		overflow-wrap: anywhere;
		font-size: 0.85rem;
	}
	.more {
		margin-top: 0.35rem;
		font-size: 0.85rem;
	}
	.empty {
		margin: 0.5rem 0;
	}
	@media (max-width: 900px) {
		.row {
			grid-template-columns: 1fr auto;
		}
		.image,
		.ports {
			display: none;
		}
		.containers {
			padding-left: 0.8rem;
		}
		.container {
			grid-template-columns: 1fr auto;
		}
		.node,
		.detail {
			display: none;
		}
	}
</style>
