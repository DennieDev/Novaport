<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { beforeNavigate } from '$app/navigation';
	import { page } from '$app/state';
	import { api, ApiError, errorMessage } from '#lib/api.ts';
	import CodeEditor from '#lib/components/CodeEditor.svelte';
	import MergeEditor from '#lib/components/MergeEditor.svelte';
	import { can } from '#lib/session.svelte.ts';
	import type {
		ConflictBody,
		FileConflict,
		SaveResult,
		Stack,
		StackFiles
	} from '#lib/types.ts';

	const id = $derived(page.params.id ?? '');
	const editable = $derived(can('editor', id));

	let stack = $state<Stack | undefined>();
	let base = $state('');
	// Content as it is in git (null = file doesn't exist there) and as edited (null = deleted).
	let original = $state<Record<string, string | null>>({});
	let current = $state<Record<string, string | null>>({});
	let binary = $state<string[]>([]);
	let selected = $state<string | undefined>();
	let message = $state('');
	let error = $state<string | undefined>();
	let invalid = $state<string | undefined>();
	let saved = $state<SaveResult | undefined>();
	let busy = $state(false);
	let conflict = $state<{ tip: string; files: (FileConflict & { resolved: string })[] }>();

	const paths = $derived(
		Object.keys({ ...original, ...current }).sort((a, b) =>
			a.startsWith('compose') === b.startsWith('compose') ? a.localeCompare(b) : a.startsWith('compose') ? -1 : 1
		)
	);
	const changed = $derived(paths.filter((p) => original[p] !== current[p]));

	async function load() {
		error = undefined;
		conflict = undefined;
		try {
			const [s, f] = await Promise.all([
				api.get<Stack>(`/stacks/${id}`),
				api.get<StackFiles>(`/stacks/${id}/files`)
			]);
			stack = s;
			base = f.commit;
			original = Object.fromEntries(f.files.map((x) => [x.path, x.content]));
			current = { ...original };
			binary = f.files.filter((x) => x.binary).map((x) => x.path);
			selected = f.files.find((x) => !x.binary)?.path;
		} catch (e) {
			error = errorMessage(e);
		}
	}

	$effect(() => {
		id;
		load();
	});

	beforeNavigate((navigation) => {
		if (changed.length && !saved && !confirm('Discard unsaved changes?')) navigation.cancel();
	});

	function addFile() {
		const path = prompt('File path inside the stack folder, e.g. config/app.conf');
		if (!path) return;
		if (path in current && current[path] !== null) {
			selected = path;
			return;
		}
		current[path] = '';
		selected = path;
	}

	function removeFile(path: string) {
		if (!confirm(`Delete ${path}?`)) return;
		if (original[path] === undefined) delete current[path];
		else current[path] = null;
		if (selected === path) selected = paths.find((p) => current[p] !== null);
	}

	function restoreFile(path: string) {
		current[path] = original[path];
	}

	async function save(changes: { path: string; content: string | null }[], baseCommit: string, validate = true) {
		busy = true;
		error = undefined;
		invalid = undefined;
		try {
			saved = await api.post<SaveResult>(`/stacks/${id}/files`, {
				base_commit: baseCommit,
				changes,
				message: message.trim() || null,
				validate_compose: validate
			});
			conflict = undefined;
		} catch (e) {
			if (e instanceof ApiError && e.status === 409) {
				const body = e.body as ConflictBody;
				conflict = {
					tip: body.tip,
					files: body.conflicts.map((c) => ({ ...c, resolved: c.yours ?? '' }))
				};
			} else if (e instanceof ApiError && e.status === 422 && e.message.includes("doesn't validate")) {
				invalid = e.message;
			} else {
				error = errorMessage(e);
			}
		} finally {
			busy = false;
		}
	}

	function saveChanges(validate = true) {
		return save(
			changed.map((p) => ({ path: p, content: current[p] ?? null })),
			base,
			validate
		);
	}

	function saveResolved(validate = true) {
		if (!conflict) return;
		const resolved = new Map(conflict.files.map((f) => [f.path, f]));
		const changes = changed.map((p) => {
			const file = resolved.get(p);
			if (!file) return { path: p, content: current[p] ?? null };
			return { path: p, content: file.yours === null ? null : file.resolved };
		});
		return save(changes, conflict.tip, validate);
	}
</script>

<p><a href="/stacks/{id}">← {stack?.name ?? 'Stack'}</a></p>

