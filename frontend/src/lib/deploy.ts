// SPDX-License-Identifier: AGPL-3.0-or-later
import type { Deployment, DeploymentStatus } from './types';

export const STAGES = ['resolve', 'validate', 'preflight', 'apply', 'converge'];

const ACTIVE: DeploymentStatus[] = ['pending', 'awaiting_approval', 'running'];

export function isActive(status: DeploymentStatus): boolean {
	return ACTIVE.includes(status);
}

export function stageState(d: Pick<Deployment, 'stage' | 'status'>, stage: string): string {
	const current = d.stage ? STAGES.indexOf(d.stage) : -1;
	const index = STAGES.indexOf(stage);
	if (d.status === 'succeeded') return 'done';
	if (index < current) return 'done';
	if (index === current) return d.status === 'failed' ? 'failed' : 'active';
	return 'todo';
}
