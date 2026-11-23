<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import Badge from '#lib/components/Badge.svelte';
	import Status from '#lib/components/Status.svelte';
	import { bytes } from '#lib/format.ts';
	import { poll } from '#lib/poll.svelte.ts';
	import type { Node } from '#lib/types.ts';

	const nodes = poll<Node[]>(() => '/nodes');
</script>

<h1>Nodes</h1>
<Status loading={nodes.loading && !nodes.data} error={nodes.error} />

{#if nodes.data}
	<table>
		<thead>
			<tr>
				<th>Hostname</th>
				<th>Role</th>
				<th>State</th>
				<th>Availability</th>
				<th>Address</th>
				<th>Engine</th>
				<th>Resources</th>
				<th>Labels</th>
			</tr>
		</thead>
		<tbody>
			{#each nodes.data as n (n.id)}
				<tr>
					<td><a href="/nodes/{n.id}">{n.hostname}</a></td>
					<td>
						{n.role}
						{#if n.leader}<Badge value="leader" tone="ok" />{/if}
						{#if n.manager_reachability && n.manager_reachability !== 'reachable'}
							<Badge value={n.manager_reachability} />
						{/if}
					</td>
					<td>
						<Badge value={n.state} />
						{#if n.message}<div class="muted">{n.message}</div>{/if}
					</td>
					<td><Badge value={n.availability} /></td>
					<td>{n.address ?? '–'}</td>
					<td>{n.engine_version ?? '–'}</td>
					<td>{n.cpus ?? '–'} CPUs · {bytes(n.memory_bytes)}</td>
					<td>
						{#each Object.entries(n.labels) as [k, v] (k)}
							<div><code>{k}={v}</code></div>
						{/each}
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}
