<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { page } from '$app/state';
	import { api, errorMessage } from '#lib/api.ts';
	import Crumbs from '#lib/components/Crumbs.svelte';
	import Badge from '#lib/components/Badge.svelte';
	import DeployDialog from '#lib/components/DeployDialog.svelte';
	import DeployProgress from '#lib/components/DeployProgress.svelte';
	import DeploymentRow from '#lib/components/DeploymentRow.svelte';
	import Icon from '#lib/components/Icon.svelte';
	import ImageUpdates from '#lib/components/ImageUpdates.svelte';
	import StackServices from '#lib/components/StackServices.svelte';
	import Status from '#lib/components/Status.svelte';
	import { isActive } from '#lib/deploy.ts';
	import { ago } from '#lib/format.ts';
	import { onStack, STACK_TOPICS } from '#lib/live.svelte.ts';
	import { poll } from '#lib/poll.svelte.ts';
	import { can } from '#lib/session.svelte.ts';
	import type {
		Deployment,
		DeployOptions,
		ImageCheck,
		Revision,
		Stack,
		StackContainers
	} from '#lib/types.ts';

	const id = $derived(page.params.id ?? '');
	const tab = $derived(page.url.searchParams.get('tab') ?? 'services');
	const stack = poll<Stack>(() => `/stacks/${id}`, 5000, {
		topics: STACK_TOPICS,
		match: onStack(() => id)
	});
	const revisions = poll<Revision[]>(() => `/stacks/${id}/revisions`, 15_000, {
		topics: ['revisions'],
		match: onStack(() => id)
	});
	const deployments = poll<Deployment[]>(() => `/stacks/${id}/deployments?limit=20`, 5000, {
		topics: ['deployments'],
		match: onStack(() => id)
	});
	const containers = poll<StackContainers>(() => `/stacks/${id}/containers`, 5000);
	const health = $derived(containers.data?.health);
	const updates = poll<ImageCheck[]>(() => `/stacks/${id}/updates`, 60_000, {
		topics: ['image_checks'],
		match: onStack(() => id)
	});
	const availableUpdates = $derived(
		(updates.data ?? []).filter((c) => c.status === 'available').length
	);
	let checking = $state(false);

	async function checkUpdates() {
		checking = true;
		error = undefined;
		try {
			await api.post(`/stacks/${id}/updates/check`);
			await Promise.all([updates.refresh(), deployments.refresh(), revisions.refresh()]);
		} catch (e) {
			error = errorMessage(e);
		} finally {
			checking = false;
		}
	}

	let error = $state<string | undefined>();
	let busy = $state(false);

	// The deployment shown above the services: one that is under way, or the last one watched
	// here until it is closed.
	let watching = $state<string | undefined>();
	const active = $derived(deployments.data?.find((d) => isActive(d.status)));
	$effect(() => {
		if (active && active.id !== watching) watching = active.id;
	});

	const ROLLOUT_REFRESH_MS = 2000;
	$effect(() => {
		if (!active) return;
		const timer = setInterval(() => containers.refresh(), ROLLOUT_REFRESH_MS);
		return () => {
			clearInterval(timer);
			containers.refresh();
			stack.refresh();
		};
	});

	let asking = $state(false);
	let toDeploy = $state<Revision | undefined>();
	const deployTarget = $derived(toDeploy ?? stack.data?.latest_revision ?? undefined);

	function deploy(revision?: Revision) {
		toDeploy = revision;
		asking = true;
	}

	async function confirmDeploy(options: DeployOptions) {
		busy = true;
		error = undefined;
		try {
			const d = await api.post<Deployment>(`/stacks/${id}/deploy`, {
				revision_id: toDeploy?.id ?? null,
				...options
			});
			asking = false;
			watching = d.id;
			await deployments.refresh();
		} catch (e) {
			asking = false;
			error = errorMessage(e);
		} finally {
			busy = false;
		}
	}

	let restarted = $state(false);

	async function restart() {
		const s = stack.data;
		if (!s || !confirm(`Restart every service of ${s.name}?`)) return;
		busy = true;
		error = undefined;
		restarted = false;
		try {
			await api.post(`/stacks/${id}/restart`);
			restarted = true;
		} catch (e) {
			error = errorMessage(e);
		} finally {
			busy = false;
		}
	}
