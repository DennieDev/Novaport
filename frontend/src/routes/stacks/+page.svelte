<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import Badge from '#lib/components/Badge.svelte';
	import Status from '#lib/components/Status.svelte';
	import { ago } from '#lib/format.ts';
	import { STACK_TOPICS } from '#lib/live.svelte.ts';
	import { poll } from '#lib/poll.svelte.ts';
	import { can } from '#lib/session.svelte.ts';
	import type { Stack, StackHealth, UnmanagedStack } from '#lib/types.ts';

	const stacks = poll<Stack[]>(() => '/stacks', 5000, { topics: STACK_TOPICS });
	const health = poll<Record<string, StackHealth>>(() => '/stacks-health', 10_000);
	const unmanaged = can('admin')
		? poll<UnmanagedStack[]>(() => '/unmanaged', 60_000, { topics: ['stacks'] })
		: undefined;

	function behind(s: Stack): boolean {
		return !!s.latest_revision && s.latest_revision.id !== s.last_good_revision?.id;
	}
</script>

<h1>Stacks</h1>
<Status loading={stacks.loading && !stacks.data} error={stacks.error} />
{#if unmanaged?.data?.length}
	<p class="muted">
		{unmanaged.data.length} stack{unmanaged.data.length === 1 ? '' : 's'} running here
		{unmanaged.data.length === 1 ? 'was' : 'were'} deployed without Novaport.
		<a href="/stacks/unmanaged">Add them to git</a>
	</p>
{/if}

{#if stacks.data}
	<table>
		<thead>
			<tr>
				<th>Stack</th>
				<th>Health</th>
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
						<a class="name" href="/stacks/{s.id}">{s.name}</a>
						{#if s.removed_at}<Badge value="removed from git" tone="muted" />{/if}
						{#if s.config_error}<Badge value="settings error" tone="bad" />{/if}
					</td>
					<td>
						{#if health.data?.[s.id]}
							{@const h = health.data[s.id]}
							<span title={h.problems.join('\n')}><Badge value={h.state} /></span>
						{:else}<span class="muted">–</span>{/if}
					</td>
					<td class="muted">{s.repository.name}</td>
					<td>{s.type}</td>
					<td>{s.deploy_mode}</td>
					<td>
						<span class="revision">
							{#if s.last_good_revision}
								<code>{s.last_good_revision.commit.slice(0, 10)}</code>
							{:else}
								<Badge value="never deployed" tone="muted" />
							{/if}
							{#if behind(s)}<Badge value="newer in git" tone="warn" />{/if}
						</span>
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
					<td colspan="7" class="muted">
						No stacks yet. <a href="/repositories">Add a repository</a> with one folder per stack.
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}

<style>
	.name {
		font-weight: 600;
	}
	tr.removed td {
		opacity: 0.6;
	}
	.revision {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
	}
</style>
