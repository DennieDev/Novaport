<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { api, errorMessage } from '../api';
	import { STAGES, isActive, stageState } from '../deploy';
	import { onRow } from '../live.svelte';
	import { poll } from '../poll.svelte';
	import type { DeploymentDetail } from '../types';
	import Badge from './Badge.svelte';

	let {
		id,
		operator,
		ondismiss
	}: { id: string; operator: boolean; ondismiss: () => void } = $props();

	const deployment = poll<DeploymentDetail>(() => `/deployments/${id}`, 2000, {
		topics: ['deployments'],
		match: onRow(() => id)
	});

	let busy = $state(false);
	let error = $state<string | undefined>();

	const latest = $derived(deployment.data?.events.at(-1));

	async function act(action: 'approve' | 'cancel') {
		busy = true;
		error = undefined;
		try {
			await api.post(`/deployments/${id}/${action}`);
			await deployment.refresh();
		} catch (e) {
			error = errorMessage(e);
		} finally {
			busy = false;
		}
	}
</script>

{#if deployment.data}
	{@const d = deployment.data}
	<div class="panel progress" class:failed={d.status === 'failed'}>
		<div class="head">
			<strong>
				{isActive(d.status) ? 'Deploying' : 'Deploy of'}
				<code>{d.revision.commit.slice(0, 10)}</code>
			</strong>
			<span class="muted message">{d.revision.message}</span>
			<Badge value={d.status.replace('_', ' ')} />
			<span class="spacer"></span>
			{#if operator && d.status === 'awaiting_approval'}
				<button class="small" onclick={() => act('approve')} disabled={busy}>Approve</button>
			{/if}
			{#if operator && (d.status === 'awaiting_approval' || d.status === 'pending')}
				<button class="small" onclick={() => act('cancel')} disabled={busy}>Cancel</button>
			{/if}
			<a href="/deployments/{d.id}">Full log</a>
			{#if !isActive(d.status)}
				<button class="link" onclick={ondismiss}>close</button>
			{/if}
		</div>
		<ol class="stages">
			{#each STAGES as stage (stage)}
				<li class={stageState(d, stage)}>{stage}</li>
			{/each}
		</ol>
		{#if d.status === 'failed' && d.error}
			<pre class="error">{d.error}</pre>
		{:else if isActive(d.status) && latest}
			<pre class="muted">{latest.message}</pre>
		{:else if d.status === 'succeeded'}
			<p class="muted">Deployed.</p>
		{/if}
		{#if error}<p class="error">{error}</p>{/if}
	</div>
{/if}

<style>
	.progress {
		margin-bottom: 1rem;
	}
	.progress.failed {
		border-color: var(--bad);
	}
	.head {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		flex-wrap: wrap;
	}
	.message {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		max-width: 28rem;
	}
	.spacer {
		flex: 1;
	}
	.stages {
		display: flex;
		gap: 0.5rem;
		list-style: none;
		padding: 0;
		margin: 0.8rem 0 0;
		flex-wrap: wrap;
	}
	.stages li {
		padding: 0.15rem 0.7rem;
		border-radius: 999px;
		border: 1px solid var(--border);
		color: var(--muted);
		font-size: 0.85rem;
	}
	.stages li.done {
		color: var(--ok);
		border-color: currentColor;
	}
	.stages li.active {
		color: var(--warn);
		border-color: currentColor;
	}
	.stages li.failed {
		color: var(--bad);
		border-color: currentColor;
	}
	pre,
	p {
		margin: 0.7rem 0 0;
	}
	pre {
		white-space: pre-wrap;
		word-break: break-word;
		max-height: 12rem;
		overflow: auto;
	}
</style>
