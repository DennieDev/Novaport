<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import Badge from '#lib/components/Badge.svelte';
	import DeploymentRow from '#lib/components/DeploymentRow.svelte';
	import ImageUpdates from '#lib/components/ImageUpdates.svelte';
	import Status from '#lib/components/Status.svelte';
	import { ago, bytes, degraded, replicas } from '#lib/format.ts';
	import { STACK_TOPICS } from '#lib/live.svelte.ts';
	import { poll } from '#lib/poll.svelte.ts';
	import { can } from '#lib/session.svelte.ts';
	import type {
		Deployment,
		ImageCheck,
		Node,
		ServiceSummary,
		Stack,
		SystemInfo,
		Task
	} from '#lib/types.ts';

	// Cluster-wide views need a global role; with only stack roles, show stacks and deployments.
	const cluster = can('viewer');
	const system = cluster ? poll<SystemInfo>(() => '/system') : undefined;
	const isManager = $derived(system?.data?.swarm.is_manager ?? false);

	const services = poll<ServiceSummary[]>(() => '/services');
	const nodes = cluster ? poll<Node[]>(() => '/nodes') : undefined;
	const tasks = cluster ? poll<Task[]>(() => '/tasks') : undefined;
	const deployments = poll<Deployment[]>(() => '/deployments?limit=5', 5000, {
		topics: ['deployments']
	});
	const waiting = $derived(
		(deployments.data ?? []).filter((d) => d.status === 'awaiting_approval')
	);

	const updates = poll<ImageCheck[]>(() => '/updates', 60_000, { topics: ['image_checks'] });
	const stacks = poll<Stack[]>(() => '/stacks', 60_000, { topics: STACK_TOPICS });
	const drifted = $derived((stacks.data ?? []).filter((s) => s.drift?.length));
	const pendingUpdates = $derived(
		(updates.data ?? []).filter((c) => c.status === 'available' && c.action_taken !== 'committed')
	);

	const troubled = $derived((services.data ?? []).filter(degraded));
	const badNodes = $derived((nodes?.data ?? []).filter((n) => n.state !== 'ready'));
	const serviceNames = $derived(new Map((services.data ?? []).map((s) => [s.id, s.name])));
	// Errors from the last hour, newest first (the API already sorts).
	const recentErrors = $derived(
		(tasks?.data ?? [])
			.filter((t) => t.error && Date.now() - new Date(t.updated_at).getTime() < 3_600_000)
			.slice(0, 15)
	);
</script>

<h1>Overview</h1>

