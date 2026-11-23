<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { api, errorMessage } from '#lib/api.ts';
	import Badge from '#lib/components/Badge.svelte';
	import type { AuditEntry } from '#lib/types.ts';

	const PAGE = 100;
	const categories = [
		{ value: '', label: 'All actions' },
		{ value: 'auth.', label: 'Logins' },
		{ value: 'stack.', label: 'Stacks' },
		{ value: 'deployment.', label: 'Deployments' },
		{ value: 'secrets.', label: 'Secrets' },
		{ value: 'terminal.', label: 'Terminals' },
		{ value: 'repository.', label: 'Repositories' },
		{ value: 'role_binding.', label: 'Roles' },
		{ value: 'user.', label: 'Users' },
		{ value: 'group.', label: 'Groups' },
		{ value: 'oidc_provider.', label: 'Login providers' }
	];

	let action = $state('');
	let outcome = $state('');
	let entries = $state<AuditEntry[]>([]);
	let more = $state(false);
	let error = $state<string | undefined>();
	let loading = $state(false);

	async function load(before?: number) {
		loading = true;
		error = undefined;
		const params = new URLSearchParams({ limit: String(PAGE) });
		if (action) params.set('action', action);
		if (outcome) params.set('outcome', outcome);
		if (before !== undefined) params.set('before', String(before));
		try {
			const page = await api.get<AuditEntry[]>(`/audit?${params}`);
			entries = before === undefined ? page : [...entries, ...page];
			more = page.length === PAGE;
		} catch (e) {
			error = errorMessage(e);
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		void action;
		void outcome;
		load();
	});

	function details(entry: AuditEntry): string {
		return Object.entries(entry.details)
			.map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : String(v)}`)
			.join(' · ');
	}
</script>

<h1>Audit log</h1>
<p class="row">
	<select bind:value={action}>
		{#each categories as c (c.value)}<option value={c.value}>{c.label}</option>{/each}
	</select>
	<select bind:value={outcome}>
		<option value="">Any outcome</option>
		<option value="success">Succeeded</option>
		<option value="failed">Failed</option>
		<option value="denied">Denied</option>
	</select>
	<button onclick={() => load()} disabled={loading}>Refresh</button>
</p>

{#if error}<p class="error">{error}</p>{/if}

<table>
	<thead>
		<tr><th>When</th><th>Who</th><th>Action</th><th>Target</th><th>Outcome</th><th>Details</th></tr>
	</thead>
	<tbody>
		{#each entries as e (e.id)}
			<tr>
				<td class="nowrap" title={e.at}>{new Date(e.at).toLocaleString()}</td>
				<td>
					{e.username ?? '–'}
					{#if e.ip}<br /><span class="muted small">{e.ip}</span>{/if}
				</td>
				<td><code>{e.action}</code></td>
				<td>
					{#if e.stack_id}<a href="/stacks/{e.stack_id}">{e.target ?? 'stack'}</a>{:else}{e.target ?? ''}{/if}
				</td>
				<td><Badge value={e.outcome} /></td>
				<td class="muted small">{details(e)}</td>
			</tr>
		{:else}
			<tr><td colspan="6" class="muted">{loading ? 'Loading…' : 'Nothing recorded yet.'}</td></tr>
		{/each}
	</tbody>
</table>

{#if more}
	<p><button onclick={() => load(entries.at(-1)?.id)} disabled={loading}>Load older</button></p>
{/if}

<style>
	.row {
		display: flex;
		gap: 0.6rem;
		align-items: center;
	}
	.nowrap {
		white-space: nowrap;
	}
	.small {
		font-size: 0.85em;
	}
</style>
