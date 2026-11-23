<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { api, errorMessage } from '#lib/api.ts';
	import Status from '#lib/components/Status.svelte';
	import { poll } from '#lib/poll.svelte.ts';
	import type { Group, User } from '#lib/types.ts';

	const groups = poll<Group[]>(() => '/groups', 30_000);
	const users = poll<User[]>(() => '/users', 60_000);

	let name = $state('');
	let description = $state('');
	let adding = $state<Record<string, string>>({});
	let error = $state<string | undefined>();

	async function run(action: () => Promise<unknown>) {
		error = undefined;
		try {
			await action();
			await groups.refresh();
		} catch (e) {
			error = errorMessage(e);
		}
	}

	function create(event: SubmitEvent) {
		event.preventDefault();
		return run(async () => {
			await api.post('/groups', { name, description });
			name = '';
			description = '';
		});
	}

	function nonMembers(group: Group): User[] {
		const ids = new Set(group.members.map((m) => m.user_id));
		return (users.data ?? []).filter((u) => !ids.has(u.id));
	}

	function addMember(group: Group) {
		const userId = adding[group.id];
		if (!userId) return;
		return run(async () => {
			await api.put(`/groups/${group.id}/members/${userId}`);
			adding[group.id] = '';
		});
	}

	function removeGroup(group: Group) {
		if (!confirm(`Delete group ${group.name}? Roles given to it are removed too.`)) return;
		return run(() => api.delete(`/groups/${group.id}`));
	}
</script>

<p class="muted">
	Members come from a login provider's groups claim (see
	<a href="/access/providers">Login providers</a>) or are added here by hand. Hand-added members
	stay when the claim changes.
</p>

<Status loading={groups.loading && !groups.data} error={groups.error} />
{#if error}<p class="error">{error}</p>{/if}

{#each groups.data ?? [] as g (g.id)}
	<div class="panel group">
		<div class="head">
			<strong>{g.name}</strong>
			{#if g.description}<span class="muted">{g.description}</span>{/if}
			<span class="spacer"></span>
			<button class="link" onclick={() => removeGroup(g)}>delete</button>
		</div>
		<ul>
			{#each g.members as m (m.user_id)}
				<li>
					{m.username}
					{#if m.synced}<span class="muted">(from login provider)</span>{/if}
					<button
						class="link"
						onclick={() => run(() => api.delete(`/groups/${g.id}/members/${m.user_id}`))}
					>
						remove
					</button>
				</li>
			{:else}
				<li class="muted">No members.</li>
			{/each}
		</ul>
		{#if nonMembers(g).length}
			<p class="row">
				<select bind:value={adding[g.id]}>
					<option value="">Add a user…</option>
					{#each nonMembers(g) as u (u.id)}<option value={u.id}>{u.username}</option>{/each}
				</select>
				<button onclick={() => addMember(g)} disabled={!adding[g.id]}>Add</button>
			</p>
		{/if}
	</div>
{:else}
	{#if groups.data}<p class="muted">No groups yet.</p>{/if}
{/each}

<h2>New group</h2>
<form class="row" onsubmit={create}>
	<input bind:value={name} placeholder="Name" required maxlength="100" />
	<input class="grow" bind:value={description} placeholder="Description (optional)" maxlength="500" />
	<button type="submit">Create</button>
</form>

<style>
	.group {
		margin-bottom: 0.9rem;
	}
	.head {
		display: flex;
		gap: 0.75rem;
		align-items: baseline;
	}
	.spacer {
		flex: 1;
	}
	ul {
		margin: 0.5rem 0;
		padding-left: 1.2rem;
	}
	li button {
		margin-left: 0.5rem;
	}
	.row {
		display: flex;
		gap: 0.6rem;
		align-items: center;
		margin: 0.5rem 0 0;
	}
	.grow {
		flex: 1;
		max-width: 480px;
	}
</style>
