<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { api, errorMessage } from '#lib/api.ts';
	import { session } from '#lib/session.svelte.ts';

	let current = $state('');
	let next = $state('');
	let repeat = $state('');
	let busy = $state(false);
	let error = $state<string | undefined>();
	let done = $state(false);

	const roles = $derived(Object.values(session.user?.stack_roles ?? {}));

	async function change(event: SubmitEvent) {
		event.preventDefault();
		error = undefined;
		done = false;
		if (next !== repeat) {
			error = "The new passwords don't match.";
			return;
		}
		busy = true;
		try {
			await api.post('/auth/password', { current_password: current, new_password: next });
			done = true;
			current = next = repeat = '';
		} catch (e) {
			error = errorMessage(e);
		} finally {
			busy = false;
		}
	}
</script>

<h1>Account</h1>

{#if session.user}
	{@const u = session.user}
	<div class="panel">
		<dl class="kv">
			<dt>Username</dt>
			<dd>{u.username}</dd>
			{#if u.name}<dt>Name</dt><dd>{u.name}</dd>{/if}
			{#if u.email}<dt>Email</dt><dd>{u.email}</dd>{/if}
			<dt>Role</dt>
			<dd>
				{#if u.global_role}{u.global_role} on everything{:else}no global role{/if}
				{#if roles.length}<span class="muted">· roles on {roles.length} stack{roles.length === 1 ? '' : 's'}</span>{/if}
			</dd>
			<dt>Signs in with</dt>
			<dd>{u.local ? 'a password' : 'a login provider'}</dd>
		</dl>
	</div>

	<h2>Password</h2>
	{#if u.local}
		<form class="panel form" onsubmit={change}>
			<label>
				Current password
				<input type="password" bind:value={current} required autocomplete="current-password" />
			</label>
			<label>
				New password
				<input type="password" bind:value={next} required minlength="12" autocomplete="new-password" />
			</label>
			<label>
				Repeat new password
				<input type="password" bind:value={repeat} required minlength="12" autocomplete="new-password" />
			</label>
			<p class="muted">At least 12 characters. Your other sessions are logged out.</p>
			<div>
				<button class="primary" type="submit" disabled={busy}>Change password</button>
			</div>
			{#if error}<p class="error">{error}</p>{/if}
			{#if done}<p class="ok">Password changed.</p>{/if}
		</form>
	{:else}
		<p class="muted">Your password is managed by your login provider.</p>
	{/if}
{/if}

<style>
	.form {
		display: grid;
		gap: 0.85rem;
		max-width: 26rem;
	}
	label {
		display: grid;
		gap: 0.3rem;
		font-weight: 500;
	}
	p {
		margin: 0;
	}
	.ok {
		color: var(--ok);
	}
</style>
