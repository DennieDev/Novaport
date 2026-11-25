<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { api, errorMessage } from '#lib/api.ts';
	import Status from '#lib/components/Status.svelte';
	import { ago } from '#lib/format.ts';
	import { poll } from '#lib/poll.svelte.ts';
	import type { NotificationChannel, NotificationSubscription, Stack } from '#lib/types.ts';

	const channels = poll<NotificationChannel[]>(() => '/notification-channels', 30_000);
	const stacks = poll<Stack[]>(() => '/stacks', 60_000, { topics: ['stacks'] });

	const events: { value: string; label: string }[] = [
		{ value: '*', label: 'Everything' },
		{ value: 'deploy.failed', label: 'Deploy failed' },
		{ value: 'deploy.succeeded', label: 'Deploy succeeded' },
		{ value: 'deploy.awaiting_approval', label: 'Waiting for approval' },
		{ value: 'drift.detected', label: 'Drift detected' },
		{ value: 'update.available', label: 'Image update available' },
		{ value: 'update.committed', label: 'Image update committed' },
		{ value: 'update.pr_opened', label: 'Image update PR opened' },
		{ value: 'node.down', label: 'Node down' },
		{ value: 'agent.unreachable', label: 'Agent unreachable' },
		{ value: 'endpoint.disconnected', label: 'Endpoint disconnected' }
	];
	const labels = Object.fromEntries(events.map((e) => [e.value, e.label]));

	interface Form {
		name: string;
		kind: NotificationChannel['kind'];
		enabled: boolean;
		url: string;
		signing_secret: string;
		recipients: string;
		subscriptions: NotificationSubscription[];
	}

	function blank(): Form {
		return {
			name: '',
			kind: 'slack',
			enabled: true,
			url: '',
			signing_secret: '',
			recipients: '',
			subscriptions: [{ event: 'deploy.failed', stack_id: null }]
		};
	}

	let editing = $state<string | null>(null);
	let form = $state<Form>(blank());
	let existing = $state<NotificationChannel | undefined>();
	let error = $state<string | undefined>();
	let busy = $state(false);
	let tests = $state<Record<string, { ok: boolean; error: string | null }>>({});

	function startAdd() {
		editing = '';
		existing = undefined;
		form = blank();
		error = undefined;
	}

	function startEdit(c: NotificationChannel) {
		editing = c.id;
		existing = c;
		form = {
			name: c.name,
			kind: c.kind,
			enabled: c.enabled,
			url: '',
			signing_secret: '',
			recipients: c.recipients ?? '',
			subscriptions: c.subscriptions.map((s) => ({ event: s.event, stack_id: s.stack_id }))
		};
		error = undefined;
	}

	async function save(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		error = undefined;
		const subscriptions = form.subscriptions.map((s) => ({
			event: s.event,
			stack_id: s.stack_id || null
		}));
		try {
			if (editing) {
				await api.patch(`/notification-channels/${editing}`, {
					name: form.name,
					enabled: form.enabled,
					url: form.url || null,
					signing_secret: form.signing_secret || null,
					recipients: form.kind === 'email' ? form.recipients : null,
					subscriptions
				});
			} else {
				await api.post('/notification-channels', {
					...form,
					url: form.kind === 'email' ? null : form.url,
					signing_secret: form.kind === 'webhook' && form.signing_secret ? form.signing_secret : null,
					recipients: form.kind === 'email' ? form.recipients : null,
					subscriptions
				});
			}
			editing = null;
			await channels.refresh();
		} catch (e) {
			error = errorMessage(e);
		} finally {
			busy = false;
		}
	}

	async function remove(c: NotificationChannel) {
		if (!confirm(`Delete the channel ${c.name}?`)) return;
		try {
			await api.delete(`/notification-channels/${c.id}`);
			await channels.refresh();
		} catch (e) {
			error = errorMessage(e);
		}
	}

	async function test(c: NotificationChannel) {
		tests[c.id] = await api.post(`/notification-channels/${c.id}/test`);
	}

	function describe(s: NotificationSubscription): string {
		return `${labels[s.event] ?? s.event} ${s.stack_name ? `on ${s.stack_name}` : ''}`.trim();
	}
</script>

<h1>Notifications</h1>
<p class="muted">
	Send events to a webhook, a Slack incoming webhook, or email (set the
	<code>SWARMER_SMTP_*</code> variables first). Each channel picks the events it wants, for all
	stacks or for one.
</p>

<Status loading={channels.loading && !channels.data} error={channels.error} />

