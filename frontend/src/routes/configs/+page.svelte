<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import Status from '#lib/components/Status.svelte';
	import SwarmObjectTable from '#lib/components/SwarmObjectTable.svelte';
	import { poll } from '#lib/poll.svelte.ts';
	import type { SwarmObject } from '#lib/types.ts';

	const items = poll<SwarmObject[]>(() => '/configs', 30_000);
</script>

<h1>Configs</h1>
<p class="muted">
	Swarm configs in the cluster. Stacks create theirs from the compose file; values stay in git.
</p>
<Status loading={items.loading && !items.data} error={items.error} />
{#if items.data}
	<SwarmObjectTable items={items.data} empty="No Swarm configs." />
{/if}
