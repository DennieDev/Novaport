<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { api, errorMessage } from '#lib/api.ts';
	import Badge from '#lib/components/Badge.svelte';
	import Status from '#lib/components/Status.svelte';
	import { ago } from '#lib/format.ts';
	import { poll } from '#lib/poll.svelte.ts';
	import { can } from '#lib/session.svelte.ts';
	import type { Endpoint, EndpointKey } from '#lib/types.ts';

	const endpoints = poll<Endpoint[]>(() => '/endpoints', 10_000, { topics: ['endpoints'] });

	let name = $state('');
	let error = $state<string | undefined>();
	let issued = $state<EndpointKey | undefined>();

	async function run(action: () => Promise<unknown>) {
		error = undefined;
		try {
			await action();
			await endpoints.refresh();
		} catch (e) {
			error = errorMessage(e);
		}
	}

	function create(event: SubmitEvent) {
		event.preventDefault();
		return run(async () => {
			issued = await api.post<EndpointKey>('/endpoints', { name });
			name = '';
		});
	}

	function rotate(e: Endpoint) {
		if (!confirm(`Issue a new key for ${e.name}? The agent using the old key is disconnected.`))
			return;
		return run(async () => {
			issued = await api.post<EndpointKey>(`/endpoints/${e.id}/key`);
		});
	}

	function remove(e: Endpoint) {
		if (!confirm(`Delete the endpoint ${e.name}?`)) return;
		return run(() => api.delete(`/endpoints/${e.id}`));
	}
</script>

<h1>Endpoints</h1>
<p class="muted">
	Docker hosts and Swarm clusters outside the one Swarmer runs on. Each runs an agent that
	connects out to Swarmer, so the host needs no open ports. Pick one for a stack with
	<code>endpoint: name</code> in its <code>swarmer.yaml</code>; without it, a stack deploys to
	<code>local</code>, the Swarm or host Swarmer itself runs on.
</p>

<Status loading={endpoints.loading && !endpoints.data} error={endpoints.error} />
{#if error}<p class="error">{error}</p>{/if}

{#if issued}
	<div class="panel issued">
		<strong>Start the agent for {issued.endpoint.name}</strong>
		<p class="muted">
			The key is shown only now. On a single Docker host:
		</p>
		<pre>{issued.docker_command}</pre>
		<p class="muted">On a Swarm cluster, run this on a manager:</p>
		<pre>{issued.swarm_command}</pre>
		<p class="muted">
			The agent needs to reach Swarmer at the address in the key. Set
			<code>SWARMER_PUBLIC_URL</code> if Swarmer is behind a proxy.
		</p>
		<button class="link" onclick={() => (issued = undefined)}>done</button>
	</div>
{/if}

{#if endpoints.data}
	{#if endpoints.data.length}
		<table>
			<thead>
				<tr><th>Name</th><th>Status</th><th>Host</th><th>Docker</th><th>Stacks</th><th></th></tr>
			</thead>
			<tbody>
				{#each endpoints.data as e (e.id)}
					<tr>
						<td><strong>{e.name}</strong></td>
						<td>
							{#if e.connected}
								<Badge value="connected" tone="ok" />
								<span class="muted">since {ago(e.connected_at)}</span>
							{:else if e.connected_at}
								<Badge value="disconnected" tone="bad" />
								<span class="muted">{ago(e.disconnected_at)}</span>
							{:else}
								<span class="muted">waiting for the agent</span>
							{/if}
						</td>
						<td>
							{e.hostname ?? '–'}
							{#if e.swarm}<span class="muted">(Swarm manager)</span>{/if}
						</td>
						<td class="muted">
							{e.docker_version ?? '–'}{#if e.agent_version}, agent {e.agent_version}{/if}
						</td>
						<td>{e.stacks.join(', ') || '–'}</td>
						<td class="actions">
							{#if can('admin')}
								<button class="link" onclick={() => rotate(e)}>new key</button>
								<button class="link" onclick={() => remove(e)} disabled={e.stacks.length > 0}>
									delete
								</button>
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{:else}
		<p class="muted">No endpoints yet.</p>
	{/if}
{/if}

{#if can('admin')}
	<h2>Add an endpoint</h2>
	<form class="row" onsubmit={create}>
		<input
			bind:value={name}
			required
			pattern="[a-z0-9][a-z0-9_-]*"
			maxlength="63"
			placeholder="name, e.g. edge-01"
		/>
		<button type="submit">Add</button>
	</form>
{/if}

<style>
	.issued {
		margin-bottom: 1rem;
	}
	pre {
		white-space: pre-wrap;
		word-break: break-all;
		background: var(--code);
		padding: 0.6rem;
		border-radius: 4px;
	}
	.actions {
		white-space: nowrap;
	}
	.row {
		display: flex;
		gap: 0.6rem;
	}
</style>
