// SPDX-License-Identifier: AGPL-3.0-or-later
import { api, ApiError } from './api';
import type { Me, Role } from './types';

export const session = $state<{ user: Me | null; checked: boolean }>({ user: null, checked: false });

export async function loadSession(): Promise<Me | null> {
	try {
		session.user = await api.get<Me>('/auth/me');
	} catch (e) {
		if (!(e instanceof ApiError && e.status === 401)) throw e;
		session.user = null;
	}
	session.checked = true;
	return session.user;
}

export async function logIn(username: string, password: string): Promise<void> {
	await api.post('/auth/login', { username, password });
	await loadSession();
}

export async function logOut(): Promise<void> {
	await api.post('/auth/logout');
	session.user = null;
}

const ROLES: Role[] = ['viewer', 'operator', 'editor', 'admin'];

function includes(have: Role | null | undefined, want: Role): boolean {
	return have != null && ROLES.indexOf(have) >= ROLES.indexOf(want);
}

/** Whether the user holds `role` everywhere, or on the given stack. Mirrors the server's check. */
export function can(role: Role, stackId?: string): boolean {
	const user = session.user;
	if (!user) return false;
	return (
		includes(user.global_role, role) ||
		(stackId !== undefined && includes(user.stack_roles[stackId], role))
	);
}

/** Whether the user holds `role` everywhere or on at least one stack. */
export function canSomewhere(role: Role): boolean {
	const user = session.user;
	if (!user) return false;
	return can(role) || Object.values(user.stack_roles).some((r) => includes(r, role));
}
