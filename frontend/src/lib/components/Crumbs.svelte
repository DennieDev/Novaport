<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import Icon from './Icon.svelte';

	// The pages above this one, from the top. The current page is its heading, not a crumb.
	let { items }: { items: { href: string; label: string }[] } = $props();
</script>

<nav class="crumbs" aria-label="Breadcrumb">
	{#each items as item, i (item.href)}
		{#if i}<Icon name="chevron" size={12} />{/if}
		<a href={item.href}>{item.label}</a>
	{/each}
</nav>
