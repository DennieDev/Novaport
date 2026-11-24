// SPDX-License-Identifier: AGPL-3.0-or-later
import { websocketUrl } from './api';

/** A row the server says changed. Bulk changes leave out `id` and `stack`. */
export interface Change {
	topic: string;
	id?: string;
	stack?: string;
}

type Listener = (change: Change) => void;

/** Sent after a reconnect: anything may have changed in the meantime. */
export const RESYNC: Change = { topic: '*' };

const RETRY_DELAYS = [1000, 2000, 5000, 10_000, 30_000];
// Navigating unmounts one page before the next subscribes; keep the socket across that gap.
const IDLE_CLOSE_MS = 5000;

export const live = $state({ connected: false });

const listeners = new Set<Listener>();
let socket: WebSocket | undefined;
let retryTimer: ReturnType<typeof setTimeout> | undefined;
let idleTimer: ReturnType<typeof setTimeout> | undefined;
let attempt = 0;
let openedBefore = false;

function emit(change: Change) {
	for (const listener of listeners) listener(change);
}

function connect() {
	retryTimer = undefined;
	const ws = new WebSocket(websocketUrl('/events'));
	socket = ws;
	ws.onopen = () => {
		attempt = 0;
		live.connected = true;
		if (openedBefore) emit(RESYNC);
		openedBefore = true;
	};
	ws.onmessage = (e) => {
		try {
			emit(JSON.parse(e.data) as Change);
		} catch {
			// Not ours; ignore.
		}
	};
	ws.onclose = () => {
		if (socket !== ws) return;
		socket = undefined;
		live.connected = false;
		if (listeners.size > 0) {
			retryTimer = setTimeout(connect, RETRY_DELAYS[Math.min(attempt, RETRY_DELAYS.length - 1)]);
			attempt += 1;
		}
	};
}

function disconnect() {
	idleTimer = undefined;
	clearTimeout(retryTimer);
	retryTimer = undefined;
	const ws = socket;
	socket = undefined;
	live.connected = false;
	ws?.close();
}

/** Calls `listener` for every change the server announces. Returns the unsubscribe function. */
export function subscribe(listener: Listener): () => void {
	listeners.add(listener);
	clearTimeout(idleTimer);
	idleTimer = undefined;
	if (socket === undefined && retryTimer === undefined) connect();
	return () => {
		listeners.delete(listener);
		if (listeners.size === 0) idleTimer = setTimeout(disconnect, IDLE_CLOSE_MS);
	};
}

/** Matches changes to one stack, and bulk changes that don't say which stack. */
export function onStack(id: () => string): (change: Change) => boolean {
	return (c) => c.stack === undefined || c.stack === id();
}

/** Matches changes to one row, and bulk changes that don't say which row. */
export function onRow(id: () => string): (change: Change) => boolean {
	return (c) => c.id === undefined || c.id === id();
}

/** Everything a stack summary is built from. */
export const STACK_TOPICS = ['stacks', 'revisions', 'deployments'];
