// SPDX-License-Identifier: AGPL-3.0-or-later
export type Role = 'viewer' | 'operator' | 'editor' | 'admin';

export interface Me {
	id: string;
	username: string;
	name: string | null;
	email: string | null;
	local: boolean;
	global_role: Role | null;
	stack_roles: Record<string, Role>;
	token: string | null;
}

export interface ApiToken {
	id: string;
	name: string;
	prefix: string;
	role: Role;
	username: string;
	created_at: string;
	expires_at: string | null;
	last_used_at: string | null;
	last_used_ip: string | null;
}

export interface ApiTokenCreated extends ApiToken {
	token: string;
}

export interface ProviderLink {
	slug: string;
	display_name: string;
	login_url: string;
}

export interface GroupRef {
	id: string;
	name: string;
	synced: boolean;
}

export interface User {
	id: string;
	username: string;
	name: string | null;
	email: string | null;
	local: boolean;
	issuer: string | null;
	disabled: boolean;
	created_at: string;
	last_login_at: string | null;
	groups: GroupRef[];
}

export interface Group {
	id: string;
	name: string;
	description: string;
	created_at: string;
	members: { user_id: string; username: string; synced: boolean }[];
}

export interface GroupMapping {
	claim_value: string;
	group_id: string;
	group_name?: string;
}

export interface OidcProvider {
	id: string;
	slug: string;
	display_name: string;
	issuer: string;
	client_id: string;
	has_client_secret: boolean;
	scopes: string;
	username_claim: string;
	groups_claim: string;
	enabled: boolean;
	redirect_uri: string;
	mappings: GroupMapping[];
}

export interface ProviderCheck {
	ok: boolean;
	error: string | null;
	authorization_endpoint: string | null;
	token_endpoint: string | null;
	userinfo_endpoint: string | null;
}

export interface RoleBinding {
	id: string;
	user_id: string | null;
	group_id: string | null;
	subject: string;
	role: Role;
	stack_id: string | null;
	stack_name: string | null;
	created_at: string;
}

