<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	// The mark follows the theme; favicon.svg is a fixed version for browser tabs.
	let { size = 22, name = false }: { size?: number; name?: boolean } = $props();
</script>

<span class="logo">
	<svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
		<rect x="1" y="1" width="62" height="62" rx="14" class="tile" />
		<path class="glyph" d="M22.42 45Q19.54 45 17.39 44.08Q15.24 43.17 14.02 41.54Q12.81 39.92 12.72 37.72H18.44Q18.53 38.91 19.61 39.64Q20.68 40.38 22.42 40.38H24.44Q26.54 40.38 27.6 39.55Q28.65 38.73 28.65 37.31Q28.65 35.98 27.71 35.23Q26.77 34.47 24.8 34.24L21.69 33.79Q17.39 33.19 15.37 31.5Q13.36 29.8 13.36 26.42Q13.36 22.89 15.74 20.95Q18.12 19 22.74 19H24.48Q28.7 19 31.21 20.88Q33.73 22.75 33.91 25.91H28.19Q28.05 24.9 27.07 24.26Q26.09 23.62 24.48 23.62H22.74Q20.78 23.62 19.88 24.33Q18.99 25.04 18.99 26.42Q18.99 27.7 19.77 28.32Q20.55 28.93 22.29 29.21L25.54 29.67Q30.02 30.31 32.15 32.07Q34.28 33.83 34.28 37.26Q34.28 40.93 31.79 42.96Q29.29 45 24.44 45Z" />
		<rect x="37.28" y="39.5" width="14" height="5.5" rx="1.5" class="cursor" />
	</svg>
	{#if name}<span class="name">swarmer<span class="cursor-text" aria-hidden="true">_</span></span>{/if}
</span>

<style>
	.logo {
		display: inline-flex;
		align-items: center;
		gap: 0.55em;
	}
	svg {
		flex: none;
	}
	.tile {
		fill: var(--code);
		stroke: var(--border-strong);
		stroke-width: 2;
	}
	.glyph {
		fill: var(--fg);
	}
	.cursor {
		fill: var(--accent);
	}
	.name {
		font-family: 'JetBrains Mono', ui-monospace, monospace;
		font-weight: 600;
		letter-spacing: -0.02em;
		/* Fonts put the letters at different heights in their line box; trimming it to the
		   capitals centres the name on the mark whatever the font. */
		text-box: trim-both cap alphabetic;
	}
	.cursor-text {
		color: var(--accent);
	}
	/* Blinks like the prompt on dennie.io, unless motion is turned down. */
	@keyframes blink {
		50% {
			opacity: 0;
		}
	}
	@media (prefers-reduced-motion: no-preference) {
		.cursor-text {
			animation: blink 1.2s steps(1) infinite;
		}
	}
</style>
