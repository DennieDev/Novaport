<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { api, errorMessage } from '#lib/api.ts';
	import Status from '#lib/components/Status.svelte';
	import { poll } from '#lib/poll.svelte.ts';
	import type { Group, GroupMapping, OidcProvider, ProviderCheck } from '#lib/types.ts';

	const providers = poll<OidcProvider[]>(() => '/oidc-providers', 60_000);
	const groups = poll<Group[]>(() => '/groups', 60_000);

	interface Form {
		slug: string;
		display_name: string;
		issuer: string;
		client_id: string;
		client_secret: string;
		scopes: string;
		username_claim: string;
		groups_claim: string;
		enabled: boolean;
		mappings: GroupMapping[];
	}

	function blank(): Form {
		return {
			slug: '',
			display_name: '',
			issuer: '',
			client_id: '',
			client_secret: '',
			scopes: 'openid profile email',
			username_claim: 'preferred_username',
			groups_claim: 'groups',
			enabled: true,
			mappings: []
		};
	}

	// null: no form open; '' : adding a new provider; otherwise the id being edited.
	let editing = $state<string | null>(null);
	let form = $state<Form>(blank());
	let hadSecret = $state(false);
	let clearSecret = $state(false);
	let error = $state<string | undefined>();
	let busy = $state(false);
	let checks = $state<Record<string, ProviderCheck>>({});

	function startAdd() {
		editing = '';
		form = blank();
		hadSecret = false;
		error = undefined;
	}

	function startEdit(p: OidcProvider) {
		editing = p.id;
		form = {
			slug: p.slug,
			display_name: p.display_name,
			issuer: p.issuer,
			client_id: p.client_id,
			client_secret: '',
			scopes: p.scopes,
			username_claim: p.username_claim,
			groups_claim: p.groups_claim,
			enabled: p.enabled,
			mappings: p.mappings.map((m) => ({ claim_value: m.claim_value, group_id: m.group_id }))
		};
		hadSecret = p.has_client_secret;
		clearSecret = false;
		error = undefined;
	}

	async function save(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		error = undefined;
		const mappings = form.mappings.filter((m) => m.claim_value && m.group_id);
		try {
			if (editing) {
				const { slug: _, client_secret, ...rest } = form;
				let secret: string | null = null;
				if (clearSecret) secret = '';
				else if (client_secret) secret = client_secret;
				await api.patch(`/oidc-providers/${editing}`, {
					...rest,
					mappings,
					client_secret: secret
				});
			} else {
				await api.post('/oidc-providers', {
					...form,
					mappings,
					client_secret: form.client_secret || null
				});
			}
			editing = null;
			await providers.refresh();
		} catch (e) {
			error = errorMessage(e);
		} finally {
			busy = false;
		}
	}

	async function remove(p: OidcProvider) {
		if (!confirm(`Delete ${p.display_name}? Its users stay, but lose groups synced from it.`)) return;
		try {
			await api.delete(`/oidc-providers/${p.id}`);
			await providers.refresh();
		} catch (e) {
			error = errorMessage(e);
		}
	}

	async function check(p: OidcProvider) {
		checks[p.id] = await api.post<ProviderCheck>(`/oidc-providers/${p.id}/check`);
	}
</script>

<p class="muted">
	Any OpenID Connect provider works: Keycloak, Authentik, Authelia, Entra ID, Google, and others.
	Create a confidential client there with the redirect URI shown below. New users get no roles
	until a group mapping or a role gives them some.
</p>

<Status loading={providers.loading && !providers.data} error={providers.error} />

