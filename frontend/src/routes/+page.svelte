<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import Badge from '#lib/components/Badge.svelte';
	import DeploymentRow from '#lib/components/DeploymentRow.svelte';
	import Status from '#lib/components/Status.svelte';
	import { ago, bytes, degraded, replicas } from '#lib/format.ts';
	import { poll } from '#lib/poll.svelte.ts';
	import type { Deployment, Node, ServiceSummary, SystemInfo, Task } from '#lib/types.ts';

	const system = poll<SystemInfo>(() => '/system');
	const isManager = $derived(system.data?.swarm.is_manager ?? false);

	const services = poll<ServiceSummary[]>(() => '/services');
	const nodes = poll<Node[]>(() => '/nodes');
	const tasks = poll<Task[]>(() => '/tasks');
	const deployments = poll<Deployment[]>(() => '/deployments?limit=10', 5000);
	const waiting = $derived(
		(deployments.data ?? []).filter((d) => d.status === 'awaiting_approval')
	);

	const troubled = $derived((services.data ?? []).filter(degraded));
	const badNodes = $derived((nodes.data ?? []).filter((n) => n.state !== 'ready'));
	const serviceNames = $derived(new Map((services.data ?? []).map((s) => [s.id, s.name])));
	// Errors from the last hour, newest first (the API already sorts).
	const recentErrors = $derived(
		(tasks.data ?? [])
			.filter((t) => t.error && Date.now() - new Date(t.updated_at).getTime() < 3_600_000)
			.slice(0, 15)
	);
</script>

<h1>Overview</h1>
<Status loading={system.loading && !system.data} error={system.error} />

{#if system.data}
	{@const s = system.data}
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
{/if}

{#if waiting.length}
	<h2>Waiting for approval</h2>
	<table>
		<tbody>
			{#each waiting as d (d.id)}<DeploymentRow {d} showStack />{/each}
		</tbody>
	</table>
{/if}

{#if deployments.data?.length}
	<h2>Recent deployments</h2>
	<table>
		<tbody>
			{#each deployments.data as d (d.id)}<DeploymentRow {d} showStack />{/each}
		</tbody>
	</table>
{/if}

{#if isManager}
	<h2>Needs attention</h2>
	{#if troubled.length === 0 && badNodes.length === 0 && recentErrors.length === 0}
		<p class="muted">Nothing wrong right now.</p>
	{/if}

	{#if badNodes.length}
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

<style>
	.nowrap {
		white-space: nowrap;
	}
	table + h2 {
		margin-top: 1.5rem;
	}
</style>
