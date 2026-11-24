<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import Badge from '#lib/components/Badge.svelte';
	import Status from '#lib/components/Status.svelte';
	import { ago } from '#lib/format.ts';
	import { STACK_TOPICS } from '#lib/live.svelte.ts';
	import { poll } from '#lib/poll.svelte.ts';
	import type { Stack } from '#lib/types.ts';

	const stacks = poll<Stack[]>(() => '/stacks', 5000, { topics: STACK_TOPICS });

	function behind(s: Stack): boolean {
		return !!s.latest_revision && s.latest_revision.id !== s.last_good_revision?.id;
	}
</script>

<h1>Stacks</h1>
<Status loading={stacks.loading && !stacks.data} error={stacks.error} />

{#if stacks.data}
	<table>
		<thead>
			<tr>
				<th>Stack</th>
				<th>Repository</th>
				<th>Type</th>
				<th>Mode</th>
				<th>Running revision</th>
				<th>Last deployment</th>
			</tr>
		</thead>
		<tbody>
			{#each stacks.data as s (s.id)}
				<tr class:removed={s.removed_at}>
					<td>
						<a href="/stacks/{s.id}">{s.name}</a>
						{#if s.removed_at}<Badge value="removed from git" tone="muted" />{/if}
						{#if s.config_error}<Badge value="settings error" tone="bad" />{/if}
					</td>
					<td class="muted">{s.repository.name}</td>
					<td>{s.type}</td>
					<td>{s.deploy_mode}</td>
					<td>
						{#if s.last_good_revision}
							<code>{s.last_good_revision.commit.slice(0, 10)}</code>
						{:else}
							<span class="muted">never deployed</span>
						{/if}
						{#if behind(s)}<Badge value="newer in git" tone="warn" />{/if}
					</td>
					<td>
						{#if s.last_deployment}
							<a href="/deployments/{s.last_deployment.id}">
								<Badge value={s.last_deployment.status.replace('_', ' ')} />
							</a>
							<span class="muted">{ago(s.last_deployment.created_at)}</span>
						{/if}
					</td>
				</tr>
			{:else}
				<tr>
					<td colspan="6" class="muted">
						No stacks yet. <a href="/repositories">Add a repository</a> with one folder per stack.
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}

<style>
	tr.removed td {
		opacity: 0.6;
	}
</style>
