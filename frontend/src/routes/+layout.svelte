<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import favicon from '#lib/assets/favicon.svg';
	import { loadSession, logOut, session } from '#lib/session.svelte.ts';
	import type { LayoutProps } from './$types';

	let { children }: LayoutProps = $props();

	const links = [
		{ href: '/', label: 'Overview' },
		{ href: '/services', label: 'Services' },
		{ href: '/nodes', label: 'Nodes' },
		{ href: '/networks', label: 'Networks' },
		{ href: '/secrets', label: 'Secrets' },
		{ href: '/configs', label: 'Configs' }
	];

	const isLogin = $derived(page.url.pathname === '/login');

	$effect(() => {
		if (isLogin || session.checked) return;
		loadSession().then((user) => {
			if (!user) goto(`/login?next=${encodeURIComponent(page.url.pathname)}`);
		});
	});

	function active(href: string): boolean {
		const path = page.url.pathname;
		return href === '/' ? path === '/' : path === href || path.startsWith(`${href}/`);
	}

	async function signOut() {
		await logOut();
		await goto('/login');
	}
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
	<title>Novaport</title>
</svelte:head>

{#if isLogin}
	{@render children()}
{:else if session.user}
	<div class="shell">
		<nav>
			<a class="brand" href="/">Novaport</a>
			{#each links as link (link.href)}
				<a href={link.href} class:active={active(link.href)}>{link.label}</a>
			{/each}
			<div class="spacer"></div>
			<span class="muted">{session.user.username}</span>
			<button class="link" onclick={signOut}>Log out</button>
		</nav>
		<main>
			{@render children()}
		</main>
	</div>
{/if}

<style>
	:global {
		:root {
			--bg: #fbfbfc;
			--fg: #1c1d21;
			--panel: #ffffff;
			--border: #e1e3e8;
			--muted: #6b7080;
			--accent: #4b5bd6;
			--ok: #1d8a4e;
			--warn: #b26b00;
			--bad: #c4302b;
			--code: #f2f3f6;
			color-scheme: light dark;
		}
		@media (prefers-color-scheme: dark) {
			:root {
				--bg: #15161a;
				--fg: #e4e5ea;
				--panel: #1c1d22;
				--border: #2d2f36;
				--muted: #8d92a3;
				--accent: #8f9cff;
				--ok: #4cc98a;
				--warn: #e0a640;
				--bad: #f06a64;
				--code: #23252b;
			}
		}
		* {
			box-sizing: border-box;
		}
		body {
			margin: 0;
			background: var(--bg);
			color: var(--fg);
			font:
				14px/1.5 system-ui,
				sans-serif;
		}
		a {
			color: var(--accent);
			text-decoration: none;
		}
		a:hover {
			text-decoration: underline;
		}
		h1 {
			font-size: 1.4rem;
			margin: 0 0 1rem;
		}
		h2 {
			font-size: 1.05rem;
			margin: 1.75rem 0 0.6rem;
		}
		table {
			width: 100%;
			border-collapse: collapse;
			background: var(--panel);
			border: 1px solid var(--border);
		}
		th,
		td {
			text-align: left;
			padding: 0.45rem 0.7rem;
			border-bottom: 1px solid var(--border);
			vertical-align: top;
		}
		th {
			font-weight: 600;
			color: var(--muted);
			font-size: 0.8rem;
			text-transform: uppercase;
			letter-spacing: 0.03em;
		}
		code,
		pre {
			font-family: ui-monospace, monospace;
			font-size: 0.85rem;
		}
		code {
			background: var(--code);
			padding: 0.05rem 0.3rem;
			border-radius: 4px;
		}
		.muted {
			color: var(--muted);
		}
		.error {
			color: var(--bad);
		}
		.panel {
			background: var(--panel);
			border: 1px solid var(--border);
			border-radius: 6px;
			padding: 1rem;
		}
		dl.kv {
			display: grid;
			grid-template-columns: max-content 1fr;
			gap: 0.3rem 1.25rem;
			margin: 0;
		}
		dl.kv dt {
			color: var(--muted);
		}
		dl.kv dd {
			margin: 0;
		}
		button {
			font: inherit;
			padding: 0.35rem 0.8rem;
			border: 1px solid var(--border);
			background: var(--panel);
			color: var(--fg);
			border-radius: 5px;
			cursor: pointer;
		}
		button.link {
			border: none;
			background: none;
			color: var(--accent);
			padding: 0;
		}
		input {
			font: inherit;
			padding: 0.4rem 0.6rem;
			border: 1px solid var(--border);
			background: var(--panel);
			color: var(--fg);
			border-radius: 5px;
		}
	}

	.shell {
		min-height: 100vh;
	}
	nav {
		display: flex;
		align-items: center;
		gap: 1.1rem;
		padding: 0.7rem 1.5rem;
		border-bottom: 1px solid var(--border);
		background: var(--panel);
	}
	nav a {
		color: var(--fg);
	}
	nav a.active {
		color: var(--accent);
		font-weight: 600;
	}
	.brand {
		font-weight: 700;
		margin-right: 0.75rem;
	}
	.spacer {
		flex: 1;
	}
	main {
		padding: 1.5rem;
		max-width: 1400px;
	}
</style>