{#if saved}
	<h1>Saved</h1>
	<div class="panel">
		{#if saved.pull_request_url}
			<p>
				Pushed branch <code>{saved.branch}</code> and opened a pull request:
				<a href={saved.pull_request_url} target="_blank" rel="noreferrer">{saved.pull_request_url}</a>
			</p>
			<p class="muted">Nothing is deployed until it's merged.</p>
		{:else}
			<p>Committed <code>{saved.commit.slice(0, 10)}</code> to <code>{saved.branch}</code>.</p>
			<p class="muted">
				{saved.new_revisions} new revision(s), {saved.queued_deployments} deployment(s) queued.
			</p>
		{/if}
		<p><a href="/stacks/{id}">Back to the stack</a></p>
	</div>
{:else if conflict}
	<h1>Someone else changed this stack</h1>
	<p class="muted">
		Git moved to <code>{conflict.tip.slice(0, 10)}</code> while you were editing. For each file,
		review what changed in git, then edit your version on the right until it's what you want.
	</p>
	{#each conflict.files as file (file.path)}
		<h2><code>{file.path}</code></h2>
		{#if file.base !== file.theirs}
			<p class="muted">Changed in git (before → now)</p>
			<MergeEditor left={file.base ?? ''} right={file.theirs ?? ''} path={file.path} readOnly />
		{/if}
		{#if file.yours === null}
			<p>You deleted this file; saving keeps it deleted.</p>
		{:else}
			<p class="muted">Git now → your version (editable)</p>
			<MergeEditor left={file.theirs ?? ''} bind:right={file.resolved} path={file.path} />
		{/if}
	{/each}
	<p class="actions">
		<button onclick={() => saveResolved()} disabled={busy}>Save resolved version</button>
		<button onclick={load} disabled={busy}>Discard my changes and reload</button>
		{#if error}<span class="error">{error}</span>{/if}
	</p>
	{#if invalid}
		<pre class="panel error">{invalid}</pre>
		<button onclick={() => saveResolved(false)} disabled={busy}>Commit anyway</button>
	{/if}
{:else if stack}
	<h1>{editable ? 'Edit' : 'Files of'} {stack.name}</h1>
	{#if !editable}
		<p class="muted">Read only: editing needs the editor role on this stack.</p>
	{:else if stack.edit_mode === 'pr'}
		<p class="muted">Changes go to a new branch with a pull request.</p>
	{:else}
		<p class="muted">Changes are committed to the branch and then follow the stack's deploy mode ({stack.deploy_mode}).</p>
	{/if}

	<div class="layout">
		<aside>
			<ul>
				{#each paths as path (path)}
					<li class:active={selected === path} class:deleted={current[path] === null}>
						<button class="link" onclick={() => (selected = path)} disabled={current[path] === null || binary.includes(path)}>
							{path}{original[path] !== current[path] ? ' •' : ''}
						</button>
						{#if !editable}
							<!-- read only -->
						{:else if current[path] === null}
							<button class="link small" onclick={() => restoreFile(path)}>restore</button>
						{:else}
							<button class="link small" onclick={() => removeFile(path)}>delete</button>
						{/if}
					</li>
				{/each}
			</ul>
			{#if editable}<button onclick={addFile}>Add file</button>{/if}
			<p><a href="/stacks/{id}/secrets">Secrets →</a></p>
		</aside>
		<section>
			{#if selected && current[selected] !== null && current[selected] !== undefined}
				<div class="editor-wrap">
					<CodeEditor
						bind:value={current[selected] as string}
						path={selected}
						readOnly={!editable}
					/>
				</div>
			{:else}
				<p class="muted">Select a file.</p>
			{/if}
		</section>
	</div>

	<div class="save" hidden={!editable}>
		<input bind:value={message} placeholder="Commit message (optional)" maxlength="5000" />
		<button onclick={() => saveChanges()} disabled={busy || changed.length === 0}>
			{busy ? 'Saving…' : `Save ${changed.length || ''} change${changed.length === 1 ? '' : 's'}`}
		</button>
	</div>
	{#if invalid}
		<pre class="panel error">{invalid}</pre>
		<button onclick={() => saveChanges(false)} disabled={busy}>Commit anyway</button>
	{/if}
	{#if error}<p class="error">{error}</p>{/if}
{:else if error}
	<p class="error">{error}</p>
{:else}
	<p class="muted">Loading…</p>
{/if}

<style>
	.layout {
		display: grid;
		grid-template-columns: 240px 1fr;
		gap: 1rem;
		margin-top: 1rem;
	}
	aside ul {
		list-style: none;
		padding: 0;
		margin: 0 0 0.75rem;
	}
	aside li {
		display: flex;
		justify-content: space-between;
		gap: 0.5rem;
		padding: 0.2rem 0.4rem;
		border-radius: 4px;
	}
	aside li.active {
		background: var(--code);
	}
	aside li.deleted button:first-child {
		text-decoration: line-through;
	}
	.small {
		font-size: 0.8rem;
		color: var(--muted);
	}
	.editor-wrap {
		height: 60vh;
	}
	.save {
		display: flex;
		gap: 0.75rem;
		margin-top: 1rem;
	}
	.save input {
		flex: 1;
	}
	.actions {
		display: flex;
		gap: 0.75rem;
		align-items: center;
		margin-top: 1.5rem;
	}
	pre.error {
		white-space: pre-wrap;
	}
</style>
