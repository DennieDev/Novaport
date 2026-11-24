// SPDX-License-Identifier: AGPL-3.0-or-later
import { cleanup, render, screen } from '@testing-library/svelte';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import PollProbe from './testing/PollProbe.svelte';
import { FakeSocket } from './testing/socket';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

let calls: string[] = [];

beforeEach(() => {
	vi.useFakeTimers();
	FakeSocket.all = [];
	calls = [];
	vi.stubGlobal('WebSocket', FakeSocket);
	vi.stubGlobal(
		'fetch',
		vi.fn(async (url: string) => {
			calls.push(url);
			return { ok: true, status: 200, json: async () => ({ n: calls.length }) };
		})
	);
});

afterEach(async () => {
	cleanup();
	// Let the shared socket's idle timer close it, so the next test starts fresh.
	await vi.advanceTimersByTimeAsync(10_000);
	vi.useRealTimers();
	vi.unstubAllGlobals();
});

it('loads on mount and polls at the interval', async () => {
	render(PollProbe, { path: '/things', interval: 1000 });
	await vi.advanceTimersByTimeAsync(0);
	expect(calls).toEqual(['/api/things']);
	expect(screen.getByText('1')).toBeTruthy();

	await vi.advanceTimersByTimeAsync(1000);
	expect(calls).toHaveLength(2);
	await vi.advanceTimersByTimeAsync(1000);
	expect(calls).toHaveLength(3);
	expect(FakeSocket.all).toHaveLength(0);
});

it('stops when unmounted', async () => {
	const { unmount } = render(PollProbe, { path: '/things', interval: 1000 });
	await vi.advanceTimersByTimeAsync(0);
	unmount();
	await vi.advanceTimersByTimeAsync(5000);
	expect(calls).toHaveLength(1);
});

it('reloads once for a burst of matching changes', async () => {
	render(PollProbe, {
		path: '/stacks/s1',
		interval: 60_000,
		watch: { topics: ['stacks'], match: (c) => c.stack === 's1' }
	});
	await vi.advanceTimersByTimeAsync(0);
	const socket = FakeSocket.last;
	socket.open();

	socket.send({ topic: 'stacks', id: 's1', stack: 's1' });
	socket.send({ topic: 'stacks', id: 's1', stack: 's1' });
	socket.send({ topic: 'stacks', id: 's1', stack: 's1' });
	await vi.advanceTimersByTimeAsync(300);
	expect(calls).toHaveLength(2);
	expect(screen.getByText('2')).toBeTruthy();
});

it('ignores other topics and stacks, but not a resync', async () => {
	render(PollProbe, {
		path: '/stacks/s1',
		interval: 60_000,
		watch: { topics: ['stacks'], match: (c) => c.stack === 's1' }
	});
	await vi.advanceTimersByTimeAsync(0);
	const socket = FakeSocket.last;
	socket.open();

	socket.send({ topic: 'endpoints', id: 'e1' });
	socket.send({ topic: 'stacks', id: 's2', stack: 's2' });
	await vi.advanceTimersByTimeAsync(300);
	expect(calls).toHaveLength(1);

	socket.send({ topic: '*' });
	await vi.advanceTimersByTimeAsync(300);
	expect(calls).toHaveLength(2);
});

it('polls rarely while live updates are connected', async () => {
	render(PollProbe, { path: '/stacks', interval: 5000, watch: { topics: ['stacks'] } });
	FakeSocket.last.open();
	await vi.advanceTimersByTimeAsync(0);
	// The first timer was set before the socket opened.
	await vi.advanceTimersByTimeAsync(5000);
	expect(calls).toHaveLength(2);

	await vi.advanceTimersByTimeAsync(60_000);
	expect(calls).toHaveLength(2);
	await vi.advanceTimersByTimeAsync(60_000);
	expect(calls).toHaveLength(3);
});
