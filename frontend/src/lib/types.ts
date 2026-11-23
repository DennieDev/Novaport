// SPDX-License-Identifier: AGPL-3.0-or-later
export interface Me {
	id: string;
	username: string;
	is_admin: boolean;
}

export interface SystemInfo {
	name: string;
	docker_version: string;
	os: string;
	architecture: string;
	cpus: number;
	memory_bytes: number;
	containers: number;
	containers_running: number;
	images: number;
	swarm: {
		state: string;
		node_id: string | null;
		is_manager: boolean;
		cluster_id: string | null;
		managers: number | null;
		nodes: number | null;
		error: string | null;
	};
}

export interface Node {
	id: string;
	hostname: string;
	role: string;
	availability: string;
	state: string;
	message: string | null;
	address: string | null;
	leader: boolean;
	manager_reachability: string | null;
	engine_version: string | null;
	os: string | null;
	architecture: string | null;
	cpus: number | null;
	memory_bytes: number | null;
	labels: Record<string, string>;
	engine_labels: Record<string, string>;
}

export interface Port {
	published: number | null;
	target: number;
	protocol: string;
	mode: string;
}

export interface UpdateStatus {
	state: string | null;
	message: string | null;
	started_at: string | null;
	completed_at: string | null;
}

export interface ServiceSummary {
	id: string;
	name: string;
	stack: string | null;
	image: string;
	mode: string;
	replicas_desired: number | null;
	replicas_running: number | null;
	ports: Port[];
	update_status: UpdateStatus | null;
	created_at: string;
	updated_at: string;
}

export interface Service extends ServiceSummary {
	labels: Record<string, string>;
	container_labels: Record<string, string>;
	command: string[] | null;
	args: string[] | null;
	env: string[];
	mounts: { type: string; source: string | null; target: string; read_only: boolean }[];
	networks: { id: string; name: string | null; aliases: string[] }[];
	secrets: string[];
	configs: string[];
	constraints: string[];
	tty: boolean;
	restart_condition: string | null;
}

export interface Task {
	id: string;
	service_id: string;
	node_id: string | null;
	node: string | null;
	slot: number | null;
	image: string;
	desired_state: string;
	state: string;
	message: string | null;
	error: string | null;
	exit_code: number | null;
	container_id: string | null;
	updated_at: string;
}

export interface Network {
	id: string;
	name: string;
	stack: string | null;
	driver: string;
	scope: string;
	attachable: boolean;
	internal: boolean;
	ingress: boolean;
	subnets: { subnet: string | null; gateway: string | null }[];
	labels: Record<string, string>;
	created_at: string | null;
}

export interface SwarmObject {
	id: string;
	name: string;
	stack: string | null;
	labels: Record<string, string>;
	created_at: string;
	updated_at: string;
}
