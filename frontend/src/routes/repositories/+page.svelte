<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { goto } from '$app/navigation';
	import { api, errorMessage } from '#lib/api.ts';
	import Status from '#lib/components/Status.svelte';
	import { ago } from '#lib/format.ts';
	import { poll } from '#lib/poll.svelte.ts';
	import type { DeployMode, Repository } from '#lib/types.ts';

	const repos = poll<Repository[]>(() => '/repositories', 30_000, {
		topics: ['repositories', 'stacks']
	});

	let adding = $state(false);
	let form = $state({
		name: '',
		url: '',
		branch: 'main',
		stacks_root: 'stacks',
		auth: 'none' as Repository['auth'],
		token: '',
		poll_interval: 300,
		default_deploy_mode: 'manual' as DeployMode,
		default_edit_mode: 'commit' as Repository['default_edit_mode'],
		forge: 'none' as Repository['forge'],
		forge_api_url: '',
		forge_token: ''
	});
	let error = $state<string | undefined>();
	let busy = $state(false);

	async function create(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		error = undefined;
		try {
			const repo = await api.post<Repository>('/repositories', {
				...form,
				token: form.auth === 'token' ? form.token : null,
				forge_api_url: form.forge_api_url || null,
				forge_token: form.forge_token || null
			});
			await goto(`/repositories/${repo.id}`);
		} catch (e) {
			error = errorMessage(e);
		} finally {
			busy = false;
		}
	}
</script>

<h1>Repositories</h1>
<p class="muted">
	Each repository holds stacks, one folder per stack under the stacks folder. Edits made in
	Novaport are committed back, so the deploy key or token needs write access for that.
</p>

<Status loading={repos.loading && !repos.data} error={repos.error} />
{#if repos.data}
	<table>
		<thead>
			<tr><th>Name</th><th>URL</th><th>Branch</th><th>Stacks</th><th>Last sync</th><th></th></tr>
		</thead>
		<tbody>
			{#each repos.data as r (r.id)}
				<tr>
					<td><a href="/repositories/{r.id}">{r.name}</a></td>
					<td><code>{r.url}</code></td>
					<td>{r.branch}</td>
					<td>{r.stack_count}</td>
					<td class="muted">{ago(r.last_synced_at)}</td>
					<td class="error">{r.last_error ? r.last_error.split('\n')[0] : ''}</td>
				</tr>
			{:else}
				<tr><td colspan="6" class="muted">No repositories yet.</td></tr>
			{/each}
		</tbody>
	</table>
{/if}

<p>
	<button onclick={() => (adding = !adding)}>{adding ? 'Close' : 'Add repository'}</button>
</p>

{#if adding}
	<form class="panel grid" onsubmit={create}>
		<label>Name <input bind:value={form.name} required maxlength="100" /></label>
		<label>
			URL
			<input bind:value={form.url} required placeholder="git@github.com:acme/infra.git" />
		</label>
		<label>Branch <input bind:value={form.branch} required /></label>
		<label>Stacks folder <input bind:value={form.stacks_root} placeholder="stacks" /></label>
		<label>
			Authentication
			<select bind:value={form.auth}>
				<option value="none">None (public repository)</option>
				<option value="ssh">SSH deploy key (generated)</option>
				<option value="token">HTTPS token</option>
			</select>
		</label>
		{#if form.auth === 'token'}
			<label>Token <input type="password" bind:value={form.token} required /></label>
		{/if}
		<label>
			Poll every (seconds, 0 = webhook only)
			<input type="number" min="0" max="86400" bind:value={form.poll_interval} />
		</label>
		<label>
			Default deploy mode
			<select bind:value={form.default_deploy_mode}>
				<option value="manual">Manual</option>
				<option value="approve">Approve each revision</option>
				<option value="auto">Deploy automatically</option>
			</select>
		</label>
		<label>
			Edits from Novaport
			<select bind:value={form.default_edit_mode}>
				<option value="commit">Commit to the branch</option>
				<option value="pr">Open a pull request</option>
			</select>
		</label>
		<label>
			Forge (for pull requests)
			<select bind:value={form.forge}>
				<option value="none">None</option>
				<option value="github">GitHub</option>
				<option value="gitlab">GitLab</option>
				<option value="gitea">Gitea / Forgejo</option>
			</select>
		</label>
		{#if form.forge !== 'none'}
			<label>
				API URL (optional for github.com)
				<input bind:value={form.forge_api_url} placeholder="https://git.example.com" />
			</label>
			<label>
				Forge token {form.auth === 'token' ? '(empty = use the HTTPS token)' : ''}
				<input type="password" bind:value={form.forge_token} />
			</label>
		{/if}
		{#if error}<p class="error">{error}</p>{/if}
		<div><button type="submit" disabled={busy}>Add</button></div>
	</form>
{/if}

<style>
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
		gap: 0.9rem;
		max-width: 900px;
	}
	label {
		display: grid;
		gap: 0.3rem;
	}
	select {
		font: inherit;
		padding: 0.35rem;
	}
</style>
