<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { api, errorMessage } from '../api';
	import { ago } from '../format';
	import type { ContainerRow, ServiceRow, StackContainers } from '../types';
	import Badge from './Badge.svelte';
	import Icon from './Icon.svelte';

	let {
		stackId,
		data,
		operator,
		onchange
	}: {
		stackId: string;
		data: StackContainers;
		operator: boolean;
		onchange: () => void;
	} = $props();

	const RECENT_MS = 10 * 60_000;

	let onlyRunning = $state(true);
	// What the user opened or closed; untouched services follow `openByDefault`.
	let toggled = $state<Record<string, boolean>>({});
	let busy = $state<string | undefined>();
	let message = $state<{ text: string; error: boolean } | undefined>();

	function healthy(s: ServiceRow): boolean {
		return s.replicas_desired == null || (s.replicas_running ?? 0) >= s.replicas_desired;
	}

	function recentFailures(s: ServiceRow): number {
		const since = Date.now() - RECENT_MS;
		return s.containers.filter(
			(c) =>
				!c.current &&
				['failed', 'rejected'].includes(c.state) &&
				c.updated_at != null &&
				new Date(c.updated_at).getTime() > since
		).length;
	}

	function updating(s: ServiceRow): boolean {
		return ['updating', 'rollback_started'].includes(s.update_status?.state ?? '');
	}

	function isOpen(s: ServiceRow): boolean {
		return (
			toggled[s.full_name] ??
			(data.services.length === 1 || !healthy(s) || recentFailures(s) > 0 || updating(s))
		);
	}

	// Tasks being replaced still run until Swarm stops them, so they count as running.
	function shown(s: ServiceRow): ContainerRow[] {
		return onlyRunning
			? s.containers.filter((c) => c.current || c.state === 'running')
			: s.containers;
	}

	function scheduling(s: ServiceRow): string {
		if (s.mode === 'compose') return `${s.replicas_running ?? 0}/${s.replicas_desired ?? 0}`;
		const count =
			s.replicas_desired == null
				? `${s.replicas_running ?? 0}`
				: `${s.replicas_running ?? 0}/${s.replicas_desired}`;
		return `${s.mode} ${count}`;
	}

	function link(ref: string, tab?: string): string {
		return `/stacks/${stackId}/containers/${ref}${tab ? `?tab=${tab}` : ''}`;
	}

	function lastUpdate(s: ServiceRow): string | null {
		const times = s.containers.map((c) => c.updated_at).filter((t): t is string => t != null);
		return times.length ? times.reduce((a, b) => (a > b ? a : b)) : null;
	}

	async function restart(s: ServiceRow) {
		if (!confirm(`Restart ${s.name}?`)) return;
		busy = s.name;
		message = undefined;
		try {
			await api.post(`/stacks/${stackId}/services/${encodeURIComponent(s.name)}/restart`);
			message = { text: `Restarting ${s.name}.`, error: false };
			onchange();
		} catch (e) {
			message = { text: errorMessage(e), error: true };
		} finally {
			busy = undefined;
		}
	}
</script>

