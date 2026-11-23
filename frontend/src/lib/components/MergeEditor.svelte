<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { MergeView } from '@codemirror/merge';
	import { EditorView } from '@codemirror/view';
	import { onDestroy, onMount } from 'svelte';
	import { extensionsFor } from '../editor';

	// Side by side: `left` is read-only, `right` can be edited unless `readOnly` is set.
	let {
		left,
		right = $bindable(),
		path,
		readOnly = false
	}: { left: string; right: string; path: string; readOnly?: boolean } = $props();

	let host: HTMLDivElement;
	let view: MergeView | undefined;

	onMount(() => {
		view = new MergeView({
			parent: host,
			a: { doc: left, extensions: extensionsFor(path, true) },
			b: {
				doc: right,
				extensions: [
					...extensionsFor(path, readOnly),
					EditorView.updateListener.of((update) => {
						if (update.docChanged) right = update.state.doc.toString();
					})
				]
			},
			collapseUnchanged: { margin: 3, minSize: 6 }
		});
	});
	onDestroy(() => view?.destroy());
</script>

<div class="merge" bind:this={host}></div>

<style>
	.merge {
		border: 1px solid var(--border);
		border-radius: 6px;
		overflow: auto;
		max-height: 60vh;
	}
</style>
