<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { page } from '$app/state';
	import { api, errorMessage } from '#lib/api.ts';
	import Badge from '#lib/components/Badge.svelte';
	import Status from '#lib/components/Status.svelte';
	import { ago } from '#lib/format.ts';
	import { poll } from '#lib/poll.svelte.ts';
	import type { DeploymentDetail } from '#lib/types.ts';

	const id = $derived(page.params.id ?? '');
	const deployment = poll<DeploymentDetail>(() => `/deployments/${id}`, 2000);

	let error = $state<string | undefined>();
	let busy = $state(false);

	const stages = ['resolve', 'validate', 'preflight', 'apply', 'converge'];

	function stageState(d: DeploymentDetail, stage: string): string {
		const current = d.stage ? stages.indexOf(d.stage) : -1;
		const index = stages.indexOf(stage);
		if (d.status === 'succeeded') return 'done';
		if (index < current) return 'done';
		if (index === current) return d.status === 'failed' ? 'failed' : 'active';
		return 'todo';
	}

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

<Status loading={deployment.loading && !deployment.data} error={deployment.error} />

{#if deployment.data}
	{@const d = deployment.data}
	<p><a href="/stacks/{d.stack_id}">← {d.stack_name}</a></p>
	<h1>
		Deploy {d.stack_name}
		<Badge value={d.status.replace('_', ' ')} />
	</h1>

	<div class="panel">
		<dl class="kv">
			<dt>Revision</dt>
			<dd><code>{d.revision.commit.slice(0, 10)}</code> {d.revision.message}</dd>
			<dt>Author</dt>
			<dd>{d.revision.author}</dd>
			<dt>Trigger</dt>
			<dd>{d.trigger}</dd>
			<dt>Queued</dt>
			<dd>{ago(d.created_at)}</dd>
			{#if d.finished_at}
				<dt>Finished</dt>
				<dd>{ago(d.finished_at)}</dd>
			{/if}
		</dl>
	</div>

	{#if d.status === 'awaiting_approval' || d.status === 'pending'}
		<p class="actions">
			{#if d.status === 'awaiting_approval'}
				<button onclick={() => act('approve')} disabled={busy}>Approve and deploy</button>
			{/if}
			<button onclick={() => act('cancel')} disabled={busy}>Cancel</button>
			{#if error}<span class="error">{error}</span>{/if}
		</p>
	{/if}

	<ol class="stages">
		{#each stages as stage (stage)}
			<li class={stageState(d, stage)}>{stage}</li>
		{/each}
	</ol>

	{#if d.status === 'failed' && d.error}
		<h2>Why it failed</h2>
		<pre class="panel failure">{d.error}</pre>
		<p class="muted">Nothing in git was changed or reverted.</p>
	{:else if d.status === 'cancelled' && d.error}
		<p class="muted">{d.error}</p>
	{/if}

	<h2>Log</h2>
	<table>
		<tbody>
			{#each d.events as e (e.id)}
				<tr>
					<td class="muted nowrap">{new Date(e.at).toLocaleTimeString()}</td>
					<td class="muted">{e.stage}</td>
					<td class:error={e.level === 'error'} class:warning={e.level === 'warning'}>
						<pre>{e.message}</pre>
					</td>
				</tr>
			{:else}
				<tr><td class="muted">Nothing yet.</td></tr>
			{/each}
		</tbody>
	</table>
{/if}

<style>
	h1 :global(.badge) {
		font-size: 0.85rem;
		vertical-align: middle;
		margin-left: 0.5rem;
	}
	.actions {
		display: flex;
		gap: 0.8rem;
		align-items: center;
	}
	.stages {
		display: flex;
		gap: 0.5rem;
		list-style: none;
		padding: 0;
		margin: 1.25rem 0;
	}
	.stages li {
		padding: 0.25rem 0.8rem;
		border-radius: 999px;
		border: 1px solid var(--border);
		color: var(--muted);
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
	pre {
		margin: 0;
		white-space: pre-wrap;
		word-break: break-word;
	}
	td.warning {
		color: var(--warn);
	}
	pre.failure {
		color: var(--bad);
	}
	.nowrap {
		white-space: nowrap;
	}
</style>
