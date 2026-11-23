<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api, ApiError } from '#lib/api.ts';
	import { logIn } from '#lib/session.svelte.ts';
	import type { ProviderLink } from '#lib/types.ts';

	let username = $state('');
	let password = $state('');
	let error = $state<string | undefined>(page.url.searchParams.get('error') ?? undefined);
	let busy = $state(false);
	let providers = $state<ProviderLink[]>([]);
	let showPassword = $state(false);

	$effect(() => {
		api
			.get<ProviderLink[]>('/auth/providers')
			.then((found) => (providers = found))
			.catch(() => (providers = []));
	});

	function nextPath(): string {
		const next = page.url.searchParams.get('next') ?? '/';
		// Only same-site paths, never `//host` or absolute URLs.
		return next.startsWith('/') && !next.startsWith('//') ? next : '/';
	}

	function providerUrl(p: ProviderLink): string {
		return `${p.login_url}?next=${encodeURIComponent(nextPath())}`;
	}

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		error = undefined;
		try {
			await logIn(username, password);
			await goto(nextPath());
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Could not reach the server.';
		} finally {
			busy = false;
		}
	}
</script>

<div class="wrap">
	<div class="panel box">
		<h1>Novaport</h1>
		{#each providers as p (p.slug)}
			<a class="provider" href={providerUrl(p)}>Log in with {p.display_name}</a>
		{/each}

		{#if providers.length && !showPassword}
			<button class="link" onclick={() => (showPassword = true)}>Log in with a password</button>
		{:else}
			<form onsubmit={submit}>
				<label>
					Username
					<input bind:value={username} autocomplete="username" required />
				</label>
				<label>
					Password
					<input type="password" bind:value={password} autocomplete="current-password" required />
				</label>
				<button type="submit" disabled={busy}>{busy ? 'Logging in…' : 'Log in'}</button>
			</form>
		{/if}
		{#if error}<p class="error">{error}</p>{/if}
	</div>
</div>

<style>
	.wrap {
		min-height: 100vh;
		display: grid;
		place-items: center;
	}
	.box,
	form {
		width: 320px;
		display: grid;
		gap: 0.9rem;
	}
	form {
		width: auto;
	}
	label {
		display: grid;
		gap: 0.3rem;
	}
	.provider {
		display: block;
		text-align: center;
		padding: 0.45rem 0.8rem;
		border: 1px solid var(--accent);
		border-radius: 5px;
	}
	.provider:hover {
		text-decoration: none;
		background: var(--code);
	}
	button.link {
		justify-self: center;
	}
	p {
		margin: 0;
	}
</style>
