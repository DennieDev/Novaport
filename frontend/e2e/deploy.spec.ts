// SPDX-License-Identifier: AGPL-3.0-or-later
import { expect, test, type Page } from '@playwright/test';

const password = process.env.NOVAPORT_E2E_PASSWORD ?? '';
const repoUrl = process.env.NOVAPORT_E2E_REPO ?? '';
const stack = process.env.NOVAPORT_E2E_STACK ?? 'web';

test.describe.configure({ mode: 'serial' });

let page: Page;

test.beforeAll(async ({ browser }) => {
	page = await browser.newPage();
	page.on('dialog', (dialog) => dialog.accept());
});

test.afterAll(async () => {
	await page.close();
});

test('logs in', async () => {
	await page.goto('/stacks');
	await expect(page).toHaveURL(/\/login/);
	await page.getByLabel('Username').fill('admin');
	await page.getByLabel('Password').fill(password);
	await page.getByRole('button', { name: 'Log in' }).click();
	await expect(page.getByRole('link', { name: 'Repositories' })).toBeVisible();
});

test('adds a repository and syncs its stacks', async () => {
	await page.getByRole('link', { name: 'Repositories' }).click();
	await page.getByRole('button', { name: 'Add repository' }).click();
	await page.getByLabel('Name').fill('infra');
	await page.getByLabel('URL').fill(repoUrl);
	await page.getByLabel('Poll every').fill('0');
	await page.getByRole('button', { name: 'Add', exact: true }).click();
	await expect(page.getByRole('heading', { name: 'infra' })).toBeVisible();
	await page.getByRole('button', { name: 'Sync now' }).click();
	await expect(page.getByRole('link', { name: '1', exact: true })).toBeVisible();
	await page.getByRole('link', { name: 'Stacks', exact: true }).first().click();
	await expect(page.getByRole('link', { name: stack, exact: true })).toBeVisible();
});

test('deploys a stack and follows it to the end', async () => {
	await page.getByRole('link', { name: stack, exact: true }).click();
	await expect(page.getByRole('heading', { name: stack })).toBeVisible();
	await page.getByRole('button', { name: /^Deploy/ }).click();

	// The stack page stays open and shows the deployment above the services.
	const progress = page.locator('.progress');
	await expect(progress).toContainText('succeeded', { timeout: 150_000 });
	await expect(progress).toContainText('Deployed.');
	await expect(page).toHaveURL(/\/stacks\//);

	await progress.getByRole('link', { name: 'Full log' }).click();
	await expect(page.getByRole('heading', { name: `Deploy ${stack}` })).toContainText('succeeded');
	await expect(page.getByText('All services are running.')).toBeVisible();
});

test('shows the containers of the stack with their logs', async () => {
	await page.getByRole('link', { name: `← ${stack}` }).click();
	const rows = page.locator('table.tasks tbody tr');
	await expect(rows).toHaveCount(2);
	await expect(rows.first()).toContainText('running');
	await rows.first().getByRole('link', { name: 'Logs' }).click();
	await expect(page).toHaveURL(/\/containers\/.+\?tab=logs/);
	await expect(page.locator('nav.tabs a.active')).toHaveText('Logs');
	await expect(page.locator('pre')).toBeVisible();
	await page.locator('nav.tabs').getByRole('link', { name: 'Info' }).click();
	await expect(page.getByRole('heading', { name: 'Mounts' })).toBeVisible();
});

test('shows changes made elsewhere without waiting for a poll', async () => {
	await page.goto('/repositories');
	await expect(page.getByRole('link', { name: 'infra' })).toBeVisible();
	// The repository list polls every 30 seconds; the live stream should beat that easily.
	await page.waitForTimeout(1000);
	const repos = await (await page.request.get('/api/repositories')).json();
	const response = await page.request.patch(`/api/repositories/${repos[0].id}`, {
		data: { name: 'infra-renamed' }
	});
	expect(response.ok()).toBe(true);
	await expect(page.getByRole('link', { name: 'infra-renamed' })).toBeVisible({ timeout: 5000 });
});