{#each providers.data ?? [] as p (p.id)}
	<div class="panel provider">
		<div class="head">
			<strong>{p.display_name}</strong>
			{#if !p.enabled}<span class="muted">(disabled)</span>{/if}
			<span class="spacer"></span>
			<button class="link" onclick={() => check(p)}>check</button>
			<button class="link" onclick={() => startEdit(p)}>edit</button>
			<button class="link" onclick={() => remove(p)}>delete</button>
		</div>
		<dl class="kv">
			<dt>Issuer</dt>
			<dd><code>{p.issuer}</code></dd>
			<dt>Client ID</dt>
			<dd><code>{p.client_id}</code> {p.has_client_secret ? '' : '(public client, no secret)'}</dd>
			<dt>Redirect URI</dt>
			<dd><code>{p.redirect_uri}</code></dd>
			<dt>Groups</dt>
			<dd>
				{#each p.mappings as m (m.claim_value + m.group_id)}
					<div><code>{p.groups_claim}</code> contains <code>{m.claim_value}</code> → {m.group_name}</div>
				{:else}<span class="muted">no mappings</span>{/each}
			</dd>
		</dl>
		{#if checks[p.id]}
			{@const c = checks[p.id]}
			{#if c.ok}
				<p class="ok">Discovery works. Token endpoint: <code>{c.token_endpoint}</code></p>
			{:else}
				<p class="error">{c.error}</p>
			{/if}
		{/if}
	</div>
{:else}
	{#if providers.data}<p class="muted">No login providers yet. Only password login is possible.</p>{/if}
{/each}

{#if editing === null}
	<p><button onclick={startAdd}>Add login provider</button></p>
{:else}
	<h2>{editing ? `Edit ${form.display_name}` : 'New login provider'}</h2>
	<form class="panel" onsubmit={save}>
		<div class="grid">
			<label>
				Name on the login button
				<input bind:value={form.display_name} required maxlength="100" placeholder="Company SSO" />
			</label>
			<label>
				Slug (in the redirect URI)
				<input
					bind:value={form.slug}
					required
					pattern="[a-z0-9][a-z0-9\-]*"
					maxlength="50"
					disabled={!!editing}
					placeholder="sso"
				/>
			</label>
			<label>
				Issuer URL
				<input bind:value={form.issuer} required placeholder="https://auth.example.com/realms/main" />
			</label>
			<label>Client ID <input bind:value={form.client_id} required /></label>
			<label>
				Client secret {hadSecret ? '(empty = keep the current one)' : ''}
				<input type="password" bind:value={form.client_secret} autocomplete="off" disabled={clearSecret} />
			</label>
			{#if hadSecret}
				<label class="check">
					<input type="checkbox" bind:checked={clearSecret} /> Remove the secret (public client)
				</label>
			{/if}
			<label>Scopes <input bind:value={form.scopes} required /></label>
			<label>Username claim <input bind:value={form.username_claim} required /></label>
			<label>
				Groups claim (dots for nested, e.g. realm_access.roles)
				<input bind:value={form.groups_claim} />
			</label>
			<label class="check"><input type="checkbox" bind:checked={form.enabled} /> Enabled</label>
		</div>

		<h3>Group mappings</h3>
		<p class="muted">Users whose groups claim contains the value become members of the group.</p>
		{#each form.mappings as m, i (i)}
			<p class="row">
				<input bind:value={m.claim_value} placeholder="claim value, e.g. swarmer-admins" />
				<span>→</span>
				<select bind:value={m.group_id}>
					<option value="" disabled>Group…</option>
					{#each groups.data ?? [] as g (g.id)}<option value={g.id}>{g.name}</option>{/each}
				</select>
				<button type="button" class="link" onclick={() => form.mappings.splice(i, 1)}>remove</button>
			</p>
		{/each}
		<p>
			<button
				type="button"
				onclick={() => form.mappings.push({ claim_value: '', group_id: '' })}
				disabled={!groups.data?.length}
			>
				Add mapping
			</button>
			{#if groups.data && !groups.data.length}
				<span class="muted">Create a group first under <a href="/access/groups">Groups</a>.</span>
			{/if}
		</p>

		{#if error}<p class="error">{error}</p>{/if}
		<p class="row">
			<button type="submit" disabled={busy}>{editing ? 'Save' : 'Add'}</button>
			<button type="button" class="link" onclick={() => (editing = null)}>cancel</button>
		</p>
	</form>
{/if}

<style>
	.provider {
		margin-bottom: 0.9rem;
	}
	.head {
		display: flex;
		gap: 0.9rem;
		align-items: baseline;
		margin-bottom: 0.6rem;
	}
	.spacer {
		flex: 1;
	}
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
		gap: 0.9rem;
	}
	label {
		display: grid;
		gap: 0.3rem;
	}
	label.check {
		display: flex;
		align-items: center;
		gap: 0.4rem;
	}
	h3 {
		font-size: 0.95rem;
		margin: 1.25rem 0 0.25rem;
	}
	.row {
		display: flex;
		gap: 0.6rem;
		align-items: center;
	}
	.ok {
		color: var(--ok);
	}
</style>
