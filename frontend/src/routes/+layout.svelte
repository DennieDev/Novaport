<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import favicon from '#lib/assets/favicon.svg';
	import Icon, { type IconName } from '#lib/components/Icon.svelte';
	import Logo from '#lib/components/Logo.svelte';
	import { live, subscribe } from '#lib/live.svelte.ts';
	import { can, loadSession, logOut, session } from '#lib/session.svelte.ts';
	import '#lib/styles.css';
	import { setTheme, theme, type Theme } from '#lib/theme.svelte.ts';
	import type { Role } from '#lib/types.ts';
	import type { LayoutProps } from './$types';

	let { children }: LayoutProps = $props();

	interface Link {
		href: string;
		label: string;
		icon: IconName;
		// null means any logged-in user; pages filter to the stacks they may see.
		role: Role | null;
	}

	const groups: { title: string | null; links: Link[] }[] = [
		{
			title: null,
			links: [
				{ href: '/', label: 'Overview', icon: 'overview', role: null },
				{ href: '/stacks', label: 'Stacks', icon: 'stacks', role: null },
				{ href: '/services', label: 'Services', icon: 'services', role: null }
			]
		},
		{
			title: 'Cluster',
			links: [
				{ href: '/nodes', label: 'Nodes', icon: 'nodes', role: 'viewer' },
				{ href: '/networks', label: 'Networks', icon: 'networks', role: 'viewer' },
				{ href: '/secrets', label: 'Secrets', icon: 'secrets', role: 'viewer' },
				{ href: '/configs', label: 'Configs', icon: 'configs', role: 'viewer' },
				{ href: '/endpoints', label: 'Endpoints', icon: 'endpoints', role: 'viewer' }
			]
		},
		{
			title: 'Sources',
			links: [
				{ href: '/repositories', label: 'Repositories', icon: 'repositories', role: 'admin' },
				{ href: '/registries', label: 'Registries', icon: 'registries', role: 'admin' }
			]
		},
		{
			title: 'Settings',
			links: [
				{ href: '/notifications', label: 'Notifications', icon: 'notifications', role: 'admin' },
				{ href: '/access', label: 'Access', icon: 'access', role: 'admin' },
				{ href: '/audit', label: 'Audit log', icon: 'audit', role: 'admin' }
			]
		}
	];

	const visibleGroups = $derived(
		session.user
			? groups
					.map((g) => ({ ...g, links: g.links.filter((l) => l.role === null || can(l.role)) }))
					.filter((g) => g.links.length > 0)
			: []
	);

	const roleLabel = $derived.by(() => {
		const role = session.user?.global_role;
		if (role) return role.charAt(0).toUpperCase() + role.slice(1);
		return Object.keys(session.user?.stack_roles ?? {}).length ? 'Stack roles' : 'No role';
	});

	const isLogin = $derived(page.url.pathname === '/login');
	let menuOpen = $state(false);

	$effect(() => {
		if (isLogin || session.checked) return;
		loadSession().then((user) => {
			if (!user) goto(`/login?next=${encodeURIComponent(page.url.pathname)}`);
		});
	});

	$effect(() => {
		page.url.pathname;
		menuOpen = false;
	});

	// Keeps the tab's event stream open, so the indicator shows its real state on every page.
	$effect(() => {
		if (session.user) return subscribe(() => {});
	});

	function active(href: string): boolean {
		const path = page.url.pathname;
		return href === '/' ? path === '/' : path === href || path.startsWith(`${href}/`);
	}

	const THEMES: { value: Theme; icon: IconName; label: string }[] = [
		{ value: 'system', icon: 'monitor', label: 'Theme follows the system' },
		{ value: 'light', icon: 'sun', label: 'Light theme' },
		{ value: 'dark', icon: 'moon', label: 'Dark theme' }
	];
	const currentTheme = $derived(THEMES.find((t) => t.value === theme.current) ?? THEMES[0]);

	function nextTheme() {
		const index = THEMES.findIndex((t) => t.value === theme.current);
		setTheme(THEMES[(index + 1) % THEMES.length].value);
	}

	async function signOut() {
		await logOut();
		await goto('/login');
	}
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
	<title>Swarmer</title>
</svelte:head>

