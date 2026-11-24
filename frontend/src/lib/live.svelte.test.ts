// SPDX-License-Identifier: AGPL-3.0-or-later
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FakeSocket } from './testing/socket';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

type Live = typeof import('./live.svelte');

async function load(): Promise<Live> {
	vi.resetModules();
	return import('./live.svelte');
}

beforeEach(() => {
	vi.useFakeTimers();
	FakeSocket.all = [];
	vi.stubGlobal('WebSocket', FakeSocket);
});

afterEach(() => {
	vi.useRealTimers();
	vi.unstubAllGlobals();
});

describe('subscribe', () => {
	it('shares one socket between subscribers', async () => {
		const live = await load();
		const a = vi.fn();
		const b = vi.fn();
		live.subscribe(a);
		live.subscribe(b);
		expect(FakeSocket.all).toHaveLength(1);
		expect(FakeSocket.last.url).toMatch(/\/api\/events$/);

		FakeSocket.last.open();
		expect(live.live.connected).toBe(true);
		FakeSocket.last.send({ topic: 'stacks', id: 's1', stack: 's1' });
		expect(a).toHaveBeenCalledWith({ topic: 'stacks', id: 's1', stack: 's1' });
		expect(b).toHaveBeenCalledTimes(1);
	});

	it('ignores messages that are not JSON', async () => {
		const live = await load();
		const listener = vi.fn();
		live.subscribe(listener);
		FakeSocket.last.onmessage?.({ data: 'nope' });
		expect(listener).not.toHaveBeenCalled();
	});

	it('reconnects with backoff and asks for a resync', async () => {
		const live = await load();
		const listener = vi.fn();
		live.subscribe(listener);
		FakeSocket.last.open();
		FakeSocket.last.drop();
		expect(live.live.connected).toBe(false);

		vi.advanceTimersByTime(999);
		expect(FakeSocket.all).toHaveLength(1);
		vi.advanceTimersByTime(1);
		expect(FakeSocket.all).toHaveLength(2);

		FakeSocket.last.drop();
		vi.advanceTimersByTime(1999);
		expect(FakeSocket.all).toHaveLength(2);
		vi.advanceTimersByTime(1);
		expect(FakeSocket.all).toHaveLength(3);

		FakeSocket.last.open();
		expect(listener).toHaveBeenCalledWith(live.RESYNC);
	});

	it('does not resync on the first connection', async () => {
		const live = await load();
		const listener = vi.fn();
		live.subscribe(listener);
		FakeSocket.last.open();
		expect(listener).not.toHaveBeenCalled();
	});

	it('closes the socket a while after the last subscriber leaves', async () => {
		const live = await load();
		const off = live.subscribe(vi.fn());
		const socket = FakeSocket.last;
		socket.open();
		off();
		vi.advanceTimersByTime(4000);
		expect(socket.closed).toBe(false);

		// A page that subscribes during navigation keeps the same socket.
		const offAgain = live.subscribe(vi.fn());
		vi.advanceTimersByTime(10_000);
		expect(socket.closed).toBe(false);
		expect(FakeSocket.all).toHaveLength(1);

		offAgain();
		vi.advanceTimersByTime(5000);
		expect(socket.closed).toBe(true);
		expect(live.live.connected).toBe(false);
		vi.advanceTimersByTime(60_000);
		expect(FakeSocket.all).toHaveLength(1);

		const listener = vi.fn();
		live.subscribe(listener);
		FakeSocket.last.open();
		expect(listener).not.toHaveBeenCalled();
	});
});

describe('matchers', () => {
	it('match one stack or row, and bulk changes', async () => {
		const { onRow, onStack } = await load();
		const stack = onStack(() => 's1');
		expect(stack({ topic: 'deployments', stack: 's1' })).toBe(true);
		expect(stack({ topic: 'deployments', stack: 's2' })).toBe(false);
		expect(stack({ topic: 'deployments' })).toBe(true);

		const row = onRow(() => 'd1');
		expect(row({ topic: 'deployments', id: 'd1' })).toBe(true);
		expect(row({ topic: 'deployments', id: 'd2' })).toBe(false);
		expect(row({ topic: 'deployments' })).toBe(true);
	});
});
