<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<script lang="ts" module>
	// 24x24 line icons, drawn with a 1.8 stroke.
	const paths = {
		overview: 'M4 13h6V4H4zM14 20h6v-9h-6zM4 20h6v-4H4zM14 4v4h6V4z',
		stacks: 'M12 3 3 8l9 5 9-5zM3 13l9 5 9-5M3 17.5l9 5 9-5',
		services: 'M4 7.5 12 3l8 4.5v9L12 21l-8-4.5zM4 7.5l8 4.5 8-4.5M12 12v9',
		nodes: 'M4 4h16v6H4zM4 14h16v6H4zM8 7h.01M8 17h.01',
		networks: 'M12 4v5M6 15v-3h12v3M4 15h4v5H4zM16 15h4v5h-4zM10 4h4v5h-4z',
		secrets: 'M14.5 9.5a4 4 0 1 0-1.1 2.8L20 19v2h-3v-2h-2v-2h-2l-1.6-1.6M8.5 9.5h.01',
		configs: 'M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h7',
		endpoints: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM3 12h18M12 3c2.5 3 2.5 15 0 18M12 3c-2.5 3-2.5 15 0 18',
		repositories: 'M6 3v12M18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 9a9 9 0 0 1-9 9',
		registries: 'M3 7l9-4 9 4v10l-9 4-9-4zM3 7l9 4 9-4M12 11v10',
		notifications: 'M6 16V11a6 6 0 1 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0',
		access: 'M16 19v-1a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v1M9 10a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM22 19v-1a4 4 0 0 0-3-3.9M16 4.1a3 3 0 0 1 0 5.8',
		audit: 'M9 5h11M9 12h11M9 19h11M4 5h.01M4 12h.01M4 19h.01',
		logs: 'M4 5h16M4 10h16M4 15h10M4 20h7',
		console: 'M4 4h16v16H4zM8 9l3 3-3 3M13 15h3',
		info: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v6M12 7.5h.01',
		restart: 'M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7',
		deploy: 'M12 3v12M7 10l5 5 5-5M5 21h14',
		edit: 'M4 20h4L19 9l-4-4L4 16zM14 6l4 4',
		logout: 'M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10',
		menu: 'M4 6h16M4 12h16M4 18h16',
		chevron: 'M9 6l6 6-6 6',
		file: 'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5',
		trash: 'M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3',
		plus: 'M12 5v14M5 12h14',
		undo: 'M9 14 4 9l5-5M4 9h11a5 5 0 0 1 0 10h-3'
	} as const;

	export type IconName = keyof typeof paths;
</script>

<script lang="ts">
	let { name, size = 16 }: { name: IconName; size?: number } = $props();
</script>

<svg
	width={size}
	height={size}
	viewBox="0 0 24 24"
	fill="none"
	stroke="currentColor"
	stroke-width="1.8"
	stroke-linecap="round"
	stroke-linejoin="round"
	aria-hidden="true"
>
	<path d={paths[name]} />
</svg>

<style>
	svg {
		flex: none;
	}
</style>
