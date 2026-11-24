<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { page } from '$app/state';
	import Badge from '#lib/components/Badge.svelte';
	import LogViewer from '#lib/components/LogViewer.svelte';
	import Status from '#lib/components/Status.svelte';
	import { ago, shortId } from '#lib/format.ts';
	import { poll } from '#lib/poll.svelte.ts';
	import { canSomewhere } from '#lib/session.svelte.ts';
	import type { Task } from '#lib/types.ts';

	const id = $derived(page.params.id ?? '');
	const task = poll<Task>(() => `/tasks/${id}`, 10_000);
</script>

<Status loading={task.loading && !task.data} error={task.error} />

{#if task.data}
	{@const t = task.data}
	<p class="crumbs"><a href="/services/{t.service_id}">← Service</a></p>
	<h1>
		Task {t.slot ?? shortId(t.id)}
		<Badge value={t.state} />
	</h1>
	<div class="panel">
		<dl class="kv">
			<dt>Node</dt>
			<dd>{#if t.node_id}<a href="/nodes/{t.node_id}">{t.node ?? t.node_id}</a>{:else}–{/if}</dd>
			<dt>Container</dt>
			<dd><code>{shortId(t.container_id)}</code></dd>
			<dt>Image</dt>
			<dd><code>{t.image}</code></dd>
			<dt>Updated</dt>
			<dd>{ago(t.updated_at)}</dd>
			{#if t.error}
				<dt>Error</dt>
				<dd class="error">{t.error}</dd>
			{/if}
		</dl>
	</div>

	{#if t.state === 'running' && canSomewhere('operator')}
		<p><a href="/tasks/{t.id}/terminal">Open a terminal</a></p>
	{/if}

	<h2>Container logs</h2>
	{#if t.container_id}
		<LogViewer source={`/tasks/${t.id}`} />
	{:else}
		<p class="muted">This task has no container.</p>
	{/if}
{/if}
