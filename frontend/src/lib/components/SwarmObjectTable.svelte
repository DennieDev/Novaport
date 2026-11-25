<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { ago } from '../format';
	import type { SwarmObject } from '../types';

	let { items, empty }: { items: SwarmObject[]; empty: string } = $props();
</script>

<table>
	<thead>
		<tr><th>Name</th><th>Stack</th><th>Created</th><th>Updated</th></tr>
	</thead>
	<tbody>
		{#each items as item (item.id)}
			<tr>
				<td><code>{item.name}</code></td>
				<td>{item.stack ?? '–'}</td>
				<td class="muted">{ago(item.created_at)}</td>
				<td class="muted">{ago(item.updated_at)}</td>
			</tr>
		{:else}
			<tr><td colspan="4" class="muted">{empty}</td></tr>
		{/each}
	</tbody>
</table>
