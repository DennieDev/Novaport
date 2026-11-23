<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import Badge from './Badge.svelte';
	import { ago } from '../format';
	import type { Deployment } from '../types';

	let { d, showStack = false }: { d: Deployment; showStack?: boolean } = $props();
</script>

<tr>
	<td><a href="/deployments/{d.id}"><Badge value={d.status.replace('_', ' ')} /></a></td>
	{#if showStack}<td><a href="/stacks/{d.stack_id}">{d.stack_name}</a></td>{/if}
	<td>
		<code>{d.revision.commit.slice(0, 10)}</code>
		<span class="muted">{d.revision.message}</span>
	</td>
	<td class="muted">{d.trigger}</td>
	<td class="muted">{ago(d.created_at)}</td>
	<td class="error">{d.status === 'failed' ? (d.error ?? '').split('\n')[0] : ''}</td>
</tr>
