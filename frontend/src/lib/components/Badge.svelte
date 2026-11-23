<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	let { value, tone }: { value: string; tone?: 'ok' | 'warn' | 'bad' | 'muted' } = $props();

	const good = ['running', 'ready', 'active', 'complete', 'completed', 'reachable', 'updated'];
	const bad = ['failed', 'rejected', 'down', 'unreachable', 'orphaned', 'paused', 'rollback_paused'];
	const muted = ['shutdown', 'drain', 'inactive', 'remove'];

	const resolved = $derived(
		tone ??
			(good.includes(value)
				? 'ok'
				: bad.includes(value)
					? 'bad'
					: muted.includes(value)
						? 'muted'
						: 'warn')
	);
</script>

<span class="badge {resolved}">{value}</span>

<style>
	.badge {
		display: inline-block;
		padding: 0.05rem 0.45rem;
		border-radius: 999px;
		font-size: 0.8rem;
		white-space: nowrap;
		border: 1px solid currentColor;
	}
	.ok {
		color: var(--ok);
	}
	.warn {
		color: var(--warn);
	}
	.bad {
		color: var(--bad);
	}
	.muted {
		color: var(--muted);
	}
</style>
