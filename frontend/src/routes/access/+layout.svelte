<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { page } from '$app/state';
	import type { LayoutProps } from './$types';

	let { children }: LayoutProps = $props();

	const tabs = [
		{ href: '/access', label: 'Users' },
		{ href: '/access/groups', label: 'Groups' },
		{ href: '/access/bindings', label: 'Roles' },
		{ href: '/access/providers', label: 'Login providers' }
	];
</script>

<h1>Access</h1>
<nav class="tabs">
	{#each tabs as tab (tab.href)}
		<a href={tab.href} class:active={page.url.pathname === tab.href}>{tab.label}</a>
	{/each}
</nav>

{@render children()}

<style>
	.tabs {
		display: flex;
		gap: 1.25rem;
		border-bottom: 1px solid var(--border);
		margin-bottom: 1.25rem;
	}
	.tabs a {
		color: var(--fg);
		padding: 0.4rem 0;
		border-bottom: 2px solid transparent;
		margin-bottom: -1px;
	}
	.tabs a.active {
		color: var(--accent);
		border-bottom-color: var(--accent);
		font-weight: 600;
	}
	.tabs a:hover {
		text-decoration: none;
	}
</style>