{#if isLogin}
	{@render children()}
{:else if session.user}
	<div class="shell">
		<header class="topbar">
			<button class="menu" aria-label="Menu" onclick={() => (menuOpen = !menuOpen)}>
				<Icon name="menu" size={18} />
			</button>
			<a class="brand" href="/" aria-label="Swarmer"><Logo size={20} name /></a>
		</header>

		<aside class:open={menuOpen}>
			<a class="brand" href="/" aria-label="Swarmer"><Logo size={22} name /></a>
			<nav>
				{#each visibleGroups as group (group.title)}
					{#if group.title}<div class="group">{group.title}</div>{/if}
					{#each group.links as link (link.href)}
						<a href={link.href} class:active={active(link.href)}>
							<Icon name={link.icon} />
							{link.label}
						</a>
					{/each}
				{/each}
			</nav>
			<div class="account">
				<a class="who" href="/account" title="Account and password">
					<span class="name">{session.user.name ?? session.user.username}</span>
					<span class="role">
						{roleLabel}
						<span class="dot">·</span>
						<span
							class="live"
							class:on={live.connected}
							title={live.connected
								? 'Pages update as soon as something changes'
								: 'Live updates are offline; pages refresh every few seconds'}
						>
							{live.connected ? 'live' : 'offline'}
						</span>
					</span>
				</a>
				<button
					class="small"
					onclick={nextTheme}
					title="{currentTheme.label} (click to change)"
					aria-label="{currentTheme.label} (click to change)"
				>
					<Icon name={currentTheme.icon} size={14} />
				</button>
				<button class="small" onclick={signOut} title="Log out">
					<Icon name="logout" size={14} />
				</button>
			</div>
		</aside>
		{#if menuOpen}
			<button class="scrim" aria-label="Close menu" onclick={() => (menuOpen = false)}></button>
		{/if}

		<main>
			{@render children()}
		</main>
	</div>
{/if}

<style>
	.shell {
		min-height: 100vh;
	}
	aside {
		position: fixed;
		inset: 0 auto 0 0;
		width: 232px;
		display: flex;
		flex-direction: column;
		background: var(--sidebar);
		border-right: 1px solid var(--border);
		z-index: 20;
	}
	.brand {
		display: flex;
		align-items: center;
		gap: 0.55rem;
		font-weight: 700;
		font-size: 1.05rem;
		color: var(--fg);
		letter-spacing: -0.01em;
	}
	aside > .brand {
		padding: 1.1rem 1.25rem 1rem;
	}
	.brand:hover {
		text-decoration: none;
	}
	nav {
		flex: 1;
		overflow-y: auto;
		padding: 0.25rem 0.75rem 1rem;
	}
	.group {
		margin: 1.1rem 0.5rem 0.35rem;
		font-size: 0.7rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--muted);
	}
	nav a {
		display: flex;
		align-items: center;
		gap: 0.65rem;
		padding: 0.42rem 0.6rem;
		margin: 1px 0;
		border-radius: 6px;
		color: var(--fg);
		font-weight: 500;
	}
	nav a :global(svg) {
		color: var(--muted);
	}
	nav a:hover {
		background: var(--panel-alt);
		text-decoration: none;
	}
	nav a.active {
		background: var(--accent-soft);
		color: var(--accent);
	}
	nav a.active :global(svg) {
		color: var(--accent);
	}
	.account {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0.85rem 1rem;
		border-top: 1px solid var(--border);
	}
	.who {
		flex: 1;
		min-width: 0;
		display: flex;
		flex-direction: column;
		color: var(--fg);
		border-radius: 6px;
		padding: 0.2rem 0.35rem;
		margin: -0.2rem -0.35rem;
	}
	.who:hover {
		background: var(--panel-alt);
		text-decoration: none;
	}
	.name {
		font-weight: 600;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.role {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		font-size: 0.8rem;
		color: var(--muted);
	}
	.live {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
	}
	.live::before {
		content: '';
		width: 7px;
		height: 7px;
		border-radius: 50%;
		background: var(--border-strong);
	}
	.live.on::before {
		background: var(--ok);
	}
	main {
		margin-left: 232px;
		padding: 1.75rem 2.25rem 3rem;
	}
	.topbar {
		display: none;
	}
	.scrim {
		display: none;
	}

	@media (max-width: 900px) {
		.topbar {
			position: sticky;
			top: 0;
			z-index: 10;
			display: flex;
			align-items: center;
			gap: 0.75rem;
			padding: 0.6rem 1rem;
			background: var(--sidebar);
			border-bottom: 1px solid var(--border);
		}
		.menu {
			padding: 0.3rem;
		}
		aside {
			transform: translateX(-100%);
			transition: transform 0.18s ease;
			box-shadow: 0 0 24px rgb(0 0 0 / 0.15);
		}
		aside.open {
			transform: none;
		}
		.scrim,
		.scrim:hover:not(:disabled) {
			display: block;
			position: fixed;
			inset: 0;
			z-index: 15;
			border: none;
			border-radius: 0;
			background: rgb(0 0 0 / 0.3);
		}
		main {
			margin-left: 0;
			padding: 1.25rem 1rem 2.5rem;
		}
	}
</style>
