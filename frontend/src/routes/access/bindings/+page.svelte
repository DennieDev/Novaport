<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { page } from '$app/state';
	import { api, errorMessage } from '#lib/api.ts';
	import Status from '#lib/components/Status.svelte';
	import { poll } from '#lib/poll.svelte.ts';
	import type { Group, Role, RoleBinding, Stack, User } from '#lib/types.ts';

	const stackFilter = $derived(page.url.searchParams.get('stack') ?? '');
	const bindings = poll<RoleBinding[]>(
		() => (stackFilter ? `/role-bindings?stack_id=${stackFilter}` : '/role-bindings'),
		30_000
	);
	const users = poll<User[]>(() => '/users', 60_000);
	const groups = poll<Group[]>(() => '/groups', 60_000);
	const stacks = poll<Stack[]>(() => '/stacks', 60_000);

	const roles: { value: Role; label: string }[] = [
		{ value: 'viewer', label: 'Viewer: see stacks, services, logs, history' },
		{ value: 'operator', label: 'Operator: also deploy, approve, roll back, terminals' },
		{ value: 'editor', label: 'Editor: also edit files and secrets' },
		{ value: 'admin', label: 'Admin: everything, including settings and access' }
	];

	let subject = $state('');
	let role = $state<Role>('viewer');
	let scope = $state('');
	let error = $state<string | undefined>();

	$effect(() => {
		scope = stackFilter;
	});

	async function run(action: () => Promise<unknown>) {
		error = undefined;
		try {
			await action();
			await bindings.refresh();
		} catch (e) {
			error = errorMessage(e);
		}
	}

	function create(event: SubmitEvent) {
		event.preventDefault();
		const [kind, id] = subject.split(':');
		return run(async () => {
			await api.post('/role-bindings', {
				user_id: kind === 'user' ? id : null,
				group_id: kind === 'group' ? id : null,
				role,
				stack_id: scope || null
			});
			subject = '';
		});
	}

	function changeRole(binding: RoleBinding, value: Role) {
		return run(() => api.patch(`/role-bindings/${binding.id}`, { role: value }));
	}

	function remove(binding: RoleBinding) {
		return run(() => api.delete(`/role-bindings/${binding.id}`));
	}

	const filterName = $derived(stacks.data?.find((s) => s.id === stackFilter)?.name);
</script>

<p class="muted">
	A role on everything applies to every stack and to cluster-wide pages such as nodes and
	networks. A role on one stack applies to that stack, its services, and their logs. When a user
	has several roles, the highest one counts.
</p>

{#if stackFilter}
	<p>Showing roles on <strong>{filterName ?? 'one stack'}</strong>. <a href="/access/bindings">Show all</a></p>
{/if}

<Status loading={bindings.loading && !bindings.data} error={bindings.error} />
{#if error}<p class="error">{error}</p>{/if}

{#if bindings.data}
	<table>
		<thead><tr><th>Who</th><th>Role</th><th>On</th><th></th></tr></thead>
		<tbody>
			{#each bindings.data as b (b.id)}
				<tr>
					<td>{b.subject} <span class="muted">{b.group_id ? '(group)' : ''}</span></td>
					<td>
						<select
							value={b.role}
							onchange={(e) => changeRole(b, (e.currentTarget as HTMLSelectElement).value as Role)}
						>
							{#each roles as r (r.value)}<option value={r.value}>{r.value}</option>{/each}
						</select>
					</td>
					<td>
						{#if b.stack_id}<a href="/stacks/{b.stack_id}">{b.stack_name}</a>{:else}everything{/if}
					</td>
					<td><button class="link" onclick={() => remove(b)}>remove</button></td>
				</tr>
			{:else}
				<tr><td colspan="4" class="muted">No roles given yet.</td></tr>
			{/each}
		</tbody>
	</table>
{/if}

<h2>Give a role</h2>
<form class="panel grid" onsubmit={create}>
	<label>
		User or group
		<select bind:value={subject} required>
			<option value="" disabled>Choose…</option>
			<optgroup label="Groups">
				{#each groups.data ?? [] as g (g.id)}<option value="group:{g.id}">{g.name}</option>{/each}
			</optgroup>
			<optgroup label="Users">
				{#each users.data ?? [] as u (u.id)}<option value="user:{u.id}">{u.username}</option>{/each}
			</optgroup>
		</select>
	</label>
	<label>
		Role
		<select bind:value={role}>
			{#each roles as r (r.value)}<option value={r.value}>{r.label}</option>{/each}
		</select>
	</label>
	<label>
		On
		<select bind:value={scope}>
			<option value="">Everything</option>
			{#each stacks.data ?? [] as s (s.id)}<option value={s.id}>{s.name}</option>{/each}
		</select>
	</label>
	<div><button type="submit">Give role</button></div>
</form>

<style>
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
		gap: 0.9rem;
		max-width: 900px;
		align-items: end;
	}
	label {
		display: grid;
		gap: 0.3rem;
	}
</style>
