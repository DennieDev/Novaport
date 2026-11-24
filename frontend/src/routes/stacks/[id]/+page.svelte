<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api, errorMessage } from '#lib/api.ts';
	import Badge from '#lib/components/Badge.svelte';
	import DeploymentRow from '#lib/components/DeploymentRow.svelte';
	import Icon from '#lib/components/Icon.svelte';
	import ImageUpdates from '#lib/components/ImageUpdates.svelte';
	import StackServices from '#lib/components/StackServices.svelte';
	import Status from '#lib/components/Status.svelte';
	import { ago } from '#lib/format.ts';
	import { onStack, STACK_TOPICS } from '#lib/live.svelte.ts';
	import { poll } from '#lib/poll.svelte.ts';
	import { can } from '#lib/session.svelte.ts';
	import type { Deployment, ImageCheck, Revision, Stack } from '#lib/types.ts';

	const id = $derived(page.params.id ?? '');
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
	const updates = poll<ImageCheck[]>(() => `/stacks/${id}/updates`, 60_000, {
		topics: ['image_checks'],
		match: onStack(() => id)
	});
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

	async function deploy(revision?: Revision) {
		const s = stack.data;
		if (!s) return;
		const what = revision ? `revision ${revision.commit.slice(0, 10)}` : 'the latest revision';
		if (!confirm(`Deploy ${what} of ${s.name}?`)) return;
		busy = true;
		error = undefined;
		try {
			const d = await api.post<Deployment>(`/stacks/${id}/deploy`, {
				revision_id: revision?.id ?? null
			});
			await goto(`/deployments/${d.id}`);
		} catch (e) {
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

<p class="crumbs"><a href="/stacks">Stacks</a></p>
<Status loading={stack.loading && !stack.data} error={stack.error} />

{#if stack.data}
	{@const s = stack.data}
	<div class="page-header">
		<div>
			<h1>
				{s.name}
				<Badge value={s.type === 'swarm' ? 'swarm' : 'compose'} tone="muted" />
				{#if s.removed_at}<Badge value="removed from git" tone="bad" />{/if}
				{#if s.drift?.length}<Badge value="drifted" tone="warn" />{/if}
			</h1>
			<p class="subtitle">
				<a href="/repositories/{s.repository.id}">{s.repository.name}</a> · <code>{s.path}</code>
				· {s.endpoint ?? 'local'}
			</p>
		</div>
		<div class="toolbar">
			{#if can('operator', s.id)}
				<button
					class="primary"
					onclick={() => deploy()}
					disabled={busy || !s.latest_revision || !!s.removed_at}
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
	{#if restarted}<p class="muted">Restart started.</p>{/if}

	{#if s.removed_at}
		<p class="error">
			This folder was removed from git {ago(s.removed_at)}. Its services keep running until you
			remove them.
		</p>
	{/if}
	{#if s.config_error}
		<pre class="panel error">{s.config_error}</pre>
		<p class="muted">New revisions are ignored until novaport.yaml is fixed.</p>
	{/if}

	<div class="summary">
		<div class="panel">
			<div class="label">Running</div>
			{#if s.last_good_revision}
				<div><code>{s.last_good_revision.commit.slice(0, 10)}</code> {s.last_good_revision.message}</div>
			{:else}<div class="muted">never deployed by Novaport</div>{/if}
		</div>
		<div class="panel">
			<div class="label">Latest in git</div>
			{#if s.latest_revision}
				<div>
					<code>{s.latest_revision.commit.slice(0, 10)}</code>
					{s.latest_revision.message}
					{#if s.latest_revision.id !== s.last_good_revision?.id}<Badge value="not deployed" />{/if}
				</div>
				<div class="muted">{s.latest_revision.author}, {ago(s.latest_revision.committed_at)}</div>
			{:else}<div>–</div>{/if}
		</div>
		<div class="panel">
			<div class="label">Mode</div>
			<div>Deploy: {s.deploy_mode}</div>
			<div class="muted">Edits: {s.edit_mode === 'pr' ? 'pull request' : 'commit to branch'}</div>
		</div>
		<div class="panel">
			<div class="label">Last deployment</div>
			{#if s.last_deployment}
				<div>
					<a href="/deployments/{s.last_deployment.id}">
						<Badge value={s.last_deployment.status.replace('_', ' ')} />
					</a>
				</div>
				<div class="muted">{ago(s.last_deployment.created_at)}</div>
			{:else}<div class="muted">none</div>{/if}
		</div>
	</div>

	{#if s.drift?.length}
		<div class="panel drift">
			<strong>Drifted from the last deploy</strong>
			<span class="muted">
				· checked {ago(s.drift_checked_at)}. Someone changed the running services outside
				Novaport. Deploying again restores what git says.
			</span>
			<ul>
				{#each s.drift as line (line)}<li><code>{line}</code></li>{/each}
			</ul>
		</div>
	{/if}

	<StackServices stackId={s.id} operator={can('operator', s.id)} />

	<h2>Image updates</h2>
	{#if updates.data?.length}
		<ImageUpdates checks={updates.data} />
	{:else}
		<p class="muted">
			No service has an <code>x-updates</code> policy. Add one to a service in the compose file
			to have Novaport look for newer images.
		</p>
	{/if}
	{#if can('operator', s.id) && !s.removed_at}
		<p>
			<button onclick={checkUpdates} disabled={checking}>
				{checking ? 'Checking…' : 'Check for updates now'}
			</button>
		</p>
	{/if}

	<h2>Deployments</h2>
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
{/if}

<style>
	.summary {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
		gap: 0.75rem;
	}
	.summary .panel {
		padding: 0.75rem 1rem;
		min-width: 0;
		overflow-wrap: anywhere;
	}
	.label {
		font-size: 0.72rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--muted);
		margin-bottom: 0.3rem;
	}
	.drift {
		border-color: var(--warn);
		margin-top: 1rem;
	}
	.drift ul {
		margin: 0.5rem 0 0;
		padding-left: 1.2rem;
	}
	pre.error {
		white-space: pre-wrap;
	}
</style>
