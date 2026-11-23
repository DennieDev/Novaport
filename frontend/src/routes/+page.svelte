<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts">
	import { getJson, type Health } from '#lib/api.ts';

	const health = getJson<Health>('/readyz');
</script>

<main>
	<h1>Novaport</h1>
	{#await health}
		<p>Connecting…</p>
	{:then h}
		<p>Server {h.version}: {h.status}</p>
	{:catch error}
		<p>Server unavailable ({error.message})</p>
	{/await}
</main>
