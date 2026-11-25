// SPDX-License-Identifier: AGPL-3.0-or-later
import { fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, expect, it } from 'vitest';
import type { ContainerRow, ServiceRow, StackContainers } from '../types';
import StackServices from './StackServices.svelte';

afterEach(() => {
	document.body.innerHTML = '';
});

function task(name: string, state: string, current = true): ContainerRow {
	return {
		ref: name,
		name,
		container_id: null,
		node: 'node1',
		node_id: 'n1',
		state,
		desired_state: current ? 'running' : 'shutdown',
		status: null,
		health: null,
		error: null,
		image: 'app:2',
		updated_at: new Date().toISOString(),
		current
	};
}

function service(name: string, containers: ContainerRow[]): ServiceRow {
	return {
		name,
		full_name: `shop_${name}`,
		service_id: name,
		image: 'app:2',
		mode: 'replicated',
		replicas_desired: 1,
		replicas_running: 1,
		ports: [],
		update_status: null,
		containers
	};
}

function stack(...services: ServiceRow[]): StackContainers {
	return {
		type: 'swarm',
		endpoint: null,
		agents: true,
		health: { state: 'healthy', problems: [] },
		services
	};
}

const healthy = () =>
	stack(service('api', [task('api.1', 'running')]), service('web', [task('web.1', 'running')]));

function show(data: StackContainers, deploying = false) {
	return render(StackServices, {
		props: { stackId: 's1', data, operator: false, deploying, onchange: () => {} }
	});
}

it('keeps healthy services closed outside a deploy', () => {
	show(healthy());
	expect(screen.queryByText('api.1')).toBeNull();
	expect(screen.getByText('Expand all')).toBeTruthy();
});

it('opens every service during a deploy', () => {
	show(healthy(), true);
	expect(screen.getByText('api.1')).toBeTruthy();
	expect(screen.getByText('web.1')).toBeTruthy();
	expect(screen.getByText('Collapse all')).toBeTruthy();
});

it('shows starting tasks next to the ones they replace', () => {
	const data = stack(
		service('api', [task('api.1', 'starting'), task('api.1 old', 'running', false)]),
		service('web', [task('web.1', 'running')])
	);
	show(data);
	expect(screen.getByText('1 starting')).toBeTruthy();
	expect(screen.getByText('api.1')).toBeTruthy();
	expect(screen.getByText('api.1 old')).toBeTruthy();
	expect(screen.queryByText('web.1')).toBeNull();
});

it('expands and collapses all services', async () => {
	show(healthy());
	await fireEvent.click(screen.getByText('Expand all'));
	expect(screen.getByText('api.1')).toBeTruthy();
	expect(screen.getByText('web.1')).toBeTruthy();
	await fireEvent.click(screen.getByText('Collapse all'));
	expect(screen.queryByText('api.1')).toBeNull();
});
