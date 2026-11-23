<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { EditorView } from '@codemirror/view';
	import { onDestroy, onMount } from 'svelte';
	import { extensionsFor } from '../editor';

	let {
		value = $bindable(),
		path,
		readOnly = false
	}: { value: string; path: string; readOnly?: boolean } = $props();

	let host: HTMLDivElement;
	let view: EditorView | undefined;

	let shownPath: string | undefined;

	function create() {
		view?.destroy();
		shownPath = path;
		view = new EditorView({
			parent: host,
			doc: value,
			extensions: [
				...extensionsFor(path, readOnly),
				EditorView.updateListener.of((update) => {
					if (update.docChanged) value = update.state.doc.toString();
				})
			]
		});
	}

	onMount(create);
	onDestroy(() => view?.destroy());

	// Recreate when switching files; sync when the value changes from outside.
	$effect(() => {
		const current = value;
		if (!view) return;
		if (path !== shownPath) {
			shownPath = path;
			create();
		} else if (view.state.doc.toString() !== current) {
			view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: current } });
		}
	});
</script>

<div class="editor" bind:this={host}></div>

<style>
	.editor {
		height: 100%;
		border: 1px solid var(--border);
		border-radius: 6px;
		overflow: hidden;
	}
	.editor :global(.cm-editor) {
		height: 100%;
	}
</style>
