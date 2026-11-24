<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { page } from '$app/state';
	import Terminal from '#lib/components/Terminal.svelte';

	const id = $derived(page.params.id ?? '');
</script>

<p class="crumbs"><a href="/tasks/{id}">← Task</a></p>
<h1>Terminal</h1>
<Terminal path="/tasks/{id}/exec" />
