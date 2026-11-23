<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import Badge from './Badge.svelte';
	import { ago } from '../format';
	import type { ImageCheck } from '../types';

	let { checks, showStack = false }: { checks: ImageCheck[]; showStack?: boolean } = $props();

	const actions: Record<string, string> = {
		committed: 'committed',
		pr_opened: 'pull request opened',
		notified: 'notified',
		recorded: 'recorded',
		failed: 'failed'
	};

	function tagOf(ref: string | null): string {
		if (!ref) return '–';
		const name = ref.split('@')[0];
		const slash = name.lastIndexOf('/');
		const colon = name.lastIndexOf(':');
		const tag = colon > slash ? name.slice(colon + 1) : 'latest';
		const digest = ref.includes('@') ? ` @${ref.split('@')[1].replace('sha256:', '').slice(0, 12)}` : '';
		return tag + digest;
	}

	function policyText(c: ImageCheck): string {
		const p = c.policy;
		if (!p.mode) return '';
		const parts = [p.mode];
		if (p.mode === 'version') parts.push(`up to ${p.allow}`);
		if (p.action) parts.push(`then ${p.action}`);
		return parts.join(', ');
	}
</script>

<table>
	<thead>
		<tr>
			{#if showStack}<th>Stack</th>{/if}
			<th>Service</th>
			<th>In git</th>
			<th>Newer</th>
			<th>Policy</th>
			<th>Done</th>
			<th>Checked</th>
		</tr>
	</thead>
	<tbody>
		{#each checks as c (c.stack_id + c.service)}
			<tr>
				{#if showStack}<td><a href="/stacks/{c.stack_id}">{c.stack_name}</a></td>{/if}
				<td>{c.service}</td>
				<td title={c.current_ref ?? ''}><code>{tagOf(c.current_ref)}</code></td>
				<td>
					{#if c.status === 'available'}
						<code title={c.candidate_ref ?? ''}>{tagOf(c.candidate_ref)}</code>
					{:else if c.status === 'error'}
						<span class="error">{c.error}</span>
					{:else}
						<Badge value={c.status === 'current' ? 'up to date' : c.status} tone="muted" />
					{/if}
				</td>
				<td class="muted">{policyText(c)}</td>
				<td>
					{#if c.action_taken}
						{#if c.action_ref?.startsWith('http')}
							<a href={c.action_ref} target="_blank" rel="noreferrer">{actions[c.action_taken]}</a>
						{:else}
							{actions[c.action_taken]}
							{#if c.action_ref}<code>{c.action_ref.slice(0, 10)}</code>{/if}
						{/if}
						{#if c.action_taken === 'failed' && c.error && c.status !== 'error'}
							<div class="error small">{c.error}</div>
						{/if}
					{/if}
				</td>
				<td class="muted">{ago(c.checked_at)}</td>
			</tr>
		{/each}
	</tbody>
</table>

<style>
	.small {
		font-size: 0.85em;
	}
</style>
