<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { DeployOptions } from '../types';
	import Icon from './Icon.svelte';

	let {
		open = $bindable(false),
		title,
		confirmLabel = 'Deploy',
		busy = false,
		children,
		onconfirm
	}: {
		open?: boolean;
		title: string;
		confirmLabel?: string;
		busy?: boolean;
		children?: Snippet;
		onconfirm: (options: DeployOptions) => void;
	} = $props();

	let dialog = $state<HTMLDialogElement>();
	let prune = $state(true);
	let pullImages = $state(true);

	$effect(() => {
		if (!dialog) return;
		if (open && !dialog.open) dialog.showModal();
		if (!open && dialog.open) dialog.close();
	});

	function confirm(event: SubmitEvent) {
		event.preventDefault();
		onconfirm({ prune, pull_images: pullImages });
	}
</script>

<dialog bind:this={dialog} onclose={() => (open = false)}>
	<form method="dialog" onsubmit={confirm}>
		<h2>{title}</h2>
		{#if children}<div class="body">{@render children()}</div>{/if}

		<label class="option">
			<span class="switch">
				<input type="checkbox" bind:checked={pullImages} />
				<span class="track"><span class="thumb"></span></span>
			</span>
			<span>
				<strong>Pull images</strong>
				<span class="muted">
					{pullImages
						? 'Fetch every image from its registry again, so a moved tag like latest is picked up.'
						: 'Use the images the hosts already have; nothing is fetched.'}
				</span>
			</span>
		</label>
		<label class="option">
			<span class="switch">
				<input type="checkbox" bind:checked={prune} />
				<span class="track"><span class="thumb"></span></span>
			</span>
			<span>
				<strong>Prune services</strong>
				<span class="muted">
					{prune
						? 'Remove services that are no longer in the compose file.'
						: 'Leave services running that were removed from the compose file.'}
				</span>
			</span>
		</label>

		<div class="buttons">
			<button type="button" onclick={() => (open = false)} disabled={busy}>Cancel</button>
			<button class="primary" type="submit" disabled={busy}>
				<Icon name="deploy" size={14} />{confirmLabel}
			</button>
		</div>
	</form>
</dialog>

<style>
	dialog {
		width: min(32rem, calc(100vw - 2rem));
		padding: 1.25rem 1.4rem;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--panel);
		color: var(--fg);
		box-shadow: var(--shadow);
	}
	dialog::backdrop {
		background: rgb(0 0 0 / 0.35);
	}
	h2 {
		margin: 0 0 0.8rem;
	}
	.body {
		margin-bottom: 1rem;
	}
	.option {
		display: flex;
		gap: 0.75rem;
		align-items: flex-start;
		padding: 0.6rem 0;
		border-top: 1px solid var(--border);
		cursor: pointer;
	}
	.option > span:last-child {
		display: grid;
		gap: 0.15rem;
	}
	.switch {
		margin-top: 0.15rem;
	}
	.switch input {
		position: absolute;
		opacity: 0;
		pointer-events: none;
	}
	.track {
		display: inline-block;
		position: relative;
		width: 30px;
		height: 17px;
		border-radius: 999px;
		background: var(--border-strong);
		transition: background 0.15s;
	}
	.thumb {
		position: absolute;
		top: 2px;
		left: 2px;
		width: 13px;
		height: 13px;
		border-radius: 50%;
		background: white;
		transition: transform 0.15s;
	}
	.switch input:checked + .track {
		background: var(--accent);
	}
	.switch input:checked + .track .thumb {
		transform: translateX(13px);
	}
	.switch input:focus-visible + .track {
		outline: 2px solid var(--accent);
		outline-offset: 2px;
	}
	.buttons {
		display: flex;
		justify-content: flex-end;
		gap: 0.6rem;
		margin-top: 1rem;
	}
</style>
