<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { page } from '$app/state';
	import Crumbs from '#lib/components/Crumbs.svelte';
	import Terminal from '#lib/components/Terminal.svelte';

	const id = $derived(page.params.id ?? '');
</script>

<Crumbs
	items={[
		{ href: '/services', label: 'Services' },
		{ href: `/tasks/${id}`, label: 'Task' }
	]}
/>
<h1>Terminal</h1>
<Terminal path="/tasks/{id}/exec" />
