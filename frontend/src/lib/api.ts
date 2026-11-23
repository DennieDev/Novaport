// SPDX-License-Identifier: AGPL-3.0-or-later
export class ApiError extends Error {
	constructor(
		readonly status: number,
		message: string
	) {
		super(message);
	}
}

export async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
	const response = await fetch(`/api${path}`, {
		...init,
		headers: { Accept: 'application/json', ...init?.headers }
	});
	if (!response.ok) {
		throw new ApiError(response.status, `${response.status} ${response.statusText}`);
	}
	return (await response.json()) as T;
}

export interface Health {
	status: string;
	version: string;
}
