// SPDX-License-Identifier: AGPL-3.0-or-later
import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { svelteTesting } from '@testing-library/svelte/vite';
import { defineConfig } from 'vitest/config';

const backend = process.env.NOVAPORT_BACKEND ?? 'http://localhost:8000';

export default defineConfig({
	plugins: [
		sveltekit({
			compilerOptions: {
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},
			// Single-page app: the server returns index.html for every non-API route.
			adapter: adapter({ fallback: 'index.html' })
		}),
		...(process.env.VITEST ? [svelteTesting()] : [])
	],
	test: {
		environment: 'jsdom',
		include: ['src/**/*.test.ts'],
		restoreMocks: true
	},
	server: {
		proxy: {
			'/api': { target: backend, changeOrigin: true, ws: true }
		}
	}
});
