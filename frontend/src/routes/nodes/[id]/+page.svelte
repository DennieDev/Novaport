<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { page } from '$app/state';
	import { api, errorMessage } from '#lib/api.ts';
	import Badge from '#lib/components/Badge.svelte';
	import Status from '#lib/components/Status.svelte';
	import { bytes } from '#lib/format.ts';
	import { poll } from '#lib/poll.svelte.ts';
	import type { DiskUsage, Node, NodeImage, NodeVolume, VolumeFiles } from '#lib/types.ts';

	const id = $derived(page.params.id ?? '');
	const nodes = poll<Node[]>(() => '/nodes', 30_000);
	const node = $derived(nodes.data?.find((n) => n.id === id));

	const disk = poll<DiskUsage>(() => `/nodes/${id}/disk`, 60_000);
	const images = poll<NodeImage[]>(() => `/nodes/${id}/images`, 60_000);
	const volumes = poll<NodeVolume[]>(() => `/nodes/${id}/volumes`, 60_000);

	let browsing = $state<{ volume: string; files: VolumeFiles } | undefined>();
	let browseError = $state<string | undefined>();

	async function browse(volume: string, path = '/') {
		browseError = undefined;
		try {
			const files = await api.get<VolumeFiles>(
				`/nodes/${id}/volumes/${encodeURIComponent(volume)}/files?path=${encodeURIComponent(path)}`
			);
			browsing = { volume, files };
		} catch (e) {
			browseError = errorMessage(e);
		}
	}

	function join(base: string, name: string): string {
		return `${base.replace(/\/$/, '')}/${name}`;
	}

	function parent(path: string): string {
		const parts = path.split('/').filter(Boolean);
		parts.pop();
		return `/${parts.join('/')}`;
	}

	const percent = (used: number, total: number) => (total ? Math.round((used / total) * 100) : 0);
</script>

<p><a href="/nodes">← Nodes</a></p>

{#if node}
	<h1>
		{node.hostname}
		<Badge value={node.state} />
		<Badge value={node.availability} />
	</h1>
	<div class="panel">
		<dl class="kv">
			<dt>Role</dt>
			<dd>{node.role}{node.leader ? ' (leader)' : ''}</dd>
			<dt>Address</dt>
			<dd>{node.address ?? '–'}</dd>
			<dt>Engine</dt>
			<dd>{node.engine_version ?? '–'} · {node.os}/{node.architecture}</dd>
			<dt>Resources</dt>
			<dd>{node.cpus ?? '–'} CPUs · {bytes(node.memory_bytes)}</dd>
		</dl>
	</div>
{:else}
	<Status loading={nodes.loading} error={nodes.error} />
{/if}

<h2>Disk</h2>
<Status loading={disk.loading && !disk.data} error={disk.error} />
{#if disk.data}
	{@const d = disk.data}
	<div class="panel">
		<dl class="kv">
			{#if d.filesystem}
				<dt>Host filesystem</dt>
				<dd>
					{bytes(d.filesystem.used)} used of {bytes(d.filesystem.total)}
					({percent(d.filesystem.used, d.filesystem.total)}%) · {bytes(d.filesystem.free)} free
				</dd>
			{/if}
			<dt>Images</dt>
			<dd>{d.images.count} · {bytes(d.images.size)}</dd>
			<dt>Containers</dt>
			<dd>{d.containers.count} · {bytes(d.containers.size)} writable layers</dd>
			<dt>Volumes</dt>
			<dd>{d.volumes.count} · {bytes(d.volumes.size)}</dd>
			<dt>Build cache</dt>
			<dd>{d.build_cache.count} · {bytes(d.build_cache.size)}</dd>
		</dl>
	</div>
{/if}

<h2>Volumes</h2>
<Status loading={volumes.loading && !volumes.data} error={volumes.error} />
{#if volumes.data}
	<table>
		<thead><tr><th>Name</th><th>Driver</th><th>Size</th><th>Used by</th><th></th></tr></thead>
		<tbody>
			{#each volumes.data as v (v.name)}
				<tr>
					<td><code>{v.name}</code></td>
					<td>{v.driver}</td>
					<td>{bytes(v.size)}</td>
					<td>{v.containers ?? '–'}</td>
					<td>
						{#if v.driver === 'local'}
							<button class="link" onclick={() => browse(v.name)}>browse</button>
						{/if}
					</td>
				</tr>
			{:else}
				<tr><td colspan="5" class="muted">No volumes.</td></tr>
			{/each}
		</tbody>
	</table>
{/if}

{#if browseError}<p class="error">{browseError}</p>{/if}
{#if browsing}
	{@const b = browsing}
	<h2>
		<code>{b.volume}:{b.files.path}</code>
		<button class="link" onclick={() => (browsing = undefined)}>close</button>
	</h2>
	<table>
		<tbody>
			{#if b.files.path !== '/'}
				<tr>
					<td colspan="3">
						<button class="link" onclick={() => browse(b.volume, parent(b.files.path))}>..</button>
					</td>
				</tr>
			{/if}
			{#each b.files.entries as e (e.name)}
				<tr>
					<td>
						{#if e.kind === 'directory'}
							<button class="link" onclick={() => browse(b.volume, join(b.files.path, e.name))}>
								{e.name}/
							</button>
						{:else}
							{e.name}
						{/if}
					</td>
					<td class="muted">{e.kind}</td>
					<td>{e.kind === 'file' ? bytes(e.size) : ''}</td>
				</tr>
			{:else}
				<tr><td colspan="3" class="muted">Empty.</td></tr>
			{/each}
		</tbody>
	</table>
	{#if b.files.truncated}<p class="muted">Only the first 1000 entries are shown.</p>{/if}
{/if}

<h2>Images</h2>
<Status loading={images.loading && !images.data} error={images.error} />
{#if images.data}
	<table>
		<thead><tr><th>Tags</th><th>Size</th><th>Containers</th><th>Created</th></tr></thead>
		<tbody>
			{#each images.data as i (i.id)}
				<tr>
					<td>
						{#each i.tags as tag (tag)}<div><code>{tag}</code></div>{:else}
							<span class="muted">untagged {i.id.slice(7, 19)}</span>
						{/each}
					</td>
					<td>{bytes(i.size)}</td>
					<td>{i.containers || ''}</td>
					<td class="muted">{new Date(i.created * 1000).toLocaleDateString()}</td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}

<style>
	h1 :global(.badge) {
		font-size: 0.8rem;
		vertical-align: middle;
		margin-left: 0.4rem;
	}
	h2 .link {
		font-size: 0.85rem;
		font-weight: 400;
		margin-left: 0.75rem;
	}
</style>
