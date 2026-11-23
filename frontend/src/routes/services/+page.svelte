<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import Badge from '#lib/components/Badge.svelte';
	import Status from '#lib/components/Status.svelte';
	import { ago, degraded, replicas } from '#lib/format.ts';
	import { poll } from '#lib/poll.svelte.ts';
	import type { ServiceSummary } from '#lib/types.ts';

	const services = poll<ServiceSummary[]>(() => '/services');
	let filter = $state('');

	const visible = $derived(
		(services.data ?? []).filter((s) => {
			const q = filter.trim().toLowerCase();
			return !q || [s.name, s.stack ?? '', s.image].some((v) => v.toLowerCase().includes(q));
		})
	);
</script>

<h1>Services</h1>
<p><input placeholder="Filter by name, stack or image" bind:value={filter} /></p>
<Status loading={services.loading && !services.data} error={services.error} />

{#if services.data}
	<table>
		<thead>
			<tr>
				<th>Stack</th>
				<th>Service</th>
				<th>Image</th>
				<th>Mode</th>
				<th>Replicas</th>
				<th>Ports</th>
				<th>Updated</th>
			</tr>
		</thead>
		<tbody>
			{#each visible as s (s.id)}
				<tr>
					<td>{s.stack ?? '–'}</td>
					<td><a href="/services/{s.id}">{s.name}</a></td>
					<td><code>{s.image}</code></td>
					<td>{s.mode}</td>
					<td>
						{#if degraded(s)}
							<Badge value={replicas(s)} tone="bad" />
						{:else}
							{replicas(s)}
						{/if}
						{#if s.update_status?.state && s.update_status.state !== 'completed'}
							<Badge value={s.update_status.state} />
						{/if}
					</td>
					<td>
						{#each s.ports as p, i (i)}
							<div>{p.published ?? '–'}→{p.target}/{p.protocol}</div>
						{/each}
					</td>
					<td class="muted">{ago(s.updated_at)}</td>
				</tr>
			{:else}
				<tr><td colspan="7" class="muted">No services.</td></tr>
			{/each}
		</tbody>
	</table>
{/if}
