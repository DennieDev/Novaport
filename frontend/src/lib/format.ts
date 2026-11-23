// SPDX-License-Identifier: AGPL-3.0-or-later
export function bytes(value: number | null | undefined): string {
	if (value == null) return '–';
	const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
	let n = value;
	let i = 0;
	while (n >= 1024 && i < units.length - 1) {
		n /= 1024;
		i++;
	}
	return `${n.toFixed(n < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}

const relative = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });

export function ago(iso: string | null | undefined): string {
	if (!iso) return '–';
	const seconds = (new Date(iso).getTime() - Date.now()) / 1000;
	const steps: [Intl.RelativeTimeFormatUnit, number][] = [
		['year', 31_536_000],
		['month', 2_592_000],
		['day', 86_400],
		['hour', 3600],
		['minute', 60]
	];
	for (const [unit, size] of steps) {
		if (Math.abs(seconds) >= size) return relative.format(Math.round(seconds / size), unit);
	}
	return relative.format(Math.round(seconds), 'second');
}

export function shortId(id: string | null | undefined): string {
	return id ? id.slice(0, 12) : '–';
}

export function replicas(s: { replicas_running: number | null; replicas_desired: number | null }) {
	if (s.replicas_desired == null) return s.replicas_running == null ? '–' : `${s.replicas_running}`;
	return `${s.replicas_running ?? 0}/${s.replicas_desired}`;
}

export function degraded(s: {
	replicas_running: number | null;
	replicas_desired: number | null;
	update_status: { state: string | null } | null;
}): boolean {
	const short =
		s.replicas_desired != null && (s.replicas_running ?? 0) < s.replicas_desired;
	const stuck = ['paused', 'rollback_paused'].includes(s.update_status?.state ?? '');
	return short || stuck;
}
