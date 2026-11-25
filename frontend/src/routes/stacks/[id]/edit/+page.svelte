<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { beforeNavigate, goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api, ApiError, errorMessage } from '#lib/api.ts';
	import Crumbs from '#lib/components/Crumbs.svelte';
	import Badge from '#lib/components/Badge.svelte';
	import CodeEditor from '#lib/components/CodeEditor.svelte';
	import DeployDialog from '#lib/components/DeployDialog.svelte';
	import Icon from '#lib/components/Icon.svelte';
	import MergeEditor from '#lib/components/MergeEditor.svelte';
	import { can } from '#lib/session.svelte.ts';
	import type {
		ConflictBody,
		DeployOptions,
		FileConflict,
		SaveResult,
		Stack,
		StackFiles
	} from '#lib/types.ts';

	const id = $derived(page.params.id ?? '');
	const editable = $derived(can('editor', id));

	let stack = $state<Stack | undefined>();
	const canDeploy = $derived(can('operator', id) && stack?.edit_mode !== 'pr');
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
	// Set when saving should also deploy; kept while a conflict or validation error is resolved.
	let deployOptions = $state<DeployOptions | undefined>();
	let askingDeploy = $state(false);

	const paths = $derived(
		Object.keys({ ...original, ...current }).sort((a, b) =>
			a.startsWith('compose') === b.startsWith('compose') ? a.localeCompare(b) : a.startsWith('compose') ? -1 : 1
		)
	);
	const changed = $derived(paths.filter((p) => original[p] !== current[p]));

	function fileState(path: string): 'new' | 'deleted' | 'modified' | undefined {
		if (original[path] === current[path]) return undefined;
		if (current[path] === null) return 'deleted';
		return original[path] === undefined ? 'new' : 'modified';
	}

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
			const result = await api.post<SaveResult>(`/stacks/${id}/files`, {
				base_commit: baseCommit,
				changes,
				message: message.trim() || null,
				validate_compose: validate,
				deploy: deployOptions ?? null
			});
			saved = result;
			conflict = undefined;
			askingDeploy = false;
			if (result.deployment_id) await goto(`/stacks/${id}`);
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
			askingDeploy = false;
		} finally {
			busy = false;
		}
	}

	function saveAndDeploy(options: DeployOptions) {
		deployOptions = options;
		return saveChanges();
	}

	function saveOnly() {
		deployOptions = undefined;
		return saveChanges();
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

<Crumbs
	items={[
		{ href: '/stacks', label: 'Stacks' },
		{ href: `/stacks/${id}`, label: stack?.name ?? 'Stack' }
	]}
/>

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
		<button onclick={() => saveResolved()} disabled={busy}>
			{deployOptions ? 'Save resolved version and deploy' : 'Save resolved version'}
		</button>
		<button onclick={load} disabled={busy}>Discard my changes and reload</button>
		{#if error}<span class="error">{error}</span>{/if}
	</p>
	{#if invalid}
		<pre class="panel error">{invalid}</pre>
		<button onclick={() => saveResolved(false)} disabled={busy}>Commit anyway</button>
	{/if}
{:else if stack}
	<DeployDialog
		bind:open={askingDeploy}
		title="Save and deploy {stack.name}"
		confirmLabel="Save and deploy"
		busy={busy}
		onconfirm={saveAndDeploy}
	>
		<p>
			Commits {changed.length} change{changed.length === 1 ? '' : 's'} to
			<code>{stack.repository.name}</code>, then deploys the new revision.
		</p>
	</DeployDialog>

	<div class="page-header">
		<div>
			<h1>{editable ? 'Edit' : 'Files of'} {stack.name}</h1>
			<p class="subtitle">
				{#if !editable}
					Read only: editing needs the editor role on this stack.
				{:else if stack.edit_mode === 'pr'}
					Saving opens a pull request; nothing is deployed until it's merged.
				{:else}
					Saving commits to the branch. Without deploying, the stack follows its deploy mode
					({stack.deploy_mode}).
				{/if}
			</p>
		</div>
		<div class="toolbar">
			<a class="button" href="/stacks/{id}/secrets"><Icon name="secrets" size={14} />Secrets</a>
		</div>
	</div>

	<div class="layout">
		<aside class="panel files">
			<div class="files-head">
				<span class="label">Files</span>
				{#if editable}
					<button class="icon" onclick={addFile} title="Add a file" aria-label="Add a file">
						<Icon name="plus" size={15} />
					</button>
				{/if}
			</div>
			<ul>
				{#each paths as path (path)}
					{@const status = fileState(path)}
					<li class:active={selected === path} class:deleted={status === 'deleted'}>
						<button
							class="file"
							onclick={() => (selected = path)}
							disabled={status === 'deleted' || binary.includes(path)}
							title={binary.includes(path) ? 'Binary file' : path}
						>
							<Icon name="file" size={14} />
							<span class="path">{path}</span>
						</button>
						{#if status}
							<Badge
								value={status}
								tone={status === 'deleted' ? 'bad' : status === 'new' ? 'ok' : 'warn'}
							/>
						{/if}
						{#if !editable}
							<!-- read only -->
						{:else if status === 'deleted'}
							<button class="icon" onclick={() => restoreFile(path)} title="Restore {path}">
								<Icon name="undo" size={14} />
							</button>
						{:else}
							<button class="icon hover" onclick={() => removeFile(path)} title="Delete {path}">
								<Icon name="trash" size={14} />
							</button>
						{/if}
					</li>
				{/each}
			</ul>
		</aside>
		<section class="panel editor">
			{#if selected && current[selected] !== null && current[selected] !== undefined}
				<div class="editor-head"><code>{selected}</code></div>
				<div class="editor-wrap">
					<CodeEditor
						bind:value={current[selected] as string}
						path={selected}
						readOnly={!editable}
					/>
				</div>
			{:else}
				<p class="muted empty">Select a file.</p>
			{/if}
		</section>
	</div>

	{#if editable}
		<div class="panel save">
			<input bind:value={message} placeholder="Commit message (optional)" maxlength="5000" />
			<button onclick={saveOnly} disabled={busy || changed.length === 0}>
				{busy && !deployOptions
					? 'Saving…'
					: `Save${changed.length ? ` ${changed.length} change${changed.length === 1 ? '' : 's'}` : ''}`}
			</button>
			{#if canDeploy}
				<button
					class="primary"
					onclick={() => (askingDeploy = true)}
					disabled={busy || changed.length === 0}
				>
					<Icon name="deploy" size={14} />Save and deploy
				</button>
			{/if}
		</div>
	{/if}
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
		grid-template-columns: 260px 1fr;
		gap: 1rem;
		align-items: start;
	}
	.files {
		padding: 0.5rem;
	}
	.files-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 0.2rem 0.4rem 0.5rem;
	}
	.label {
		font-size: 0.75rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--muted);
	}
	.files ul {
		list-style: none;
		padding: 0;
		margin: 0;
	}
	.files li {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		padding: 0.1rem 0.25rem;
		border-radius: 6px;
	}
	.files li:hover {
		background: var(--panel-alt);
	}
	.files li.active {
		background: var(--accent-soft);
	}
	.file {
		display: flex;
		align-items: center;
		gap: 0.45rem;
		flex: 1;
		min-width: 0;
		padding: 0.35rem 0.25rem;
		border: none;
		background: none;
		color: var(--fg);
		text-align: left;
		font: inherit;
		cursor: pointer;
	}
	.file:disabled {
		cursor: default;
	}
	.files li.active .file {
		color: var(--accent);
		font-weight: 500;
	}
	.files li.deleted .path {
		text-decoration: line-through;
		color: var(--muted);
	}
	.path {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.icon {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 26px;
		height: 26px;
		padding: 0;
		border: none;
		background: none;
		color: var(--muted);
		border-radius: 6px;
		cursor: pointer;
	}
	.icon:hover {
		color: var(--fg);
		background: var(--panel-alt);
	}
	.icon.hover {
		visibility: hidden;
	}
	.files li:hover .icon.hover,
	.files li.active .icon.hover,
	.icon.hover:focus-visible {
		visibility: visible;
	}
	.editor {
		padding: 0;
		overflow: hidden;
	}
	.editor-head {
		padding: 0.5rem 0.9rem;
		border-bottom: 1px solid var(--border);
		background: var(--panel-alt);
	}
	.editor-head code {
		background: none;
		padding: 0;
	}
	.editor-wrap {
		height: 60vh;
	}
	.empty {
		padding: 1rem;
		margin: 0;
	}
	.save {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		margin-top: 1rem;
		padding: 0.75rem;
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
