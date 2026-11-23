// SPDX-License-Identifier: AGPL-3.0-or-later
import { api, ApiError } from './api';
import type { Me } from './types';

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
