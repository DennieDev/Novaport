<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api, errorMessage } from '#lib/api.ts';
	import Badge from '#lib/components/Badge.svelte';
	import DeploymentRow from '#lib/components/DeploymentRow.svelte';
	import ImageUpdates from '#lib/components/ImageUpdates.svelte';
	import Status from '#lib/components/Status.svelte';
	import { ago } from '#lib/format.ts';
	import { poll } from '#lib/poll.svelte.ts';
	import { can } from '#lib/session.svelte.ts';
	import type { Deployment, ImageCheck, Revision, Stack } from '#lib/types.ts';

	const id = $derived(page.params.id ?? '');
	const stack = poll<Stack>(() => `/stacks/${id}`, 5000);
	const revisions = poll<Revision[]>(() => `/stacks/${id}/revisions`, 15_000);
	const deployments = poll<Deployment[]>(() => `/stacks/${id}/deployments?limit=20`, 5000);
	const updates = poll<ImageCheck[]>(() => `/stacks/${id}/updates`, 60_000);
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
</script>

<p><a href="/stacks">← Stacks</a></p>
<Status loading={stack.loading && !stack.data} error={stack.error} />

{#if stack.data}
	{@const s = stack.data}
	<h1>{s.name}</h1>

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

	<div class="panel">
		<dl class="kv">
			<dt>Repository</dt>
			<dd><a href="/repositories/{s.repository.id}">{s.repository.name}</a> · <code>{s.path}</code></dd>
			<dt>Type</dt>
			<dd>{s.type === 'swarm' ? 'Swarm stack' : 'Compose project'}</dd>
			<dt>Deploy mode</dt>
			<dd>{s.deploy_mode}</dd>
			<dt>Edits</dt>
			<dd>{s.edit_mode === 'pr' ? 'pull request' : 'commit to branch'}</dd>
			<dt>Running</dt>
			<dd>
				{#if s.last_good_revision}
					<code>{s.last_good_revision.commit.slice(0, 10)}</code>
					{s.last_good_revision.message}
				{:else}<span class="muted">never deployed by Novaport</span>{/if}
			</dd>
			<dt>Latest in git</dt>
			<dd>
				{#if s.latest_revision}
					<code>{s.latest_revision.commit.slice(0, 10)}</code>
					{s.latest_revision.message}
					<span class="muted">by {s.latest_revision.author}, {ago(s.latest_revision.committed_at)}</span>
				{:else}–{/if}
			</dd>
		</dl>
	</div>

	<p class="actions">
		{#if can('operator', s.id)}
			<button onclick={() => deploy()} disabled={busy || !s.latest_revision || !!s.removed_at}>
				Deploy latest
			</button>
		{/if}
		{#if !s.removed_at}
			<a href="/stacks/{s.id}/edit">{can('editor', s.id) ? 'Edit files' : 'Files'}</a>
			<a href="/stacks/{s.id}/secrets">Secrets</a>
		{/if}
		<a href="/services?stack={s.name}">Services</a>
		{#if can('admin')}<a href="/access/bindings?stack={s.id}">Access</a>{/if}
		{#if error}<span class="error">{error}</span>{/if}
	</p>

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
	.actions {
		display: flex;
		gap: 1rem;
		align-items: center;
		margin-top: 1rem;
	}
	pre.error {
		white-space: pre-wrap;
	}
</style>