<div class="bar">
	<span class="muted">
		{data.services.length} service{data.services.length === 1 ? '' : 's'}
		{#if !data.agents}· logs and consoles need the Novaport agent on this stack's hosts{/if}
	</span>
	<label class="switch">
		<input type="checkbox" bind:checked={onlyRunning} />
		<span class="track"><span class="thumb"></span></span>
		Only running tasks
	</label>
</div>
{#if message}<p class:error={message.error} class:muted={!message.error}>{message.text}</p>{/if}

<div class="scroll">
	<table class="services">
		<thead>
			<tr>
				<th class="expand"></th>
				<th>Name</th>
				<th>Image</th>
				<th>Scheduling</th>
				<th>Ports</th>
				<th>Updated</th>
				<th class="actions"></th>
			</tr>
		</thead>
		<tbody>
			{#each data.services as s (s.full_name)}
				{@const open = isOpen(s)}
				{@const failures = recentFailures(s)}
				<tr class="service" class:open>
					<td class="expand">
						<button
							class="chevron"
							aria-expanded={open}
							aria-label="{open ? 'Hide' : 'Show'} tasks of {s.name}"
							onclick={() => (toggled[s.full_name] = !open)}
						>
							<Icon name="chevron" size={14} />
						</button>
					</td>
					<td>
						<button class="name" onclick={() => (toggled[s.full_name] = !open)}>{s.name}</button>
						{#if failures}
							<Badge value="restarting" tone="bad" />
						{/if}
						{#if s.update_status?.state && s.update_status.state !== 'completed'}
							<Badge value={`update ${s.update_status.state}`} />
						{/if}
					</td>
					<td><code>{s.image}</code></td>
					<td>
						<Badge
							value={scheduling(s)}
							tone={healthy(s) ? (failures ? 'warn' : 'ok') : s.replicas_running ? 'warn' : 'bad'}
						/>
					</td>
					<td>
						{#each s.ports as p (`${p.published}/${p.target}/${p.protocol}`)}
							<span class="port">{p.published ?? '–'}:{p.target}{p.protocol === 'tcp' ? '' : `/${p.protocol}`}</span>
						{:else}<span class="muted">–</span>{/each}
					</td>
					<td class="muted">{ago(lastUpdate(s))}</td>
					<td class="actions">
						<span class="icons">
							{#if s.service_id}
								<a class="icon" href="/services/{s.service_id}" title="Service details">
									<Icon name="info" size={15} />
								</a>
							{/if}
							{#if operator}
								<button
									class="icon"
									onclick={() => restart(s)}
									disabled={busy === s.name}
									title="Restart {s.name}"
								>
									<Icon name="restart" size={15} />
								</button>
							{/if}
						</span>
					</td>
				</tr>
				{#if open}
					<tr class="tasks-row">
						<td></td>
						<td colspan="6">
							<table class="tasks">
								<thead>
									<tr>
										<th>Status</th>
										<th>Task</th>
										<th>Actions</th>
										<th>Node</th>
										<th>Last update</th>
										<th>Message</th>
									</tr>
								</thead>
								<tbody>
									{#each shown(s) as c (c.ref)}
										<tr class:history={!c.current}>
											<td>
												<Badge value={c.state} />
												{#if c.health}<Badge value={c.health} />{/if}
											</td>
											<td><a href={link(c.ref)}>{c.name}</a></td>
											<td>
												<span class="icons">
													<a class="icon" href={link(c.ref, 'logs')} title="Logs">
														<Icon name="logs" size={15} />
													</a>
													<a class="icon" href={link(c.ref)} title="Inspect">
														<Icon name="info" size={15} />
													</a>
													{#if operator && c.state === 'running' && data.agents}
														<a class="icon" href={link(c.ref, 'console')} title="Console">
															<Icon name="console" size={15} />
														</a>
													{/if}
												</span>
											</td>
											<td>{c.node ?? '–'}</td>
											<td class="muted">{ago(c.updated_at)}</td>
											<td class="message">
												{#if c.error}<span class="error">{c.error}</span>
												{:else}<span class="muted">{c.status ?? ''}</span>{/if}
											</td>
										</tr>
									{:else}
										<tr>
											<td colspan="6" class="muted">
												No running tasks{onlyRunning && s.containers.length
													? '. Turn off "Only running tasks" to see stopped ones.'
													: '.'}
											</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</td>
					</tr>
				{/if}
			{:else}
				<tr><td colspan="7" class="muted">Nothing of this stack is running.</td></tr>
			{/each}
		</tbody>
	</table>
</div>

<style>
	.bar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		margin-bottom: 0.6rem;
	}
	.switch {
		display: inline-flex;
		align-items: center;
		gap: 0.5rem;
		cursor: pointer;
		user-select: none;
	}
	.switch input {
		position: absolute;
		opacity: 0;
		pointer-events: none;
	}
	.track {
		width: 30px;
		height: 17px;
		border-radius: 999px;
		background: var(--border-strong);
		position: relative;
		transition: background 0.15s;
	}
	.thumb {
		position: absolute;
		top: 2px;
		left: 2px;
		width: 13px;
		height: 13px;
		border-radius: 50%;
		background: #fff;
		transition: transform 0.15s;
	}
	.switch input:checked + .track {
		background: var(--accent);
	}
	.switch input:checked + .track .thumb {
		transform: translateX(13px);
	}
	.switch input:focus-visible + .track {
		outline: 2px solid var(--accent);
		outline-offset: 2px;
	}
	.scroll {
		overflow-x: auto;
	}
	.expand {
		width: 2.2rem;
		padding-right: 0;
	}
	.chevron,
	.name {
		border: none;
		background: none;
		padding: 0.15rem;
		color: var(--muted);
	}
	.chevron:hover:not(:disabled),
	.name:hover:not(:disabled) {
		background: none;
	}
	.chevron :global(svg) {
		transition: transform 0.12s;
	}
	.open .chevron :global(svg) {
		transform: rotate(90deg);
	}
	.name {
		color: var(--fg);
		font-weight: 600;
		margin-right: 0.4rem;
	}
	.port {
		font-family: ui-monospace, monospace;
		font-size: 0.8rem;
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 0 0.35rem;
		margin-right: 0.25rem;
		white-space: nowrap;
	}
	.actions {
		width: 1%;
		white-space: nowrap;
	}
	.icons {
		display: inline-flex;
		gap: 0.15rem;
	}
	.icon {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 1.8rem;
		height: 1.8rem;
		padding: 0;
		border: none;
		border-radius: 6px;
		background: none;
		color: var(--muted);
	}
	.icon:hover:not(:disabled) {
		background: var(--accent-soft);
		color: var(--accent);
		text-decoration: none;
	}
	tr.service.open td {
		border-bottom-color: transparent;
	}
	.tasks-row > td {
		padding-top: 0;
		padding-bottom: 0.9rem;
	}
	.tasks-row:hover td {
		background: none;
	}
	table.tasks {
		box-shadow: none;
		background: var(--panel-alt);
	}
	table.tasks th {
		background: transparent;
	}
	.message {
		max-width: 32rem;
		overflow-wrap: anywhere;
	}
	tr.history td {
		opacity: 0.65;
	}
</style>
