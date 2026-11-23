// SPDX-License-Identifier: AGPL-3.0-or-later
import { goto } from '$app/navigation';

export class ApiError extends Error {
	constructor(
		readonly status: number,
		message: string,
		readonly body: unknown = undefined
	) {
		super(message);
	}
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
	const response = await fetch(`/api${path}`, {
		method,
		headers: {
			Accept: 'application/json',
			...(body === undefined ? {} : { 'Content-Type': 'application/json' })
		},
		body: body === undefined ? undefined : JSON.stringify(body)
	});
	if (response.status === 401 && !path.startsWith('/auth/')) {
		await goto(`/login?next=${encodeURIComponent(location.pathname)}`);
	}
	if (!response.ok) {
		let detail = response.statusText;
		let data: unknown;
		try {
			data = await response.json();
			const message = (data as { detail?: unknown }).detail;
			if (typeof message === 'string') detail = message;
		} catch {
			// Not JSON; keep the status text.
		}
		throw new ApiError(response.status, detail, data);
	}
	if (response.status === 204) return undefined as T;
	return (await response.json()) as T;
}

export const api = {
	get: <T>(path: string) => request<T>('GET', path),
	post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
	patch: <T>(path: string, body: unknown) => request<T>('PATCH', path, body),
	delete: (path: string) => request<void>('DELETE', path)
};

export function errorMessage(e: unknown): string {
	return e instanceof Error ? e.message : String(e);
}

export function websocketUrl(path: string): string {
	const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
	return `${scheme}://${location.host}/api${path}`;
}
