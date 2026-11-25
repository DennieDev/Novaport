<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { agentsMissing } from '../format';

	let { loading, error }: { loading: boolean; error: string | undefined } = $props();
</script>

{#if agentsMissing(error)}
	<p class="muted">{error}</p>
{:else if error}
	<p class="error">{error}</p>
{:else if loading}
	<p class="muted">Loading…</p>
{/if}
