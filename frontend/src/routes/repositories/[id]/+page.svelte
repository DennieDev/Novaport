<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api, errorMessage } from '#lib/api.ts';
	import Status from '#lib/components/Status.svelte';
	import { ago } from '#lib/format.ts';
	import { poll } from '#lib/poll.svelte.ts';
	import type { Repository, SyncResult } from '#lib/types.ts';

	const id = $derived(page.params.id ?? '');
	const repo = poll<Repository>(() => `/repositories/${id}`, 15_000, {
		topics: ['repositories', 'stacks']
	});

	let showSecret = $state(false);
	let message = $state<string | undefined>();
	let error = $state<string | undefined>();
	let busy = $state(false);

	const webhookUrl = $derived(repo.data ? `${location.origin}${repo.data.webhook_path}` : '');

	async function act(fn: () => Promise<string | void>) {
		busy = true;
		error = undefined;
		message = undefined;
		try {
			message = (await fn()) ?? undefined;
			await repo.refresh();
		} catch (e) {
			error = errorMessage(e);
		} finally {
			busy = false;
		}
	}

	const sync = () =>
		act(async () => {
			const r = await api.post<SyncResult>(`/repositories/${id}/sync`);
			if (r.error) throw new Error(r.error);
			if (r.skipped) return 'A sync is already running.';
			return `Synced ${r.commit?.slice(0, 10)}: ${r.new_revisions} new revision(s), ${r.queued_deployments} deployment(s) queued.`;
		});

	const rotateKey = () =>
		act(async () => {
			if (!confirm('Generate a new deploy key? The old key stops working once removed from the forge.'))
				return;
			await api.post(`/repositories/${id}/ssh-key`);
			return 'New deploy key generated. Add it to the repository.';
		});

	const rotateSecret = () =>
		act(async () => {
			await api.post(`/repositories/${id}/webhook-secret`);
			showSecret = true;
			return 'New webhook secret generated. Update it in the webhook settings.';
		});

	async function remove() {
		if (!repo.data) return;
		const ok = confirm(
			`Remove ${repo.data.name} and its ${repo.data.stack_count} stack(s) from Novaport? ` +
				'Running services are not touched.'
		);
		if (!ok) return;
		await act(async () => {
			await api.delete(`/repositories/${id}`);
			await goto('/repositories');
		});
	}
</script>

<p><a href="/repositories">← Repositories</a></p>
<Status loading={repo.loading && !repo.data} error={repo.error} />

{#if repo.data}
	{@const r = repo.data}
	<h1>{r.name}</h1>

	<div class="panel">
		<dl class="kv">
			<dt>URL</dt>
			<dd><code>{r.url}</code></dd>
			<dt>Branch</dt>
			<dd>{r.branch}</dd>
			<dt>Stacks folder</dt>
			<dd><code>{r.stacks_root || '/'}</code></dd>
			<dt>Polling</dt>
			<dd>{r.poll_interval ? `every ${r.poll_interval}s` : 'off (webhook only)'}</dd>
			<dt>Default deploy mode</dt>
			<dd>{r.default_deploy_mode}</dd>
			<dt>Edits</dt>
			<dd>
				{r.default_edit_mode === 'pr' ? 'pull requests' : 'commit to branch'}
				{#if r.forge !== 'none'}
					· {r.forge}{r.forge_api_url ? ` (${r.forge_api_url})` : ''}
					· token {r.has_forge_token || r.has_token ? 'stored' : 'missing'}
				{/if}
			</dd>
			<dt>Last sync</dt>
			<dd>
				{ago(r.last_synced_at)}
				{#if r.last_commit}at <code>{r.last_commit.slice(0, 10)}</code>{/if}
			</dd>
			<dt>Stacks</dt>
			<dd><a href="/stacks">{r.stack_count}</a></dd>
		</dl>
	</div>

	{#if r.last_error}
		<h2>Problems</h2>
		<pre class="panel error">{r.last_error}</pre>
	{/if}

	<p class="actions">
		<button onclick={sync} disabled={busy}>Sync now</button>
		{#if message}<span class="muted">{message}</span>{/if}
		{#if error}<span class="error">{error}</span>{/if}
	</p>

	{#if r.auth === 'ssh'}
		<h2>Deploy key</h2>
		<p class="muted">Add this as a read-only deploy key on the repository.</p>
		<pre class="panel key">{r.ssh_public_key}</pre>
		<button onclick={rotateKey} disabled={busy}>Generate new key</button>
	{:else if r.auth === 'token'}
		<h2>Authentication</h2>
		<p class="muted">HTTPS token {r.has_token ? 'stored (encrypted)' : 'missing'}.</p>
	{/if}

	<h2>Webhook</h2>
	<p class="muted">
		Point a push webhook here so changes sync right away. GitHub, GitLab, Gitea and Forgejo are
		supported; use content type JSON.
	</p>
	<div class="panel">
		<dl class="kv">
			<dt>URL</dt>
			<dd><code>{webhookUrl}</code></dd>
			<dt>Secret</dt>
			<dd>
				{#if showSecret}<code>{r.webhook_secret}</code>{:else}<span class="muted">hidden</span>{/if}
				<button class="link" onclick={() => (showSecret = !showSecret)}>
					{showSecret ? 'hide' : 'show'}
				</button>
				<button class="link" onclick={rotateSecret} disabled={busy}>regenerate</button>
			</dd>
		</dl>
	</div>

	<h2>Danger zone</h2>
	<button onclick={remove} disabled={busy}>Remove repository</button>
{/if}

<style>
	.actions {
		display: flex;
		gap: 0.8rem;
		align-items: center;
		margin-top: 1rem;
	}
	pre.key {
		white-space: pre-wrap;
		word-break: break-all;
	}
	pre.error {
		white-space: pre-wrap;
	}
	dd .link {
		margin-left: 0.6rem;
	}
</style>
