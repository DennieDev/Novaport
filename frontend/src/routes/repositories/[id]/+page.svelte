<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api, errorMessage } from '#lib/api.ts';
	import Crumbs from '#lib/components/Crumbs.svelte';
	import Badge from '#lib/components/Badge.svelte';
	import Icon from '#lib/components/Icon.svelte';
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

	const WARN_DAYS = 14;
	let newToken = $state('');

	function daysLeft(r: Repository): number | null {
		if (!r.forge_token_expires_at) return null;
		return Math.floor((new Date(r.forge_token_expires_at).getTime() - Date.now()) / 86_400_000);
	}

	const replaceToken = (event: SubmitEvent) => {
		event.preventDefault();
		return act(async () => {
			await api.patch(`/repositories/${id}`, { forge_token: newToken });
			newToken = '';
			return 'Token replaced.';
		});
	};

	const checkToken = () =>
		act(async () => {
			await api.post(`/repositories/${id}/forge-token/check`);
			return 'Checked with the forge.';
		});

	const removeToken = () =>
		act(async () => {
			if (!confirm('Remove the forge token? Pull requests can no longer be opened.')) return;
			await api.patch(`/repositories/${id}`, { remove_forge_token: true });
			return 'Token removed.';
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
			`Remove ${repo.data.name} and its ${repo.data.stack_count} stack(s) from Swarmer? ` +
				'Running services are not touched.'
		);
		if (!ok) return;
		await act(async () => {
			await api.delete(`/repositories/${id}`);
			await goto('/repositories');
		});
	}
</script>

<Crumbs items={[{ href: '/repositories', label: 'Repositories' }]} />
<Status loading={repo.loading && !repo.data} error={repo.error} />

{#if repo.data}
	{@const r = repo.data}
	<div class="page-header">
		<h1>{r.name}</h1>
		<div class="toolbar">
			<button onclick={sync} disabled={busy}><Icon name="restart" size={14} />Sync now</button>
		</div>
	</div>
	{#if message}<p class="muted">{message}</p>{/if}
	{#if error}<p class="error">{error}</p>{/if}

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


	{#if r.auth === 'ssh'}
		<h2>Deploy key</h2>
		<p class="muted">
			Add this as a deploy key on the repository, with write access: edits and image updates
			are pushed with it.
		</p>
		<pre class="panel key">{r.ssh_public_key}</pre>
		<button onclick={rotateKey} disabled={busy}>Generate new key</button>
	{:else if r.auth === 'token'}
		<h2>Authentication</h2>
		<p class="muted">HTTPS token {r.has_token ? 'stored (encrypted)' : 'missing'}.</p>
	{/if}

	{#if r.forge !== 'none'}
		{@const days = daysLeft(r)}
		<h2>Forge token</h2>
		<p class="muted">
			Opens pull requests on {r.forge}.
			{#if !r.has_forge_token && r.has_token}Without its own token, the HTTPS token is used.{/if}
			It is checked daily, and a notification goes out {WARN_DAYS} days before it expires.
		</p>
		<div class="panel">
			<dl class="kv">
				<dt>Token</dt>
				<dd>
					{#if r.has_forge_token}stored (encrypted)
					{:else if r.has_token}none; using the HTTPS token
					{:else}<span class="muted">none</span>{/if}
				</dd>
				{#if r.has_forge_token || r.has_token}
					<dt>Expires</dt>
					<dd>
						{#if r.forge_token_error}
							<Badge value="not working" tone="bad" />
						{:else if r.forge_token_expires_at}
							{new Date(r.forge_token_expires_at).toLocaleDateString()}
							{#if days !== null && days < 0}<Badge value="expired" tone="bad" />
							{:else if days !== null && days <= WARN_DAYS}<Badge value="in {days} days" tone="warn" />
							{:else}<span class="muted">({ago(r.forge_token_expires_at)})</span>{/if}
						{:else if r.forge_token_checked_at}
							<span class="muted">no expiry date</span>
						{:else}
							<span class="muted">not checked yet</span>
						{/if}
					</dd>
					<dt>Checked</dt>
					<dd>
						{ago(r.forge_token_checked_at)}
						<button class="link" onclick={checkToken} disabled={busy}>check now</button>
					</dd>
					{#if r.forge_token_error}
						<dt>Problem</dt>
						<dd class="error">{r.forge_token_error}</dd>
					{/if}
				{/if}
			</dl>
			<form class="replace" onsubmit={replaceToken}>
				<input
					type="password"
					bind:value={newToken}
					required
					autocomplete="off"
					placeholder={r.has_forge_token ? 'New token' : 'Token'}
					aria-label="Forge token"
				/>
				<button type="submit" disabled={busy || !newToken}>
					{r.has_forge_token ? 'Replace' : 'Add'}
				</button>
				{#if r.has_forge_token}
					<button type="button" onclick={removeToken} disabled={busy}>Remove</button>
				{/if}
			</form>
		</div>
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
	.replace {
		display: flex;
		gap: 0.6rem;
		margin-top: 1rem;
		padding-top: 1rem;
		border-top: 1px solid var(--border);
	}
	.replace input {
		flex: 1;
		max-width: 24rem;
	}
</style>