export interface AuditEntry {
	id: number;
	at: string;
	user_id: string | null;
	username: string | null;
	action: string;
	target: string | null;
	stack_id: string | null;
	ip: string | null;
	outcome: 'success' | 'failed' | 'denied';
	details: Record<string, unknown>;
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

export type DeployMode = 'auto' | 'approve' | 'manual';
export type DeploymentStatus =
	| 'pending'
	| 'awaiting_approval'
	| 'running'
	| 'succeeded'
	| 'failed'
	| 'cancelled';

export interface Repository {
	id: string;
	name: string;
	url: string;
	branch: string;
	stacks_root: string;
	auth: 'none' | 'ssh' | 'token';
	ssh_public_key: string | null;
	has_token: boolean;
	webhook_path: string;
	webhook_secret: string;
	poll_interval: number;
	default_deploy_mode: DeployMode;
	default_edit_mode: 'commit' | 'pr';
	forge: 'none' | 'github' | 'gitlab' | 'gitea';
	forge_api_url: string | null;
	has_forge_token: boolean;
	created_at: string;
	last_synced_at: string | null;
	last_commit: string | null;
	last_error: string | null;
	stack_count: number;
}

export interface SyncResult {
	commit: string | null;
	new_revisions: number;
	queued_deployments: number;
	skipped: boolean;
	error: string | null;
}

export interface Revision {
	id: string;
	commit: string;
	tree: string;
	message: string;
	author: string;
	committed_at: string;
	created_at: string;
}

export interface Deployment {
	id: string;
	stack_id: string;
	stack_name: string;
	revision: Revision;
	status: DeploymentStatus;
	stage: string | null;
	trigger: string;
	error: string | null;
	created_at: string;
	started_at: string | null;
	finished_at: string | null;
}

export interface DeploymentDetail extends Deployment {
	events: { id: number; at: string; stage: string; level: string; message: string }[];
}

export interface Stack {
	id: string;
	name: string;
	path: string;
	repository: { id: string; name: string };
	type: 'swarm' | 'compose';
	endpoint: string | null;
	deploy_mode: DeployMode;
	edit_mode: 'commit' | 'pr';
	config_error: string | null;
	removed_at: string | null;
	latest_revision: Revision | null;
	last_good_revision: Revision | null;
	last_deployment: Deployment | null;
	drift: string[] | null;
	drift_checked_at: string | null;
}

export interface AgentStatus {
	enabled: boolean;
	agents: {
		url: string;
		node_id: string | null;
		hostname: string | null;
		version: string | null;
		error: string | null;
	}[];
}

interface Usage {
	count: number;
	size: number;
}

export interface DiskUsage {
	filesystem: { total: number; used: number; free: number } | null;
	images: Usage;
	containers: Usage;
	volumes: Usage;
	build_cache: Usage;
}

export interface NodeImage {
	id: string;
	tags: string[];
	size: number;
	created: number;
	containers: number;
}

export interface NodeVolume {
	name: string;
	driver: string;
	mountpoint: string;
	size: number | null;
	containers: number | null;
	labels: Record<string, string>;
}

export interface VolumeFiles {
	path: string;
	entries: { name: string; kind: string; size: number; modified: number }[];
	truncated: boolean;
}

export interface StackFile {
	path: string;
	content: string | null;
	binary: boolean;
	size: number;
}

export interface StackFiles {
	commit: string;
	files: StackFile[];
	has_secrets: boolean;
}

export interface FileConflict {
	path: string;
	base: string | null;
	theirs: string | null;
	yours: string | null;
}

export interface ConflictBody {
	detail: string;
	tip: string;
	conflicts: FileConflict[];
}

export interface SaveResult {
	commit: string;
	branch: string;
	pull_request_url: string | null;
	new_revisions: number;
	queued_deployments: number;
}

export interface SecretsData {
	commit: string;
	keys: string[];
	values: Record<string, string> | null;
}

export interface SopsInfo {
	available: boolean;
	recipient: string | null;
	example: string | null;
}

export interface RegistryCredential {
	id: string;
	host: string;
	username: string;
	created_at: string;
}

export interface ImageCheck {
	stack_id: string;
	stack_name: string;
	service: string;
	current_ref: string | null;
	candidate_ref: string | null;
	status: 'current' | 'available' | 'error' | 'disabled';
	policy: { mode?: string; allow?: string; match?: string | null; action?: string };
	error: string | null;
	checked_at: string;
	action_taken: 'committed' | 'pr_opened' | 'notified' | 'recorded' | 'failed' | null;
	action_ref: string | null;
}

export interface NotificationSubscription {
	event: string;
	stack_id: string | null;
	stack_name?: string | null;
}

export interface NotificationChannel {
	id: string;
	name: string;
	kind: 'webhook' | 'slack' | 'email';
	enabled: boolean;
	has_url: boolean;
	has_signing_secret: boolean;
	recipients: string | null;
	subscriptions: NotificationSubscription[];
	last_sent_at: string | null;
	last_error: string | null;
}

export interface Endpoint {
	id: string;
	name: string;
	connected: boolean;
	hostname: string | null;
	docker_version: string | null;
	agent_version: string | null;
	swarm: boolean;
	connected_at: string | null;
	disconnected_at: string | null;
	stacks: string[];
}

export interface EndpointKey {
	endpoint: Endpoint;
	key: string;
	docker_command: string;
	swarm_command: string;
}

export interface UnmanagedStack {
	name: string;
	type: 'swarm' | 'compose';
	endpoint: string | null;
	services: number;
	name_taken: boolean;
}

export interface GeneratedCompose {
	compose: string;
	notes: string[];
	secrets: string[];
	own: boolean;
}

export interface Mount {
	type: string;
	source: string | null;
	target: string;
	read_only: boolean;
}

export interface ContainerRow {
	ref: string;
	name: string;
	container_id: string | null;
	node: string | null;
	node_id: string | null;
	state: string;
	desired_state: string | null;
	status: string | null;
	health: string | null;
	error: string | null;
	image: string;
	updated_at: string | null;
	current: boolean;
}

export interface ServiceRow {
	name: string;
	full_name: string;
	service_id: string | null;
	image: string;
	mode: string;
	replicas_desired: number | null;
	replicas_running: number | null;
	ports: Port[];
	update_status: UpdateStatus | null;
	containers: ContainerRow[];
}

export interface StackHealth {
	state: 'healthy' | 'degraded' | 'down' | 'stopped';
	problems: string[];
}

export interface StackContainers {
	type: 'swarm' | 'compose';
	endpoint: string | null;
	agents: boolean;
	health: StackHealth;
	services: ServiceRow[];
}

export interface ContainerInfo {
	ref: string;
	name: string;
	service: string;
	container_id: string | null;
	node: string | null;
	state: string;
	status: string | null;
	health: string | null;
	health_log: string | null;
	error: string | null;
	exit_code: number | null;
	image: string;
	command: string[];
	env: string[];
	labels: Record<string, string>;
	mounts: Mount[];
	ports: Port[];
	networks: { name: string; addresses: string[] }[];
	restart_count: number | null;
	created_at: string | null;
	started_at: string | null;
	finished_at: string | null;
}
