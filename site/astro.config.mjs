// SPDX-License-Identifier: AGPL-3.0-or-later
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import starlightOpenAPI, { openAPISidebarGroups } from 'starlight-openapi';

export default defineConfig({
	site: 'https://swarmer.dennie.io',
	integrations: [
		starlight({
			title: 'Swarmer',
			description: 'Git-driven management UI for Docker and Docker Swarm stacks.',
			logo: {
				light: './src/assets/logo-light.svg',
				dark: './src/assets/logo-dark.svg',
			},
			favicon: '/favicon.svg',
			social: [
				{ icon: 'github', label: 'GitHub', href: 'https://github.com/DennieDev/swarmer' },
			],
			editLink: {
				baseUrl: 'https://github.com/DennieDev/swarmer/edit/main/site/',
			},
			customCss: ['./src/styles/theme.css'],
			plugins: [
				starlightOpenAPI([
					{
						base: 'api',
						schema: './openapi.json',
						sidebar: { label: 'API reference', operations: { badges: true } },
					},
				]),
			],
			sidebar: [
				{
					label: 'Start here',
					items: ['introduction', 'install', 'first-stack'],
				},
				{
					label: 'Stacks',
					items: [
						'stacks/repository',
						'stacks/settings',
						'stacks/variables-and-secrets',
						'stacks/deploys',
						'stacks/editing',
						'stacks/image-updates',
						'stacks/adopting',
					],
				},
				{
					label: 'Running Swarmer',
					items: [
						'operations/endpoints',
						'operations/access',
						'operations/notifications',
						'operations/webhooks',
						'operations/keys-and-backups',
						'operations/upgrading',
						'operations/self-managed',
					],
				},
				{
					label: 'Reference',
					items: ['reference/configuration', 'reference/cli', 'reference/api', 'architecture'],
				},
				...openAPISidebarGroups,
			],
		}),
	],
});