<div class="tiles">
	<a class="tile" href="/stacks">
		<span class="label">Stacks</span>
		<span class="value">{stacks.data?.length ?? '–'}</span>
		<span class="hint" class:warn={drifted.length}>
			{drifted.length ? `${drifted.length} drifted` : 'all as deployed'}
		</span>
	</a>
	<a class="tile" href="/services">
		<span class="label">Services</span>
		<span class="value">{services.data?.length ?? '–'}</span>
		<span class="hint" class:bad={troubled.length}>
			{troubled.length ? `${troubled.length} degraded` : 'all running'}
		</span>
	</a>
	{#if nodes}
		<a class="tile" href="/nodes">
			<span class="label">Nodes</span>
			<span class="value">{nodes.data?.length ?? '–'}</span>
			<span class="hint" class:bad={badNodes.length}>
				{badNodes.length ? `${badNodes.length} not ready` : 'all ready'}
			</span>
		</a>
	{/if}
	<a class="tile" href="#approvals">
		<span class="label">Waiting for approval</span>
		<span class="value">{waiting.length}</span>
		<span class="hint" class:warn={pendingUpdates.length}>
			{pendingUpdates.length} image update{pendingUpdates.length === 1 ? '' : 's'} pending
		</span>
	</a>
</div>

{#if system}
	<Status loading={system.loading && !system.data} error={system.error} />
{:else}
	<p class="muted">
		You have access to some stacks only. See them under <a href="/stacks">Stacks</a>.
	</p>
{/if}


{#if waiting.length}
	<h2 id="approvals">Waiting for approval</h2>
	<table>
		<tbody>
			{#each waiting as d (d.id)}<DeploymentRow {d} showStack />{/each}
		</tbody>
	</table>
{/if}

{#if drifted.length}
	<h2>Drifted stacks</h2>
	<table>
		<tbody>
			{#each drifted as s (s.id)}
				<tr>
					<td><a href="/stacks/{s.id}">{s.name}</a></td>
					<td class="muted">{s.drift?.[0]}{(s.drift?.length ?? 0) > 1 ? ` and ${(s.drift?.length ?? 0) - 1} more` : ''}</td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}

{#if pendingUpdates.length}
	<h2>Image updates</h2>
	<ImageUpdates checks={pendingUpdates} showStack />
{/if}


{#if isManager}
	{#if troubled.length === 0 && badNodes.length === 0 && recentErrors.length === 0}
		<h2>Cluster health</h2>
		<p class="muted">Nothing wrong right now.</p>
	{/if}

	{#if badNodes.length}
		<h2>Nodes not ready</h2>
		<table>
			<thead><tr><th>Node</th><th>State</th><th>Message</th></tr></thead>
			<tbody>
				{#each badNodes as n (n.id)}
					<tr>
						<td>{n.hostname}</td>
						<td><Badge value={n.state} /></td>
						<td>{n.message ?? ''}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}

	{#if troubled.length}
		<h2>Services below desired state</h2>
		<table>
			<thead><tr><th>Service</th><th>Replicas</th><th>Update</th></tr></thead>
			<tbody>
				{#each troubled as svc (svc.id)}
					<tr>
						<td><a href="/services/{svc.id}">{svc.name}</a></td>
						<td>{replicas(svc)}</td>
						<td>
							{#if svc.update_status?.state}
								<Badge value={svc.update_status.state} />
								{svc.update_status.message ?? ''}
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}

	{#if recentErrors.length}
		<h2>Task errors in the last hour</h2>
		<table>
			<thead><tr><th>When</th><th>Service</th><th>Node</th><th>State</th><th>Error</th></tr></thead>
			<tbody>
				{#each recentErrors as t (t.id)}
					<tr>
						<td class="nowrap">{ago(t.updated_at)}</td>
						<td>
							<a href="/services/{t.service_id}">{serviceNames.get(t.service_id) ?? t.service_id}</a>
						</td>
						<td>{t.node ?? '–'}</td>
						<td><Badge value={t.state} /></td>
						<td class="error">{t.error}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
{/if}

{#if deployments.data?.length}
	<h2>Recent deployments</h2>
	<table>
		<tbody>
			{#each deployments.data as d (d.id)}<DeploymentRow {d} showStack />{/each}
		</tbody>
	</table>
{/if}

{#if system?.data}
	{@const s = system.data}
	<details class="host">
		<summary>Host details</summary>
		<div class="panel">
			<dl class="kv">
				<dt>Host</dt>
				<dd>{s.name} · Docker {s.docker_version} · {s.os} ({s.architecture})</dd>
				<dt>Resources</dt>
				<dd>{s.cpus} CPUs · {bytes(s.memory_bytes)}</dd>
				<dt>Containers</dt>
				<dd>{s.containers_running} running of {s.containers} · {s.images} images</dd>
				<dt>Swarm</dt>
				<dd>
					<Badge value={s.swarm.state} />
					{#if s.swarm.is_manager}
						manager · {s.swarm.nodes} nodes, {s.swarm.managers} managers
					{:else if s.swarm.state === 'active'}
						worker; connect Novaport to a manager to see the cluster
					{/if}
					{#if s.swarm.error}<span class="error">{s.swarm.error}</span>{/if}
				</dd>
			</dl>
		</div>
	</details>
{/if}

<style>
	.host {
		margin-top: 2rem;
	}
	.host summary {
		cursor: pointer;
		color: var(--muted);
		margin-bottom: 0.6rem;
	}
	.tiles {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
		gap: 0.75rem;
		margin-bottom: 1.25rem;
	}
	.tile {
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
		padding: 0.9rem 1.1rem;
		background: var(--panel);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow: var(--shadow);
		color: var(--fg);
	}
	.tile:hover {
		border-color: var(--border-strong);
		text-decoration: none;
	}
	.tile .label {
		font-size: 0.75rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--muted);
	}
	.tile .value {
		font-size: 1.7rem;
		font-weight: 650;
		line-height: 1.2;
	}
	.tile .hint {
		font-size: 0.82rem;
		color: var(--muted);
	}
	.tile .hint.warn {
		color: var(--warn);
	}
	.tile .hint.bad {
		color: var(--bad);
	}
	.nowrap {
		white-space: nowrap;
	}
	table + h2 {
		margin-top: 1.5rem;
	}
</style>
