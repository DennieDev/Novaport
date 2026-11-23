<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import Status from '#lib/components/Status.svelte';
	import { poll } from '#lib/poll.svelte.ts';
	import type { Network } from '#lib/types.ts';

	const networks = poll<Network[]>(() => '/networks', 30_000);
</script>

<h1>Networks</h1>
<Status loading={networks.loading && !networks.data} error={networks.error} />

{#if networks.data}
	<table>
		<thead>
			<tr>
				<th>Name</th>
				<th>Stack</th>
				<th>Driver</th>
				<th>Scope</th>
				<th>Subnets</th>
				<th>Flags</th>
			</tr>
		</thead>
		<tbody>
			{#each networks.data as n (n.id)}
				<tr>
					<td>{n.name}</td>
					<td>{n.stack ?? '–'}</td>
					<td>{n.driver}</td>
					<td>{n.scope}</td>
					<td>
						{#each n.subnets as s, i (i)}
							<div><code>{s.subnet ?? '–'}</code> <span class="muted">{s.gateway ?? ''}</span></div>
						{/each}
					</td>
					<td class="muted">
						{[n.ingress && 'ingress', n.internal && 'internal', n.attachable && 'attachable']
							.filter(Boolean)
							.join(', ')}
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}
