<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { api, errorMessage } from '#lib/api.ts';
	import Badge from '#lib/components/Badge.svelte';
	import CodeEditor from '#lib/components/CodeEditor.svelte';
	import Status from '#lib/components/Status.svelte';
	import { poll } from '#lib/poll.svelte.ts';
	import type {
		DeployMode,
		GeneratedCompose,
		Repository,
		SaveResult,
		Stack,
		UnmanagedStack
	} from '#lib/types.ts';

	const unmanaged = poll<UnmanagedStack[]>(() => '/unmanaged', 30_000, { topics: ['stacks'] });
	const repositories = poll<Repository[]>(() => '/repositories', 60_000);

	let selected = $state<UnmanagedStack | undefined>();
	let generated = $state<GeneratedCompose | undefined>();
	let compose = $state('');
	let repositoryId = $state('');
	let deploy = $state<DeployMode>('manual');
	let message = $state('');
	let busy = $state(false);
	let error = $state<string | undefined>();
	let saved = $state<{ result: SaveResult; name: string; stackId?: string } | undefined>();

	function query(u: UnmanagedStack): string {
		return u.endpoint ? `?endpoint=${encodeURIComponent(u.endpoint)}` : '';
	}

	async function open(u: UnmanagedStack) {
		selected = u;
		generated = undefined;
		saved = undefined;
		error = undefined;
		message = `Add ${u.name}`;
		repositoryId ||= repositories.data?.[0]?.id ?? '';
		try {
			generated = await api.get<GeneratedCompose>(`/unmanaged/${u.type}/${u.name}${query(u)}`);
			compose = generated.compose;
		} catch (e) {
			error = errorMessage(e);
		}
	}

	async function adopt(event: SubmitEvent) {
		event.preventDefault();
		const u = selected;
		if (!u) return;
		busy = true;
		error = undefined;
		try {
			const result = await api.post<SaveResult>(`/unmanaged/${u.type}/${u.name}/adopt${query(u)}`, {
				repository_id: repositoryId,
				compose,
				deploy,
				message
			});
			const stacks = await api.get<Stack[]>('/stacks');
			saved = { result, name: u.name, stackId: stacks.find((s) => s.name === u.name)?.id };
			selected = undefined;
			await unmanaged.refresh();
		} catch (e) {
			error = errorMessage(e);
		} finally {
			busy = false;
		}
	}
</script>

<p class="crumbs"><a href="/stacks">← Stacks</a></p>
<h1>Deployed without Swarmer</h1>
<p class="muted">
	These Swarm stacks and Compose projects run on your hosts, but no repository describes them.
	Swarmer can write a compose file from what is running and commit it, after which the stack
	is managed like any other.
</p>

{#if saved}
	<div class="panel">
		{#if saved.result.pull_request_url}
			<p>
				Opened a pull request for {saved.name}:
				<a href={saved.result.pull_request_url} target="_blank" rel="noreferrer">
					{saved.result.pull_request_url}
				</a>
			</p>
			<p class="muted">The stack appears in Swarmer once it's merged.</p>
		{:else}
			<p>
				Committed <code>{saved.result.commit.slice(0, 10)}</code> to <code>{saved.result.branch}</code>.
				{#if saved.stackId}<a href="/stacks/{saved.stackId}">Open {saved.name}</a>{/if}
			</p>
			{#if saved.result.queued_deployments === 0}
				<p class="muted">Nothing was deployed. Deploy it from the stack page when you're ready.</p>
			{/if}
		{/if}
	</div>
{/if}

<Status loading={unmanaged.loading && !unmanaged.data} error={unmanaged.error} />
{#if unmanaged.data}
	<table>
		<thead>
			<tr><th>Name</th><th>Type</th><th>Endpoint</th><th>Services</th><th></th></tr>
		</thead>
		<tbody>
			{#each unmanaged.data as u (`${u.endpoint}/${u.type}/${u.name}`)}
				<tr>
					<td>{u.name}</td>
					<td>{u.type === 'swarm' ? 'Swarm stack' : 'Compose project'}</td>
					<td>{u.endpoint ?? 'local'}</td>
					<td>{u.services}</td>
					<td>
						{#if u.name_taken}
							<Badge value="name in use" tone="warn" />
						{:else}
							<button class="link" onclick={() => open(u)}>add to git</button>
						{/if}
					</td>
				</tr>
			{:else}
				<tr><td colspan="5" class="muted">Everything running here is managed by Swarmer.</td></tr>
			{/each}
		</tbody>
	</table>
{/if}

{#if selected}
	<h2>Add {selected.name}</h2>
	{#if !generated && !error}
		<p class="muted">Reading the running services…</p>
	{/if}
	{#if generated}
		<ul class="notes">
			{#each generated.notes as note (note)}<li>{note}</li>{/each}
		</ul>
		{#if generated.secrets.length}
			<p>
				Goes to <code>secrets.env</code>, encrypted:
				{#each generated.secrets as name, i (name)}{#if i}, {/if}<code>{name}</code>{/each}
			</p>
		{/if}
		<form class="panel" onsubmit={adopt}>
			<CodeEditor bind:value={compose} path="compose.yaml" />
			<div class="grid">
				<label>
					Repository
					<select bind:value={repositoryId} required>
						{#each repositories.data ?? [] as r (r.id)}
							<option value={r.id}>{r.name} ({r.stacks_root || '/'}/{selected.name})</option>
						{/each}
					</select>
				</label>
				<label>
					After adding
					<select bind:value={deploy}>
						<option value="manual">Deploy only when asked</option>
						<option value="approve">Deploy new revisions after approval</option>
						<option value="auto">Deploy new revisions automatically</option>
					</select>
				</label>
				<label>Commit message <input bind:value={message} required /></label>
			</div>
			<p class="muted">
				Deploying the new stack replaces the running services with what this file describes.
				Review it first; anything that differs is applied on the first deploy.
			</p>
			{#if generated.own && deploy === 'auto'}
				<p class="warn">
					Every change to this stack would restart Swarmer, and a change that keeps it from
					starting can only be fixed by hand.
				</p>
			{/if}
			<p class="actions">
				<button type="submit" disabled={busy || !repositoryId}>Commit to the repository</button>
				<button type="button" onclick={() => (selected = undefined)}>Cancel</button>
			</p>
		</form>
	{/if}
{/if}
{#if error}<p class="error">{error}</p>{/if}

<style>
	.warn {
		color: var(--warn);
	}
	.notes {
		margin: 0 0 1rem;
		padding-left: 1.2rem;
	}
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
		gap: 0.9rem;
		margin-top: 1rem;
	}
	.actions button + button {
		margin-left: 0.5rem;
	}
</style>