{#each channels.data ?? [] as c (c.id)}
	<div class="panel channel">
		<div class="head">
			<strong>{c.name}</strong>
			<span class="muted">{c.kind}{c.enabled ? '' : ', disabled'}</span>
			<span class="spacer"></span>
			<button class="link" onclick={() => test(c)}>send test</button>
			<button class="link" onclick={() => startEdit(c)}>edit</button>
			<button class="link" onclick={() => remove(c)}>delete</button>
		</div>
		<p>
			{#each c.subscriptions as s (s.event + s.stack_id)}<span class="chip">{describe(s)}</span>{:else}
				<span class="muted">Subscribed to nothing.</span>
			{/each}
		</p>
		<p class="muted small">
			{#if c.kind === 'email'}To {c.recipients}.{/if}
			Last sent {ago(c.last_sent_at)}.
			{#if c.last_error}<span class="error">Last error: {c.last_error}</span>{/if}
		</p>
		{#if tests[c.id]}
			{#if tests[c.id].ok}<p class="ok">Test message sent.</p>
			{:else}<p class="error">{tests[c.id].error}</p>{/if}
		{/if}
	</div>
{:else}
	{#if channels.data}<p class="muted">No channels yet.</p>{/if}
{/each}

{#if editing === null}
	<p><button onclick={startAdd}>Add channel</button></p>
{:else}
	<h2>{editing ? `Edit ${existing?.name}` : 'New channel'}</h2>
	<form class="panel" onsubmit={save}>
		<div class="grid">
			<label>Name <input bind:value={form.name} required maxlength="100" /></label>
			<label>
				Kind
				<select bind:value={form.kind} disabled={!!editing}>
					<option value="slack">Slack (incoming webhook)</option>
					<option value="webhook">Webhook (JSON POST)</option>
					<option value="email">Email</option>
				</select>
			</label>
			{#if form.kind === 'email'}
				<label>
					Recipients (comma-separated)
					<input bind:value={form.recipients} required placeholder="ops@example.com" />
				</label>
			{:else}
				<label>
					URL {existing?.has_url ? '(empty = keep the current one)' : ''}
					<input
						bind:value={form.url}
						type="url"
						required={!existing?.has_url}
						placeholder="https://hooks.slack.com/services/…"
						autocomplete="off"
					/>
				</label>
			{/if}
			{#if form.kind === 'webhook'}
				<label>
					Signing secret (optional{existing?.has_signing_secret ? ', empty = keep' : ''})
					<input type="password" bind:value={form.signing_secret} autocomplete="off" />
				</label>
			{/if}
			<label class="check"><input type="checkbox" bind:checked={form.enabled} /> Enabled</label>
		</div>

		<h3>Events</h3>
		{#each form.subscriptions as s, i (i)}
			<p class="row">
				<select bind:value={s.event}>
					{#each events as e (e.value)}<option value={e.value}>{e.label}</option>{/each}
				</select>
				<select bind:value={s.stack_id}>
					<option value={null}>All stacks</option>
					{#each stacks.data ?? [] as st (st.id)}<option value={st.id}>{st.name}</option>{/each}
				</select>
				<button type="button" class="link" onclick={() => form.subscriptions.splice(i, 1)}>
					remove
				</button>
			</p>
		{/each}
		<p>
			<button type="button" onclick={() => form.subscriptions.push({ event: '*', stack_id: null })}>
				Add event
			</button>
		</p>
		{#if form.kind === 'webhook'}
			<p class="muted small">
				Webhooks get a JSON body with <code>event</code>, <code>title</code>, <code>text</code>,
				<code>stack</code>, <code>link</code>, and <code>data</code>. With a signing secret, the
				<code>X-Swarmer-Signature</code> header carries <code>sha256=</code> and the HMAC of the body.
			</p>
		{/if}

		{#if error}<p class="error">{error}</p>{/if}
		<p class="row">
			<button type="submit" disabled={busy}>{editing ? 'Save' : 'Add'}</button>
			<button type="button" class="link" onclick={() => (editing = null)}>cancel</button>
		</p>
	</form>
{/if}

<style>
	.channel {
		margin-bottom: 0.9rem;
	}
	.channel p {
		margin: 0.4rem 0 0;
	}
	.head {
		display: flex;
		gap: 0.9rem;
		align-items: baseline;
	}
	.spacer {
		flex: 1;
	}
	.chip {
		display: inline-block;
		background: var(--code);
		border-radius: 4px;
		padding: 0 0.4rem;
		margin: 0 0.3rem 0.2rem 0;
	}
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
		gap: 0.9rem;
	}
	label {
		display: grid;
		gap: 0.3rem;
	}
	label.check {
		display: flex;
		align-items: center;
		gap: 0.4rem;
	}
	h3 {
		font-size: 0.95rem;
		margin: 1.25rem 0 0.25rem;
	}
	.row {
		display: flex;
		gap: 0.6rem;
		align-items: center;
	}
	.small {
		font-size: 0.85em;
	}
	.ok {
		color: var(--ok);
	}
</style>
