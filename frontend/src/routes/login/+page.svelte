<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { ApiError } from '#lib/api.ts';
	import { logIn } from '#lib/session.svelte.ts';

	let username = $state('');
	let password = $state('');
	let error = $state<string | undefined>();
	let busy = $state(false);

	function nextPath(): string {
		const next = page.url.searchParams.get('next') ?? '/';
		// Only same-site paths, never `//host` or absolute URLs.
		return next.startsWith('/') && !next.startsWith('//') ? next : '/';
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
	<form class="panel" onsubmit={submit}>
		<h1>Novaport</h1>
		<label>
			Username
			<input bind:value={username} autocomplete="username" required />
		</label>
		<label>
			Password
			<input type="password" bind:value={password} autocomplete="current-password" required />
		</label>
		{#if error}<p class="error">{error}</p>{/if}
		<button type="submit" disabled={busy}>{busy ? 'Logging in…' : 'Log in'}</button>
	</form>
</div>

<style>
	.wrap {
		min-height: 100vh;
		display: grid;
		place-items: center;
	}
	form {
		width: 320px;
		display: grid;
		gap: 0.9rem;
	}
	label {
		display: grid;
		gap: 0.3rem;
	}
	p {
		margin: 0;
	}
</style>
