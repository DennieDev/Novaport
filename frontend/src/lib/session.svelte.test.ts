// SPDX-License-Identifier: AGPL-3.0-or-later
import { beforeEach, expect, it, vi } from 'vitest';
import { can, canSomewhere, session } from './session.svelte';
import type { Me } from './types';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

function user(global_role: Me['global_role'], stack_roles: Me['stack_roles'] = {}): Me {
	return { id: 'u', username: 'u', name: null, email: null, local: true, global_role, stack_roles };
}

beforeEach(() => {
	session.user = null;
});

it('allows nothing without a user', () => {
	expect(can('viewer')).toBe(false);
	expect(canSomewhere('viewer')).toBe(false);
});

it('ranks roles like the server', () => {
	session.user = user('editor');
	expect(can('viewer')).toBe(true);
	expect(can('operator')).toBe(true);
	expect(can('editor')).toBe(true);
	expect(can('admin')).toBe(false);
});

it('adds stack roles on top of the global role', () => {
	session.user = user('viewer', { s1: 'operator' });
	expect(can('operator')).toBe(false);
	expect(can('operator', 's1')).toBe(true);
	expect(can('operator', 's2')).toBe(false);
	expect(canSomewhere('operator')).toBe(true);
	expect(canSomewhere('editor')).toBe(false);
});
