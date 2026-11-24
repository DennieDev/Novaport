<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { poll, type Watch } from '../poll.svelte';

	let { path, interval, watch }: { path: string; interval: number; watch?: Watch } = $props();

	// svelte-ignore state_referenced_locally
	export const resource = poll<{ n: number }>(() => path, interval, watch);
</script>

<p>{resource.data?.n ?? 'none'}</p>
