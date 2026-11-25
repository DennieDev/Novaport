<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { page } from '$app/state';
	import { api } from '#lib/api.ts';
	import Badge from '#lib/components/Badge.svelte';
	import Crumbs from '#lib/components/Crumbs.svelte';
	import Icon from '#lib/components/Icon.svelte';
	import LogViewer from '#lib/components/LogViewer.svelte';
	import Status from '#lib/components/Status.svelte';
	import { ago, shortId } from '#lib/format.ts';
	import { poll } from '#lib/poll.svelte.ts';
	import { canSomewhere } from '#lib/session.svelte.ts';
	import type { Service, Task } from '#lib/types.ts';

	const id = $derived(page.params.id ?? '');
	const task = poll<Task>(() => `/tasks/${id}`, 10_000);

	let serviceName = $state<string | undefined>();
	$effect(() => {
		const serviceId = task.data?.service_id;
		if (!serviceId) return;
		api
			.get<Service>(`/services/${serviceId}`)
			.then((s) => (serviceName = s.name))
			.catch(() => (serviceName = undefined));
	});
</script>

<Status loading={task.loading && !task.data} error={task.error} />

{#if task.data}
	{@const t = task.data}
	<Crumbs
		items={[
			{ href: '/services', label: 'Services' },
			{ href: `/services/${t.service_id}`, label: serviceName ?? 'Service' }
		]}
	/>
	<div class="page-header">
		<h1>
			Task {t.slot ?? shortId(t.id)}
			<Badge value={t.state} />
		</h1>
		{#if t.state === 'running' && canSomewhere('operator')}
			<div class="toolbar">
				<a class="button" href="/tasks/{t.id}/terminal"><Icon name="console" size={14} />Terminal</a>
			</div>
		{/if}
	</div>
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

	<h2>Container logs</h2>
	{#if t.container_id}
		<LogViewer source={`/tasks/${t.id}`} />
	{:else}
		<p class="muted">This task has no container.</p>
	{/if}
{/if}