</script>

<Crumbs items={[{ href: '/stacks', label: 'Stacks' }]} />

<DeployDialog
	bind:open={asking}
	title="Deploy {stack.data?.name ?? ''}"
	{busy}
	onconfirm={confirmDeploy}
>
	{#if deployTarget}
		<p>
			<code>{deployTarget.commit.slice(0, 10)}</code>
			{deployTarget.message}
			<span class="muted">· {deployTarget.author}, {ago(deployTarget.committed_at)}</span>
		</p>
		{#if toDeploy && toDeploy.id !== stack.data?.latest_revision?.id}
			<p class="muted">An older revision: this rolls the stack back to it.</p>
		{/if}
	{/if}
</DeployDialog>
<Status loading={stack.loading && !stack.data} error={stack.error} />

{#if stack.data}
	{@const s = stack.data}
	<div class="page-header">
		<div>
			<h1>
				{s.name}
				{#if health}<Badge value={health.state} />{/if}
				<Badge value={s.type === 'swarm' ? 'swarm' : 'compose'} tone="muted" />
				{#if s.removed_at}<Badge value="removed from git" tone="bad" />{/if}
				{#if s.drift?.length}<Badge value="drifted" tone="warn" />{/if}
			</h1>
			<p class="subtitle">
				<a href="/repositories/{s.repository.id}">{s.repository.name}</a> · <code>{s.path}</code>
				· {s.endpoint ?? 'local'} · deploys {s.deploy_mode} · edits by
				{s.edit_mode === 'pr' ? 'pull request' : 'commit'}
			</p>
		</div>
		<div class="toolbar">
			{#if can('operator', s.id)}
				<button
					class="primary"
					onclick={() => deploy()}
					disabled={busy || !!active || !s.latest_revision || !!s.removed_at}
				>
					<Icon name="deploy" size={14} />Deploy latest
				</button>
				<button onclick={restart} disabled={busy || !!s.removed_at}>
					<Icon name="restart" size={14} />Restart
				</button>
			{/if}
			{#if !s.removed_at}
				<a class="button" href="/stacks/{s.id}/edit">
					<Icon name="edit" size={14} />{can('editor', s.id) ? 'Edit files' : 'Files'}
				</a>
				<a class="button" href="/stacks/{s.id}/secrets"><Icon name="secrets" size={14} />Secrets</a>
			{/if}
			{#if can('admin')}
				<a class="button" href="/access/bindings?stack={s.id}"><Icon name="access" size={14} />Access</a>
			{/if}
		</div>
	</div>
	{#if error}<p class="error">{error}</p>{/if}
	{#if health?.problems.length}
		<div class="panel problems" class:down={health.state === 'down'}>
			<strong>{health.state === 'down' ? 'Not running' : 'Unhealthy'}</strong>
			{#each health.problems as problem (problem)}<span>{problem}</span>{/each}
		</div>
	{/if}
	{#if restarted}<p class="muted">Restart started.</p>{/if}

	{#if s.removed_at}
		<p class="error">
			This folder was removed from git {ago(s.removed_at)}. Its services keep running until you
			remove them.
		</p>
	{/if}
	{#if s.config_error}
		<pre class="panel error">{s.config_error}</pre>
		<p class="muted">New revisions are ignored until swarmer.yaml is fixed.</p>
	{/if}

	<div class="panel strip">
		<div>
			<span class="label">Running</span>
			{#if s.last_good_revision}
				<code>{s.last_good_revision.commit.slice(0, 10)}</code> {s.last_good_revision.message}
			{:else}<span class="muted">never deployed by Swarmer</span>{/if}
		</div>
		{#if s.latest_revision && s.latest_revision.id !== s.last_good_revision?.id}
			<div>
				<span class="label">Newer in git</span>
				<code>{s.latest_revision.commit.slice(0, 10)}</code> {s.latest_revision.message}
				<span class="muted">· {s.latest_revision.author}, {ago(s.latest_revision.committed_at)}</span>
			</div>
		{/if}
		{#if s.last_deployment}
			<div>
				<span class="label">Last deploy</span>
				<a href="/deployments/{s.last_deployment.id}">
					<Badge value={s.last_deployment.status.replace('_', ' ')} />
				</a>
				<span class="muted">{ago(s.last_deployment.created_at)}</span>
			</div>
		{/if}
	</div>

	{#if watching}
		{#key watching}
			<DeployProgress
				id={watching}
				operator={can('operator', s.id)}
				ondismiss={() => (watching = undefined)}
			/>
		{/key}
	{/if}

	{#if s.drift?.length}
		<div class="panel drift">
			<strong>Drifted from the last deploy</strong>
			<span class="muted">
				· checked {ago(s.drift_checked_at)}. Someone changed the running services outside
				Swarmer. Deploying again restores what git says.
			</span>
			<ul>
				{#each s.drift as line (line)}<li><code>{line}</code></li>{/each}
			</ul>
		</div>
	{/if}

	<nav class="tabs">
		<a href="?tab=services" class:active={tab === 'services'}>Services</a>
		<a href="?tab=history" class:active={tab === 'history'}>History</a>
		<a href="?tab=updates" class:active={tab === 'updates'}>
			Image updates{#if availableUpdates}<span class="count">{availableUpdates}</span>{/if}
		</a>
	</nav>

	{#if tab === 'history'}
		<h2 class="first">Deployments</h2>
		<table>
			<tbody>
				{#each deployments.data ?? [] as d (d.id)}
					<DeploymentRow {d} />
				{:else}
					<tr><td class="muted">No deployments yet.</td></tr>
				{/each}
			</tbody>
		</table>

		<h2>Revisions</h2>
		<table>
			<thead>
				<tr><th>Commit</th><th>Message</th><th>Author</th><th>Committed</th><th></th></tr>
			</thead>
			<tbody>
				{#each revisions.data ?? [] as r (r.id)}
					<tr>
						<td><code>{r.commit.slice(0, 10)}</code></td>
						<td>
							{r.message}
							{#if r.id === s.last_good_revision?.id}<Badge value="running" />{/if}
						</td>
						<td class="muted">{r.author}</td>
						<td class="muted">{ago(r.committed_at)}</td>
						<td>
							{#if r.id !== s.last_good_revision?.id && !s.removed_at && can('operator', s.id)}
								<button class="link" onclick={() => deploy(r)} disabled={busy}>deploy</button>
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{:else if tab === 'updates'}
		{#if updates.data?.length}
			<ImageUpdates checks={updates.data} />
		{:else}
			<p class="muted">
				No service has an <code>x-updates</code> policy. Add one to a service in the compose file
				to have Swarmer look for newer images.
			</p>
		{/if}
		{#if can('operator', s.id) && !s.removed_at}
			<p>
				<button onclick={checkUpdates} disabled={checking}>
					{checking ? 'Checking…' : 'Check for updates now'}
				</button>
			</p>
		{/if}
	{:else}
		<Status loading={containers.loading && !containers.data} error={containers.error} />
		{#if containers.data}
			<StackServices
				stackId={s.id}
				data={containers.data}
				operator={can('operator', s.id)}
				onchange={containers.refresh}
				deploying={!!active}
			/>
		{/if}
	{/if}
{/if}

<style>
	.strip {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem 2rem;
		padding: 0.7rem 1rem;
		margin-bottom: 1rem;
	}
	.strip > div {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		min-width: 0;
		flex-wrap: wrap;
	}
	.label {
		font-size: 0.72rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--muted);
		margin-right: 0.2rem;
	}
	.count {
		margin-left: 0.4rem;
		padding: 0 0.4rem;
		border-radius: 999px;
		font-size: 0.75rem;
		background: var(--accent-soft);
		color: var(--accent);
	}
	h2.first {
		margin-top: 0;
	}
	.problems {
		display: flex;
		flex-wrap: wrap;
		gap: 0.3rem 1.25rem;
		align-items: baseline;
		border-color: var(--warn);
		border-left: 3px solid var(--warn);
		margin-bottom: 1rem;
		padding: 0.6rem 1rem;
	}
	.problems strong {
		color: var(--warn);
	}
	.problems.down {
		border-color: var(--bad);
	}
	.problems.down strong {
		color: var(--bad);
	}
	.drift {
		border-color: var(--warn);
		margin-bottom: 1rem;
	}
	.drift ul {
		margin: 0.5rem 0 0;
		padding-left: 1.2rem;
	}
	pre.error {
		white-space: pre-wrap;
	}
</style>
