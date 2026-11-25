// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen } from '@testing-library/svelte';
import { expect, it } from 'vitest';
import type { Deployment } from '../types';
import DeploymentRow from './DeploymentRow.svelte';

function deployment(overrides: Partial<Deployment> = {}): Deployment {
	return {
		id: 'd1',
		stack_id: 's1',
		stack_name: 'shop',
		revision: {
			id: 'r1',
			commit: '0123456789abcdef0123456789abcdef01234567',
			message: 'Bump api'
		} as Deployment['revision'],
		status: 'succeeded',
		stage: 'converge',
		trigger: 'git',
		error: null,
		created_at: new Date().toISOString(),
		started_at: null,
		finished_at: null,
		prune: true,
		pull_images: true,
		...overrides
	};
}

function row(d: Deployment, showStack = false) {
	const table = document.createElement('table');
	const body = table.appendChild(document.createElement('tbody'));
	document.body.appendChild(table);
	return render(DeploymentRow, { target: body, props: { d, showStack } });
}

it('shows the commit and links to the deployment', () => {
	row(deployment());
	expect(screen.getByText('0123456789')).toBeTruthy();
	expect(screen.getByText('Bump api')).toBeTruthy();
	expect(screen.getByText('succeeded').closest('a')?.getAttribute('href')).toBe('/deployments/d1');
	expect(screen.queryByText('shop')).toBeNull();
});

it('shows the stack when asked', () => {
	row(deployment(), true);
	expect(screen.getByText('shop').getAttribute('href')).toBe('/stacks/s1');
});

it('shows only the first line of an error, and only for failures', () => {
	row(deployment({ status: 'failed', error: 'image not found\nlong details' }));
	expect(screen.getByText('image not found')).toBeTruthy();
	expect(screen.queryByText(/long details/)).toBeNull();
});

it('replaces underscores in the status', () => {
	row(deployment({ status: 'awaiting_approval' }));
	expect(screen.getByText('awaiting approval')).toBeTruthy();
});
