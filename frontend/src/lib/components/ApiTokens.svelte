<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { api, errorMessage } from '#lib/api.ts';
	import Status from '#lib/components/Status.svelte';
	import { ago } from '#lib/format.ts';
	import { poll } from '#lib/poll.svelte.ts';
	import type { ApiToken, ApiTokenCreated, Role } from '#lib/types.ts';

	// `all` lists every user's tokens (admins); otherwise your own, with a form to add one.
	let { all = false }: { all?: boolean } = $props();

	const tokens = poll<ApiToken[]>(() => (all ? '/tokens/all' : '/tokens'), 30_000);

	const roles: Role[] = ['viewer', 'operator', 'editor', 'admin'];
	const expiries: [string, number | null][] = [
		['in 30 days', 30],
		['in 90 days', 90],
		['in 1 year', 365],
		['never', null]
	];

	let name = $state('');
	let role = $state<Role>('viewer');
	let expiresInDays = $state<number | null>(90);
	let error = $state<string | undefined>();
	let created = $state<ApiTokenCreated | undefined>();

	async function run(action: () => Promise<unknown>) {
		error = undefined;
		try {
			await action();
			await tokens.refresh();
		} catch (e) {
			error = errorMessage(e);
		}
	}

	function create(event: SubmitEvent) {
		event.preventDefault();
		return run(async () => {
			created = await api.post<ApiTokenCreated>('/tokens', {
				name,
				role,
				expires_in_days: expiresInDays
			});
			name = '';
		});
	}

	function revoke(t: ApiToken) {
		const owner = all ? ` of ${t.username}` : '';
		if (!confirm(`Revoke the token ${t.name}${owner}? Scripts using it stop working.`)) return;
		return run(() => api.delete(`/tokens/${t.id}`));
	}

	function expired(t: ApiToken): boolean {
		return t.expires_at !== null && new Date(t.expires_at).getTime() <= Date.now();
	}
</script>

<Status loading={tokens.loading && !tokens.data} error={tokens.error} />
{#if error}<p class="error">{error}</p>{/if}

{#if created}
	<div class="panel created">
		<strong>Token {created.name}</strong>
		<p class="muted">Copy it now; it can't be shown again.</p>
		<pre>{created.token}</pre>
		<p class="muted">Send it as <code>Authorization: Bearer &lt;token&gt;</code>.</p>
		<button class="link" onclick={() => (created = undefined)}>done</button>
	</div>
{/if}

{#if tokens.data}
	{#if tokens.data.length}
		<table>
			<thead>
				<tr>
					<th>Name</th>
					<th>Token</th>
					{#if all}<th>User</th>{/if}
					<th>Role</th>
					<th>Expires</th>
					<th>Last used</th>
					<th></th>
				</tr>
			</thead>
			<tbody>
				{#each tokens.data as t (t.id)}
					<tr>
						<td><strong>{t.name}</strong></td>
						<td class="muted"><code>{t.prefix}…</code></td>
						{#if all}<td>{t.username}</td>{/if}
						<td>{t.role}</td>
						<td class:error={expired(t)} title={t.expires_at ?? undefined}>
							{t.expires_at ? (expired(t) ? 'expired' : ago(t.expires_at)) : 'never'}
						</td>
						<td class="muted">
							{ago(t.last_used_at)}{#if t.last_used_ip}&nbsp;from {t.last_used_ip}{/if}
						</td>
						<td><button class="link" onclick={() => revoke(t)}>revoke</button></td>
					</tr>
				{/each}
			</tbody>
		</table>
	{:else}
		<p class="muted empty">No API tokens.</p>
	{/if}
{/if}

{#if !all}
	<form class="panel form" onsubmit={create}>
		<label>
			Name
			<input bind:value={name} required maxlength="100" placeholder="e.g. ci-deploy" />
		</label>
		<label>
			Highest role
			<select bind:value={role}>
				{#each roles as r (r)}<option value={r}>{r}</option>{/each}
			</select>
		</label>
		<label>
			Expires
			<select bind:value={expiresInDays}>
				{#each expiries as [label, days] (label)}
					<option value={days}>{label}</option>
				{/each}
			</select>
		</label>
		<div>
			<button class="primary" type="submit">Create token</button>
		</div>
	</form>
{/if}

<style>
	.created {
		margin-bottom: 1rem;
	}
	pre {
		white-space: pre-wrap;
		word-break: break-all;
		background: var(--code);
		padding: 0.6rem;
		border-radius: 4px;
	}
	.form {
		display: grid;
		gap: 0.85rem;
		max-width: 26rem;
	}
	label {
		display: grid;
		gap: 0.3rem;
		font-weight: 500;
	}
	.form,
	.empty {
		margin-top: 1rem;
	}
</style>
