<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { api, errorMessage } from '#lib/api.ts';
	import Status from '#lib/components/Status.svelte';
	import { ago } from '#lib/format.ts';
	import { poll } from '#lib/poll.svelte.ts';
	import { session } from '#lib/session.svelte.ts';
	import type { RoleBinding, User } from '#lib/types.ts';

	const users = poll<User[]>(() => '/users', 30_000);
	const bindings = poll<RoleBinding[]>(() => '/role-bindings', 30_000);

	let error = $state<string | undefined>();

	function rolesOf(user: User): string[] {
		const groupIds = new Set(user.groups.map((g) => g.id));
		return (bindings.data ?? [])
			.filter((b) => b.user_id === user.id || (b.group_id && groupIds.has(b.group_id)))
			.map((b) => {
				const scope = b.stack_name ?? 'everything';
				const via = b.group_id ? ` via ${b.subject}` : '';
				return `${b.role} on ${scope}${via}`;
			});
	}

	async function setDisabled(user: User, disabled: boolean) {
		error = undefined;
		try {
			await api.patch(`/users/${user.id}`, { disabled });
			await users.refresh();
		} catch (e) {
			error = errorMessage(e);
		}
	}

	async function remove(user: User) {
		if (!confirm(`Delete ${user.username}? Their roles and group memberships go too.`)) return;
		error = undefined;
		try {
			await api.delete(`/users/${user.id}`);
			await Promise.all([users.refresh(), bindings.refresh()]);
		} catch (e) {
			error = errorMessage(e);
		}
	}
</script>

<p class="muted">
	Users from a login provider appear after their first login. Give them roles directly or through
	a group under <a href="/access/bindings">Roles</a>.
</p>

<Status loading={users.loading && !users.data} error={users.error} />
{#if error}<p class="error">{error}</p>{/if}

{#if users.data}
	<table>
		<thead>
			<tr>
				<th>User</th>
				<th>Login</th>
				<th>Groups</th>
				<th>Roles</th>
				<th>Last login</th>
				<th></th>
			</tr>
		</thead>
		<tbody>
			{#each users.data as u (u.id)}
				<tr class:disabled={u.disabled}>
					<td>
						<strong>{u.username}</strong>
						{#if u.name}<br /><span class="muted">{u.name}</span>{/if}
						{#if u.email}<br /><span class="muted">{u.email}</span>{/if}
					</td>
					<td>{u.local ? 'password' : u.issuer}</td>
					<td>
						{#each u.groups as g (g.id)}
							<span class="chip" title={g.synced ? 'from the login provider' : 'added by hand'}>
								{g.name}
							</span>
						{:else}<span class="muted">–</span>{/each}
					</td>
					<td>
						{#each rolesOf(u) as role (role)}<div>{role}</div>{:else}<span class="muted">none</span>{/each}
					</td>
					<td class="muted">{ago(u.last_login_at)}</td>
					<td class="actions">
						{#if u.id !== session.user?.id}
							<button class="link" onclick={() => setDisabled(u, !u.disabled)}>
								{u.disabled ? 'enable' : 'disable'}
							</button>
							<button class="link" onclick={() => remove(u)}>delete</button>
						{/if}
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}

<style>
	tr.disabled td {
		opacity: 0.55;
	}
	.chip {
		display: inline-block;
		background: var(--code);
		border-radius: 4px;
		padding: 0 0.4rem;
		margin: 0 0.25rem 0.2rem 0;
	}
	.actions {
		white-space: nowrap;
	}
	.actions button + button {
		margin-left: 0.75rem;
	}
</style>
