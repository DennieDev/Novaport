<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { page } from '$app/state';
	import { api, ApiError, errorMessage } from '#lib/api.ts';
	import type { SaveResult, SecretsData, SopsInfo, Stack } from '#lib/types.ts';

	const id = $derived(page.params.id ?? '');

	let stack = $state<Stack | undefined>();
	let sops = $state<SopsInfo | undefined>();
	let base = $state('');
	let keys = $state<string[]>([]);
	let values = $state<Record<string, string> | null>(null);
	let edits = $state<Record<string, string>>({});
	let removed = $state<string[]>([]);
	let newKey = $state('');
	let message = $state('');
	let error = $state<string | undefined>();
	let saved = $state<SaveResult | undefined>();
	let busy = $state(false);

	const dirty = $derived(Object.keys(edits).length > 0 || removed.length > 0);

	async function load(reveal = false) {
		error = undefined;
		try {
			[stack, sops] = await Promise.all([
				api.get<Stack>(`/stacks/${id}`),
				api.get<SopsInfo>('/settings/sops')
			]);
			const data = await api.get<SecretsData>(`/stacks/${id}/secrets?reveal=${reveal}`);
			base = data.commit;
			keys = data.keys;
			values = data.values;
		} catch (e) {
			error = errorMessage(e);
		}
	}

	$effect(() => {
		id;
		load();
	});

	function add() {
		const key = newKey.trim();
		if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) {
			error = 'Use letters, digits and underscores, not starting with a digit.';
			return;
		}
		error = undefined;
		edits[key] = '';
		removed = removed.filter((k) => k !== key);
		newKey = '';
	}

	function remove(key: string) {
		delete edits[key];
		if (keys.includes(key)) removed = [...removed, key];
	}

	async function save() {
		busy = true;
		error = undefined;
		try {
			saved = await api.post<SaveResult>(`/stacks/${id}/secrets`, {
				base_commit: base,
				set: edits,
				remove: removed,
				message: message.trim() || null
			});
		} catch (e) {
			error =
				e instanceof ApiError && e.status === 409
					? 'The secrets changed in git since you loaded them. Reload and apply your change again.'
					: errorMessage(e);
		} finally {
			busy = false;
		}
	}

	const shown = $derived([...new Set([...keys, ...Object.keys(edits)])].sort());
</script>

<p><a href="/stacks/{id}">← {stack?.name ?? 'Stack'}</a></p>
<h1>Secrets{stack ? ` for ${stack.name}` : ''}</h1>

{#if sops && !sops.available}
	<p class="error">
		NOVAPORT_AGE_KEY is not set, so Novaport can't read or write secrets. Generate one with
		<code>novaport generate-age-key</code>.
	</p>
{:else if saved}
	<div class="panel">
		{#if saved.pull_request_url}
			<p>Opened a pull request: <a href={saved.pull_request_url}>{saved.pull_request_url}</a></p>
		{:else}
			<p>
				Committed <code>{saved.commit.slice(0, 10)}</code>; {saved.queued_deployments}
				deployment(s) queued.
			</p>
		{/if}
		<p><a href="/stacks/{id}">Back to the stack</a></p>
	</div>
{:else}
	<p class="muted">
		Stored in <code>{stack?.path}/secrets.env</code>, encrypted with SOPS. Reference a value from
		the compose file with <code>secrets: name: environment: KEY</code>, or as <code>${'{KEY}'}</code>.
	</p>

	<table>
		<thead>
			<tr>
				<th>Name</th>
				<th>
					Value
					{#if values === null && keys.length}
						<button class="link" onclick={() => load(true)}>reveal</button>
					{/if}
				</th>
				<th></th>
			</tr>
		</thead>
		<tbody>
			{#each shown as key (key)}
				<tr class:removed={removed.includes(key)}>
					<td><code>{key}</code></td>
					<td>
						{#if removed.includes(key)}
							<span class="muted">will be removed</span>
						{:else if key in edits}
							<input type="password" bind:value={edits[key]} placeholder="new value" autocomplete="off" />
						{:else if values}
							<code>{values[key]}</code>
							<button class="link" onclick={() => (edits[key] = values?.[key] ?? '')}>change</button>
						{:else}
							<span class="muted">••••••••</span>
							<button class="link" onclick={() => (edits[key] = '')}>change</button>
						{/if}
					</td>
					<td>
						{#if !removed.includes(key)}
							<button class="link" onclick={() => remove(key)}>remove</button>
						{/if}
					</td>
				</tr>
			{:else}
				<tr><td colspan="3" class="muted">No secrets yet.</td></tr>
			{/each}
		</tbody>
	</table>

	<p class="row">
		<input bind:value={newKey} placeholder="NEW_SECRET_NAME" />
		<button onclick={add}>Add</button>
	</p>

	<p class="row">
		<input class="grow" bind:value={message} placeholder="Commit message (optional)" />
		<button onclick={save} disabled={busy || !dirty}>Save</button>
	</p>
	{#if sops?.recipient}
		<p class="muted small">
			Novaport's public key: <code>{sops.recipient}</code>. Add it to the repository's
			<code>.sops.yaml</code> next to your own key so both of you can decrypt.
		</p>
	{/if}
{/if}
{#if error}<p class="error">{error}</p>{/if}

<style>
	.row {
		display: flex;
		gap: 0.75rem;
	}
	.grow {
		flex: 1;
	}
	tr.removed td {
		opacity: 0.6;
	}
	.small {
		font-size: 0.85rem;
	}
</style>
