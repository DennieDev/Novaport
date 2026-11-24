<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	let { value, tone }: { value: string; tone?: 'ok' | 'warn' | 'bad' | 'muted' } = $props();

	const good = ['succeeded', 'success', 'running', 'ready', 'active', 'complete', 'completed', 'reachable', 'updated', 'healthy'];
	const bad = ['failed', 'denied', 'rejected', 'down', 'unreachable', 'orphaned', 'paused', 'rollback_paused', 'exited', 'dead', 'unhealthy', 'down'];
	const muted = ['cancelled', 'shutdown', 'drain', 'inactive', 'remove', 'created', 'stopped'];

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
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		padding: 0.08rem 0.5rem;
		border-radius: 999px;
		font-size: 0.78rem;
		font-weight: 500;
		line-height: 1.5;
		white-space: nowrap;
		color: var(--tone);
		background: color-mix(in srgb, var(--tone) 12%, transparent);
	}
	.badge::before {
		content: '';
		width: 6px;
		height: 6px;
		border-radius: 50%;
		background: currentColor;
	}
	.ok {
		--tone: var(--ok);
	}
	.warn {
		--tone: var(--warn);
	}
	.bad {
		--tone: var(--bad);
	}
	.muted {
		--tone: var(--muted);
	}
</style>
