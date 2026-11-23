<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { api, errorMessage } from '#lib/api.ts';
	import Status from '#lib/components/Status.svelte';
	import { ago } from '#lib/format.ts';
	import { poll } from '#lib/poll.svelte.ts';
	import type { RegistryCredential } from '#lib/types.ts';

	const registries = poll<RegistryCredential[]>(() => '/registries', 60_000);

	let host = $state('');
	let username = $state('');
	let password = $state('');
	let error = $state<string | undefined>();
	let checks = $state<Record<string, { ok: boolean; error: string | null }>>({});
	let changing = $state<Record<string, string>>({});

	async function run(action: () => Promise<unknown>) {
		error = undefined;
		try {
			await action();
			await registries.refresh();
		} catch (e) {
			error = errorMessage(e);
		}
	}

	function create(event: SubmitEvent) {
		event.preventDefault();
		return run(async () => {
			await api.post('/registries', { host, username, password });
			host = username = password = '';
		});
	}

	function savePassword(r: RegistryCredential) {
		return run(async () => {
			await api.patch(`/registries/${r.id}`, { password: changing[r.id] });
			delete changing[r.id];
		});
	}

	function remove(r: RegistryCredential) {
		if (!confirm(`Remove the credentials for ${r.host}?`)) return;
		return run(() => api.delete(`/registries/${r.id}`));
	}

	async function check(r: RegistryCredential) {
		checks[r.id] = await api.post(`/registries/${r.id}/check`);
	}
</script>

<h1>Registries</h1>
<p class="muted">
	Credentials for private registries. Novaport uses them to check that images exist before a
	deploy, to look for image updates, and passes them to Swarm so every node can pull. Use
	<code>docker.io</code> for Docker Hub; a login there also raises its pull rate limit.
</p>

<Status loading={registries.loading && !registries.data} error={registries.error} />
{#if error}<p class="error">{error}</p>{/if}

{#if registries.data}
	<table>
		<thead><tr><th>Registry</th><th>Username</th><th>Added</th><th></th></tr></thead>
		<tbody>
			{#each registries.data as r (r.id)}
				<tr>
					<td><code>{r.host}</code></td>
					<td>{r.username}</td>
					<td class="muted">{ago(r.created_at)}</td>
					<td class="actions">
						{#if r.id in changing}
							<input type="password" bind:value={changing[r.id]} placeholder="new password or token" />
							<button onclick={() => savePassword(r)} disabled={!changing[r.id]}>Save</button>
						{:else}
							<button class="link" onclick={() => check(r)}>check</button>
							<button class="link" onclick={() => (changing[r.id] = '')}>change password</button>
							<button class="link" onclick={() => remove(r)}>remove</button>
						{/if}
						{#if checks[r.id]}
							{#if checks[r.id].ok}<span class="ok">login works</span>
							{:else}<span class="error">{checks[r.id].error}</span>{/if}
						{/if}
					</td>
				</tr>
			{:else}
				<tr><td colspan="4" class="muted">No credentials yet. Public images work without any.</td></tr>
			{/each}
		</tbody>
	</table>
{/if}

<h2>Add credentials</h2>
<form class="row" onsubmit={create}>
	<input bind:value={host} placeholder="ghcr.io" required />
	<input bind:value={username} placeholder="username" required autocomplete="off" />
	<input type="password" bind:value={password} placeholder="password or token" required autocomplete="off" />
	<button type="submit">Add</button>
</form>

<style>
	.row {
		display: flex;
		gap: 0.6rem;
		flex-wrap: wrap;
	}
	.actions {
		display: flex;
		gap: 0.75rem;
		align-items: center;
	}
	.ok {
		color: var(--ok);
	}
</style>
